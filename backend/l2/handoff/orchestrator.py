"""L1→L2 Automatic Handoff Orchestrator。"""

from __future__ import annotations

import logging
from typing import Any

from l2.handoff.diagnosis_batch import run_batch_diagnosis
from l2.handoff.matcher import (
    DATASET_NOT_READY,
    EQUIPMENT_CONFIG_REQUIRED,
    MATCHED,
    NO_ACTIVE_MODEL,
    SCHEMA_INCOMPATIBLE,
    match_active_for_ped,
)
from l2.handoff.store import HANDOFF_STORE, HandoffStore
from l2.prepared.l1_source import get_l1_prepared_meta, iter_l1_prepared_rows
from l2.prepared.mapping import resolve_equipment_type
from l2.prepared.publisher import PreparedPublishError, publish_from_l1_prepared

LOG = logging.getLogger("l2.handoff")

_SUCCESS_PREPARE = {"completed", "completed_with_warnings", "partial"}


class L1L2HandoffOrchestrator:
    def __init__(self, store: HandoffStore | None = None) -> None:
        self.store = store or HANDOFF_STORE

    def enqueue_after_prepare(
        self,
        *,
        dataset_id: str,
        pd_version_id: str,
        prepare_run_id: str | None,
        prepare_status: str | None,
    ) -> dict[str, Any] | None:
        """L1 prepare 成功提交后可靠入队（幂等）。"""
        if not pd_version_id or not str(pd_version_id).startswith("PD-"):
            return None
        if prepare_status and prepare_status not in _SUCCESS_PREPARE:
            # 失败 / needs_review：仍记录等待态，便于总览展示原因
            job = self.store.create_job(
                dataset_id=dataset_id,
                pd_version_id=pd_version_id,
                prepare_run_id=prepare_run_id,
                status="dataset_not_ready" if prepare_status == "needs_review" else "waiting_for_l1",
            )
            self.store.update_job(
                job["handoff_id"],
                status=job["status"],
                error_code="L1_NOT_READY",
                error_message=f"L1 prepare 状态为 {prepare_status}，暂不进入 L2。",
            )
            return self.store.get_job(job["handoff_id"])

        existing = self.store.get_job_by_pd(pd_version_id)
        if existing:
            return existing
        job = self.store.create_job(
            dataset_id=dataset_id,
            pd_version_id=pd_version_id,
            prepare_run_id=prepare_run_id,
            status="queued",
        )
        LOG.info("handoff enqueued handoff_id=%s pd=%s", job["handoff_id"], pd_version_id)
        return job

    def resume_dataset(self, dataset_id: str) -> dict[str, Any]:
        """手动补偿：按数据集最新 PD 入队或重试。"""
        from app.services.l1_prepare.service import latest_prepare

        prep = latest_prepare(dataset_id)
        pd_version_id = prep.get("version_id") if prep.get("applied") else None
        if not pd_version_id:
            latest = self.store.get_latest_by_dataset(dataset_id)
            if latest:
                if latest["status"] in {
                    "failed",
                    "queued",
                    "preparing_dataset",
                    "matching_model",
                    "inference_queued",
                }:
                    return self.process_job(latest["handoff_id"])
                return latest
            raise KeyError(f"数据集 {dataset_id} 没有可用的 L1 PD。")

        job = self.enqueue_after_prepare(
            dataset_id=dataset_id,
            pd_version_id=pd_version_id,
            prepare_run_id=prep.get("run_id"),
            prepare_status=prep.get("status") or "completed",
        )
        assert job is not None
        if job["status"] in {
            "queued",
            "failed",
            "preparing_dataset",
            "matching_model",
            "inference_queued",
        }:
            return self.process_job(job["handoff_id"])
        if job["status"] == "completed":
            return job
        # 已有任务但卡在中间态：强制再处理
        if job["status"] not in {
            "training_confirmation_required",
            "model_activation_required",
            "skipped",
            "waiting_for_l1",
            "dataset_not_ready",
            "equipment_config_required",
        }:
            return self.process_job(job["handoff_id"])
        return job

    def process_next(self) -> dict[str, Any] | None:
        jobs = self.store.list_jobs(status="queued", limit=1)
        if not jobs:
            # 恢复中断中的推理排队
            for status in ("inference_queued", "preparing_dataset", "matching_model"):
                jobs = self.store.list_jobs(status=status, limit=1)
                if jobs:
                    break
        if not jobs:
            return None
        return self.process_job(jobs[0]["handoff_id"])

    def process_job(self, handoff_id: str) -> dict[str, Any]:
        job = self.store.get_job(handoff_id)
        if job["status"] in {"completed", "skipped", "training_confirmation_required", "model_activation_required"}:
            # 这些状态需用户动作或已完成；不自动重跑
            if job["status"] == "completed":
                return job
            # training / activation 等待中也可检查训练完成
            if job["status"] == "model_activation_required":
                return job
            if job["status"] == "training_confirmation_required":
                return job
            if job["status"] == "skipped":
                return job

        self.store.update_job(handoff_id, status="preparing_dataset", clear_error=True)
        try:
            equipment_plans = self._discover_equipment(job["pd_version_id"])
            if not equipment_plans:
                self.store.update_job(
                    handoff_id,
                    status="equipment_config_required",
                    error_code=EQUIPMENT_CONFIG_REQUIRED,
                    error_message="未能识别任何可发布的设备分组。",
                )
                return self.store.get_job(handoff_id)

            any_inference = False
            any_train_confirm = False
            any_activation = False
            any_failed = False
            summary_items: list[dict[str, Any]] = []

            for plan in equipment_plans:
                item = self._process_equipment(handoff_id, job, plan)
                summary_items.append(
                    {
                        "item_id": item["item_id"],
                        "equipment_type": item.get("equipment_type"),
                        "status": item["status"],
                        "ped_id": item.get("ped_id"),
                        "diagnosis_run_id": item.get("diagnosis_run_id"),
                        "message": item.get("message"),
                    }
                )
                st = item["status"]
                if st in {"inference_running", "inference_queued", "completed"}:
                    any_inference = True
                if st == "training_confirmation_required":
                    any_train_confirm = True
                if st == "model_activation_required":
                    any_activation = True
                if st in {"failed", "dataset_not_ready", "equipment_config_required"}:
                    any_failed = True

            # 聚合总览状态
            job_status = self._aggregate_status(
                summary_items,
                any_inference=any_inference,
                any_train_confirm=any_train_confirm,
                any_activation=any_activation,
                any_failed=any_failed,
            )
            return self.store.update_job(
                handoff_id,
                status=job_status,
                summary={"equipment": summary_items},
                clear_error=job_status not in {"failed", "dataset_not_ready", "equipment_config_required"},
            )
        except Exception as exc:  # noqa: BLE001
            LOG.exception("handoff failed handoff_id=%s", handoff_id)
            return self.store.update_job(
                handoff_id,
                status="failed",
                error_code="HANDOFF_FAILED",
                error_message=str(exc),
            )

    def confirm_train(self, handoff_id: str, *, item_id: str | None = None) -> dict[str, Any]:
        from l2.management.errors import ManagementError
        from l2.management.service import MANAGEMENT_SERVICE

        job = self.store.get_job(handoff_id)
        targets = [
            e
            for e in job["equipment_items"]
            if e["status"] == "training_confirmation_required"
            and (item_id is None or e["item_id"] == item_id)
        ]
        if not targets:
            raise KeyError("没有等待训练确认的设备项。")
        for item in targets:
            ped_id = item.get("ped_id")
            equipment_type = item.get("equipment_type")
            if not ped_id or not equipment_type:
                self.store.upsert_equipment_item(
                    handoff_id=handoff_id,
                    item_id=item["item_id"],
                    equipment_type=equipment_type,
                    equipment_category=item.get("equipment_category"),
                    status="dataset_not_ready",
                    message="缺少 PED 或设备类型，无法训练。",
                )
                continue
            # 训练就绪检查
            from l2.prepared.catalog import get_published_dataset

            meta = get_published_dataset(ped_id)
            if not meta.get("training_ready"):
                self.store.upsert_equipment_item(
                    handoff_id=handoff_id,
                    item_id=item["item_id"],
                    equipment_type=equipment_type,
                    equipment_category=item.get("equipment_category"),
                    ped_id=ped_id,
                    status="dataset_not_ready",
                    message="数据集缺少有效故障标签或不满足监督训练条件。",
                    details={"training_ready": False},
                )
                continue
            try:
                result = MANAGEMENT_SERVICE.submit_train_job(
                    equipment_type=equipment_type,
                    prepared_dataset_id=ped_id,
                )
            except ManagementError as exc:
                self.store.upsert_equipment_item(
                    handoff_id=handoff_id,
                    item_id=item["item_id"],
                    equipment_type=equipment_type,
                    equipment_category=item.get("equipment_category"),
                    ped_id=ped_id,
                    status="failed",
                    message=exc.message,
                    details={"code": exc.error_code},
                )
                continue
            self.store.upsert_equipment_item(
                handoff_id=handoff_id,
                item_id=item["item_id"],
                equipment_type=equipment_type,
                equipment_category=item.get("equipment_category"),
                ped_id=ped_id,
                status="training_queued",
                training_job_id=result["job_id"],
                message="训练任务已提交。",
                details=result,
            )
        return self._refresh_aggregate(handoff_id)

    def skip_training(self, handoff_id: str, *, item_id: str | None = None) -> dict[str, Any]:
        job = self.store.get_job(handoff_id)
        for item in job["equipment_items"]:
            if item["status"] != "training_confirmation_required":
                continue
            if item_id and item["item_id"] != item_id:
                continue
            self.store.upsert_equipment_item(
                handoff_id=handoff_id,
                item_id=item["item_id"],
                equipment_type=item.get("equipment_type"),
                equipment_category=item.get("equipment_category"),
                ped_id=item.get("ped_id"),
                status="skipped",
                message="用户选择暂不训练。",
            )
        return self._refresh_aggregate(handoff_id)

    def on_model_activated(self, *, equipment_type: str, model_bundle_id: str) -> list[dict[str, Any]]:
        """激活成功后，恢复等待诊断的 handoff 设备项。"""
        resumed: list[dict[str, Any]] = []
        for job in self.store.list_jobs(limit=100):
            changed = False
            for item in job["equipment_items"]:
                if item.get("equipment_type") != equipment_type:
                    continue
                if item["status"] not in {"model_activation_required", "training_confirmation_required", "skipped"}:
                    # training_confirmation 也可能用户想激活已有 candidate
                    if item["status"] != "model_activation_required":
                        continue
                if item["status"] == "skipped":
                    continue
                ped_id = item.get("ped_id")
                if not ped_id:
                    continue
                self.store.upsert_equipment_item(
                    handoff_id=job["handoff_id"],
                    item_id=item["item_id"],
                    equipment_type=equipment_type,
                    equipment_category=item.get("equipment_category"),
                    ped_id=ped_id,
                    status="inference_queued",
                    match_status=MATCHED,
                    model_bundle_id=model_bundle_id,
                    message="模型已激活，排队自动诊断。",
                )
                changed = True
            if changed:
                self.store.update_job(job["handoff_id"], status="inference_queued", clear_error=True)
                resumed.append(self.process_job(job["handoff_id"]))
        return resumed

    def sync_training_progress(self) -> None:
        """轮询训练任务：完成后进入 model_activation_required。"""
        from l2.management.service import MANAGEMENT_SERVICE

        for job in self.store.list_jobs(limit=100):
            for item in job["equipment_items"]:
                if item["status"] not in {"training_queued", "training_running"}:
                    continue
                job_id = item.get("training_job_id")
                if not job_id:
                    continue
                try:
                    train = MANAGEMENT_SERVICE.get_train_job(job_id)
                except Exception:  # noqa: BLE001
                    continue
                st = train.get("status")
                if st in {"queued", "pending"}:
                    self.store.upsert_equipment_item(
                        handoff_id=job["handoff_id"],
                        item_id=item["item_id"],
                        equipment_type=item.get("equipment_type"),
                        equipment_category=item.get("equipment_category"),
                        ped_id=item.get("ped_id"),
                        status="training_queued",
                        training_job_id=job_id,
                        message="训练排队中。",
                    )
                elif st in {"running", "claimed"}:
                    self.store.upsert_equipment_item(
                        handoff_id=job["handoff_id"],
                        item_id=item["item_id"],
                        equipment_type=item.get("equipment_type"),
                        equipment_category=item.get("equipment_category"),
                        ped_id=item.get("ped_id"),
                        status="training_running",
                        training_job_id=job_id,
                        message="训练执行中。",
                    )
                elif st in {"succeeded", "completed", "success"}:
                    bundle_id = train.get("model_bundle_id") or (train.get("result") or {}).get("model_bundle_id")
                    self.store.upsert_equipment_item(
                        handoff_id=job["handoff_id"],
                        item_id=item["item_id"],
                        equipment_type=item.get("equipment_type"),
                        equipment_category=item.get("equipment_category"),
                        ped_id=item.get("ped_id"),
                        status="model_activation_required",
                        training_job_id=job_id,
                        model_bundle_id=bundle_id,
                        message="训练完成，模型等待发布。",
                        details={"train_job": train},
                    )
                    self._refresh_aggregate(job["handoff_id"])
                elif st in {"failed", "error"}:
                    self.store.upsert_equipment_item(
                        handoff_id=job["handoff_id"],
                        item_id=item["item_id"],
                        equipment_type=item.get("equipment_type"),
                        equipment_category=item.get("equipment_category"),
                        ped_id=item.get("ped_id"),
                        status="failed",
                        training_job_id=job_id,
                        message=f"训练失败：{train.get('error_message') or train.get('status')}",
                        details={"train_job": train},
                    )
                    self._refresh_aggregate(job["handoff_id"])

    def recover_pending(self) -> int:
        """服务重启补偿：重新排队可恢复任务。"""
        count = 0
        for job in self.store.list_recoverable_jobs():
            if job["status"] == "inference_running":
                self.store.update_job(job["handoff_id"], status="inference_queued")
            count += 1
        return count

    def workflow_for_dataset(self, dataset_id: str) -> dict[str, Any]:
        job = self.store.get_latest_by_dataset(dataset_id)
        if job is None:
            return {
                "dataset_id": dataset_id,
                "l1": {"status": "unknown"},
                "l2": {"status": "waiting_for_l1", "handoff": None},
            }
        return {
            "dataset_id": dataset_id,
            "l1": {
                "status": "completed",
                "pd_version_id": job["pd_version_id"],
                "prepare_run_id": job["prepare_run_id"],
            },
            "l2": {
                "status": job["status"],
                "handoff": job,
            },
        }

    def _discover_equipment(self, pd_version_id: str) -> list[dict[str, Any]]:
        meta = get_l1_prepared_meta(pd_version_id)
        categories: list[str | None] = []
        seen: set[str] = set()
        for row in iter_l1_prepared_rows(pd_version_id):
            cat = row.get("equipment_category")
            key = str(cat) if cat is not None else ""
            if key in seen:
                continue
            seen.add(key)
            categories.append(None if cat is None or str(cat).strip() == "" else str(cat))
        if not categories:
            categories = [None]

        plans: list[dict[str, Any]] = []
        seen_types: set[str] = set()
        for cat in categories:
            mapping = resolve_equipment_type(
                dataset_id=meta.dataset_id,
                original_filename=meta.original_filename,
                equipment_category=cat,
            )
            et = mapping.equipment_type or f"unmapped:{cat or 'unknown'}"
            if et in seen_types and mapping.mapping_status == "confirmed":
                continue
            seen_types.add(et)
            plans.append(
                {
                    "equipment_type": mapping.equipment_type or None,
                    "equipment_category": cat,
                    "mapping": mapping,
                }
            )
        return plans

    def _process_equipment(
        self,
        handoff_id: str,
        job: dict[str, Any],
        plan: dict[str, Any],
    ) -> dict[str, Any]:
        mapping = plan["mapping"]
        category = plan.get("equipment_category")
        equipment_type = plan.get("equipment_type")

        if mapping.mapping_status != "confirmed" or not equipment_type:
            onboarding = None
            try:
                from l2.equipment.onboarding.service import ONBOARDING_SERVICE

                onboarding = ONBOARDING_SERVICE.prepare(
                    dataset_id=job["dataset_id"],
                    pd_revision_id=job["pd_version_id"],
                    equipment_category=category,
                    equipment_group_id=getattr(mapping, "equipment_group_id", None),
                )
            except Exception as exc:  # noqa: BLE001
                LOG.warning("onboarding prepare failed: %s", exc)
            return self.store.upsert_equipment_item(
                handoff_id=handoff_id,
                equipment_type=equipment_type or (onboarding or {}).get("equipment_type_candidate"),
                equipment_category=category,
                status="equipment_config_required",
                match_status=EQUIPMENT_CONFIG_REQUIRED,
                message="检测到新的设备类别，请确认训练特征和故障标签。",
                details={
                    "mapping": mapping.__dict__,
                    "onboarding_id": (onboarding or {}).get("onboarding_id"),
                    "onboarding": {
                        "feature_count": (onboarding or {}).get("feature_count"),
                        "class_count": (onboarding or {}).get("class_count"),
                        "display_name_candidate": (onboarding or {}).get("display_name_candidate"),
                        "warnings": (onboarding or {}).get("warnings") or [],
                    },
                },
            )

        self.store.update_job(handoff_id, status="preparing_dataset")
        try:
            published = publish_from_l1_prepared(
                job["pd_version_id"],
                equipment_type=equipment_type,
                equipment_category=category,
            )
        except PreparedPublishError as exc:
            status = {
                "EQUIPMENT_CONFIG_REQUIRED": "equipment_config_required",
                "SCHEMA_INCOMPATIBLE": "dataset_not_ready",
                "NOT_TRAINING_READY": "dataset_not_ready",
                "NOT_INFERENCE_READY": "dataset_not_ready",
            }.get(exc.code, "failed")
            return self.store.upsert_equipment_item(
                handoff_id=handoff_id,
                equipment_type=equipment_type,
                equipment_category=category,
                status=status,
                match_status=exc.code,
                message=exc.message,
                details=exc.details,
            )

        ped_id = published.get("prepared_dataset_id")
        ped_rev = published.get("revision_id")
        item = self.store.upsert_equipment_item(
            handoff_id=handoff_id,
            equipment_type=equipment_type,
            equipment_category=category,
            status="matching_model",
            ped_id=ped_id,
            ped_revision_id=ped_rev,
            message="PED 已发布，正在匹配模型。",
            details={"publish": {"reused": published.get("reused", False)}},
        )

        self.store.update_job(handoff_id, status="matching_model")
        match = match_active_for_ped(published)
        match_status = match["match_status"]

        if match_status == MATCHED:
            model_bundle_id = match["model_bundle_id"]
            item = self.store.upsert_equipment_item(
                handoff_id=handoff_id,
                item_id=item["item_id"],
                equipment_type=equipment_type,
                equipment_category=category,
                status="inference_running",
                ped_id=ped_id,
                ped_revision_id=ped_rev,
                match_status=MATCHED,
                model_bundle_id=model_bundle_id,
                message="已匹配 Active，正在批量诊断。",
                details=match,
            )
            self.store.update_job(handoff_id, status="inference_running")
            run = run_batch_diagnosis(
                self.store,
                prepared_dataset_id=str(ped_id),
                model_bundle_id=str(model_bundle_id),
                handoff_item_id=item["item_id"],
            )
            final_eq_status = "completed" if run["status"] in {"completed", "completed_with_errors"} else "failed"
            return self.store.upsert_equipment_item(
                handoff_id=handoff_id,
                item_id=item["item_id"],
                equipment_type=equipment_type,
                equipment_category=category,
                status=final_eq_status,
                ped_id=ped_id,
                ped_revision_id=ped_rev,
                match_status=MATCHED,
                model_bundle_id=model_bundle_id,
                diagnosis_run_id=run["diagnosis_run_id"],
                message=(
                    f"诊断完成 {run['succeeded_records']}/{run['total_records']}"
                    if final_eq_status == "completed"
                    else "批量诊断失败。"
                ),
                details={"match": match, "diagnosis_run": run},
            )

        if match_status in {NO_ACTIVE_MODEL, SCHEMA_INCOMPATIBLE}:
            # 有兼容 Candidate 时提示可启用
            msg = match["message"]
            if match.get("candidates"):
                msg += " 可查看并启用已有模型，或使用本次数据训练。"
            return self.store.upsert_equipment_item(
                handoff_id=handoff_id,
                item_id=item["item_id"],
                equipment_type=equipment_type,
                equipment_category=category,
                status="training_confirmation_required",
                ped_id=ped_id,
                ped_revision_id=ped_rev,
                match_status=match_status,
                message=msg,
                details=match,
            )

        if match_status == EQUIPMENT_CONFIG_REQUIRED:
            return self.store.upsert_equipment_item(
                handoff_id=handoff_id,
                item_id=item["item_id"],
                equipment_type=equipment_type,
                equipment_category=category,
                status="equipment_config_required",
                ped_id=ped_id,
                match_status=match_status,
                message=match["message"],
                details=match,
            )

        return self.store.upsert_equipment_item(
            handoff_id=handoff_id,
            item_id=item["item_id"],
            equipment_type=equipment_type,
            equipment_category=category,
            status="dataset_not_ready",
            ped_id=ped_id,
            match_status=match_status or DATASET_NOT_READY,
            message=match.get("message") or "数据集未就绪。",
            details=match,
        )

    def _aggregate_status(
        self,
        items: list[dict[str, Any]],
        *,
        any_inference: bool,
        any_train_confirm: bool,
        any_activation: bool,
        any_failed: bool,
    ) -> str:
        statuses = [i["status"] for i in items]
        if not statuses:
            return "failed"
        if all(s == "completed" for s in statuses):
            return "completed"
        if all(s == "skipped" for s in statuses):
            return "skipped"
        if any(s == "inference_running" for s in statuses):
            return "inference_running"
        if any_train_confirm and not any(s == "completed" for s in statuses):
            return "training_confirmation_required"
        if any_train_confirm and any(s == "completed" for s in statuses):
            # 部分完成 + 部分待训练
            return "training_confirmation_required"
        if any_activation:
            return "model_activation_required"
        if any(s in {"training_queued", "training_running"} for s in statuses):
            return "training_running" if any(s == "training_running" for s in statuses) else "training_queued"
        if any(s == "equipment_config_required" for s in statuses) and not any_inference:
            return "equipment_config_required"
        if any(s == "dataset_not_ready" for s in statuses) and not any_inference:
            return "dataset_not_ready"
        if any_failed and not any(s == "completed" for s in statuses):
            return "failed"
        if any(s == "completed" for s in statuses):
            return "completed" if all(s in {"completed", "skipped"} for s in statuses) else "training_confirmation_required"
        return statuses[0]

    def _refresh_aggregate(self, handoff_id: str) -> dict[str, Any]:
        job = self.store.get_job(handoff_id)
        items = [
            {
                "item_id": e["item_id"],
                "equipment_type": e.get("equipment_type"),
                "status": e["status"],
                "ped_id": e.get("ped_id"),
                "diagnosis_run_id": e.get("diagnosis_run_id"),
                "message": e.get("message"),
            }
            for e in job["equipment_items"]
        ]
        status = self._aggregate_status(
            items,
            any_inference=any(i["status"] == "completed" for i in items),
            any_train_confirm=any(i["status"] == "training_confirmation_required" for i in items),
            any_activation=any(i["status"] == "model_activation_required" for i in items),
            any_failed=any(i["status"] == "failed" for i in items),
        )
        return self.store.update_job(handoff_id, status=status, summary={"equipment": items})


ORCHESTRATOR = L1L2HandoffOrchestrator()
