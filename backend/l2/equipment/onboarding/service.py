"""Equipment Onboarding Service。"""

from __future__ import annotations

import logging
from typing import Any

from l2.equipment.onboarding.draft import build_draft_from_pd, content_key_for
from l2.equipment.onboarding.register import register_equipment_spec
from l2.equipment.onboarding.store import ONBOARDING_STORE, OnboardingStore
from l2.equipment.onboarding.validate import apply_overrides, validate_draft
from l2.equipment.registry import load_all_specs
from l2.management.errors import ManagementError
from l2.prepared.l1_source import get_l1_prepared_meta
from l2.prepared.publisher import PreparedPublishError, publish_from_l1_prepared

LOG = logging.getLogger("l2.equipment.onboarding")


class EquipmentOnboardingService:
    def __init__(self, store: OnboardingStore | None = None) -> None:
        self.store = store or ONBOARDING_STORE

    def prepare(
        self,
        *,
        dataset_id: str,
        pd_revision_id: str,
        equipment_group_id: str | None = None,
        equipment_category: str | None = None,
    ) -> dict[str, Any]:
        meta = get_l1_prepared_meta(pd_revision_id)
        if meta.dataset_id != dataset_id:
            raise ManagementError(
                "DATASET_MISMATCH",
                "dataset_id 与 PD 修订不匹配。",
                status_code=400,
                details={"expected": meta.dataset_id, "got": dataset_id},
            )
        draft = build_draft_from_pd(
            pd_revision_id=pd_revision_id,
            equipment_category=equipment_category,
            equipment_group_id=equipment_group_id,
        )
        # 已注册且 mapping confirmed：无需 onboarding
        if draft.get("already_registered") and draft.get("mapping_status") == "confirmed":
            key = content_key_for(
                pd_revision_id=pd_revision_id,
                equipment_category=equipment_category or draft.get("equipment_category"),
                equipment_group_id=equipment_group_id,
            )
            record, _ = self.store.create_or_get(
                content_key=key,
                dataset_id=dataset_id,
                pd_revision_id=pd_revision_id,
                equipment_group_id=equipment_group_id,
                equipment_category=equipment_category or draft.get("equipment_category"),
                draft=draft,
                status="registered",
            )
            return self._public(record, extra={"already_registered": True, "needs_confirmation": False})

        key = content_key_for(
            pd_revision_id=pd_revision_id,
            equipment_category=equipment_category or draft.get("equipment_category"),
            equipment_group_id=equipment_group_id,
        )
        status = "needs_confirmation"
        record, created = self.store.create_or_get(
            content_key=key,
            dataset_id=dataset_id,
            pd_revision_id=pd_revision_id,
            equipment_group_id=equipment_group_id,
            equipment_category=equipment_category or draft.get("equipment_category"),
            draft=draft,
            status=status,
        )
        return self._public(
            record,
            extra={
                "created": created,
                "already_registered": False,
                "needs_confirmation": record["status"] == "needs_confirmation",
            },
        )

    def get(self, onboarding_id: str) -> dict[str, Any]:
        try:
            record = self.store.get(onboarding_id)
        except KeyError as exc:
            raise ManagementError(
                "ONBOARDING_NOT_FOUND",
                f"配置草案不存在：{onboarding_id}",
                status_code=404,
            ) from exc
        return self._public(record)

    def validate(self, onboarding_id: str, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        record = self.get(onboarding_id)
        result = validate_draft(record["draft"], user_overrides=overrides)
        return {
            "onboarding_id": onboarding_id,
            "ok": result["ok"],
            "errors": result["errors"],
            "warnings": result["warnings"],
            "training_readiness": result["training_readiness"],
            "schema_conflict": result["schema_conflict"],
            "preview": {
                "equipment_type": result["merged_draft"].get("equipment_type_candidate"),
                "display_name": result["merged_draft"].get("display_name_candidate"),
                "feature_count": result["merged_draft"].get("feature_count"),
                "class_labels": result["merged_draft"].get("class_labels"),
                "normal_label": result["merged_draft"].get("normal_label_candidate"),
            },
        }

    def confirm(
        self,
        onboarding_id: str,
        *,
        overrides: dict[str, Any] | None = None,
        continue_handoff: bool = True,
        handoff_id: str | None = None,
        handoff_item_id: str | None = None,
    ) -> dict[str, Any]:
        record = self.store.get(onboarding_id)
        if record["status"] == "registered" and record.get("registered_equipment_type"):
            # 幂等：已注册则继续尝试 PED / handoff
            return self._after_registered(
                record,
                register_result={
                    "reused": True,
                    "equipment_type": record["registered_equipment_type"],
                    "schema_version": record.get("registered_schema_version"),
                },
                continue_handoff=continue_handoff,
                handoff_id=handoff_id,
                handoff_item_id=handoff_item_id,
            )

        result = validate_draft(record["draft"], user_overrides=overrides)
        if not result["ok"]:
            raise ManagementError(
                "ONBOARDING_VALIDATION_FAILED",
                "设备配置校验未通过。",
                status_code=400,
                details={"errors": result["errors"], "warnings": result["warnings"]},
            )
        merged = result["merged_draft"]
        try:
            registered = register_equipment_spec(merged)
        except Exception as exc:  # noqa: BLE001
            raise ManagementError(
                "EQUIPMENT_REGISTER_FAILED",
                f"EquipmentSpec 注册失败：{exc}",
                status_code=400,
                details={"error": str(exc)},
            ) from exc

        load_all_specs(refresh=True)
        updated = self.store.update(
            onboarding_id,
            draft=merged,
            status="registered",
            registered_equipment_type=registered["equipment_type"],
            registered_schema_version=registered["schema_version"],
            mark_confirmed=True,
        )
        return self._after_registered(
            updated,
            register_result=registered,
            continue_handoff=continue_handoff,
            handoff_id=handoff_id,
            handoff_item_id=handoff_item_id,
        )

    def _after_registered(
        self,
        record: dict[str, Any],
        *,
        register_result: dict[str, Any],
        continue_handoff: bool,
        handoff_id: str | None,
        handoff_item_id: str | None,
    ) -> dict[str, Any]:
        equipment_type = str(register_result.get("equipment_type") or record.get("registered_equipment_type"))
        ped_result = None
        ped_error = None
        try:
            ped_result = publish_from_l1_prepared(
                record["pd_revision_id"],
                equipment_type=equipment_type,
                equipment_category=record.get("equipment_category"),
            )
        except PreparedPublishError as exc:
            ped_error = {"code": exc.code, "message": exc.message, "details": exc.details}
            LOG.warning("PED publish after onboarding failed: %s", exc.message)

        handoff_result = None
        if continue_handoff:
            try:
                from l2.handoff.orchestrator import ORCHESTRATOR

                if handoff_id:
                    # 更新设备项后重新处理整单
                    if handoff_item_id and ped_result:
                        ORCHESTRATOR.store.upsert_equipment_item(
                            handoff_id=handoff_id,
                            item_id=handoff_item_id,
                            equipment_type=equipment_type,
                            equipment_category=record.get("equipment_category"),
                            status="matching_model",
                            ped_id=ped_result.get("prepared_dataset_id"),
                            ped_revision_id=ped_result.get("revision_id"),
                            message="设备配置已确认，继续模型匹配。",
                            details={"onboarding_id": record["onboarding_id"], "register": register_result},
                        )
                    ORCHESTRATOR.store.update_job(handoff_id, status="queued", clear_error=True)
                    handoff_result = ORCHESTRATOR.process_job(handoff_id)
                else:
                    handoff_result = ORCHESTRATOR.resume_dataset(record["dataset_id"])
            except Exception as exc:  # noqa: BLE001
                LOG.exception("handoff resume after onboarding failed")
                handoff_result = {"error": str(exc)}

        body = self._public(record)
        body["register"] = register_result
        body["ped"] = ped_result
        body["ped_error"] = ped_error
        body["handoff"] = handoff_result
        body["next_action"] = self._next_action(ped_result, ped_error, handoff_result, record["draft"])
        return body

    def _next_action(
        self,
        ped: dict[str, Any] | None,
        ped_error: dict[str, Any] | None,
        handoff: dict[str, Any] | None,
        draft: dict[str, Any],
    ) -> dict[str, Any]:
        if ped_error:
            return {
                "action": "fix_ped_publish",
                "message": "设备配置已保存，但 PED 发布失败，请修复后重试。",
            }
        if handoff and handoff.get("status") == "completed":
            return {"action": "view_results", "message": "已完成诊断。"}
        if handoff and handoff.get("status") == "training_confirmation_required":
            return {
                "action": "start_training",
                "message": "设备配置已保存，请确认后开始训练。",
                "prepared_dataset_id": ped.get("prepared_dataset_id") if ped else None,
            }
        readiness = (draft or {}).get("training_readiness") or {}
        if ped and ped.get("training_ready"):
            return {
                "action": "start_training",
                "message": "设备配置已保存，后续同类数据将自动复用。可开始训练。",
                "prepared_dataset_id": ped.get("prepared_dataset_id"),
            }
        if not readiness.get("has_labels"):
            return {
                "action": "blocked",
                "message": "当前数据缺少有效故障标签，无法训练监督分类模型。",
            }
        return {
            "action": "review",
            "message": "设备配置已保存，后续同类数据将自动复用。",
        }

    def _public(self, record: dict[str, Any], *, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        draft = record.get("draft") or {}
        body = {
            "onboarding_id": record["onboarding_id"],
            "configuration_status": record["status"],
            "status": record["status"],
            "dataset_id": record["dataset_id"],
            "pd_revision_id": record["pd_revision_id"],
            "equipment_group_id": record.get("equipment_group_id"),
            "equipment_category": record.get("equipment_category"),
            "equipment_type_candidate": draft.get("equipment_type_candidate"),
            "display_name_candidate": draft.get("display_name_candidate"),
            "schema_version_candidate": draft.get("schema_version_candidate"),
            "feature_candidates": draft.get("feature_candidates") or [],
            "feature_count": draft.get("feature_count"),
            "target_candidate": draft.get("target_candidate"),
            "normal_label_candidate": draft.get("normal_label_candidate"),
            "normal_label_confirmed": draft.get("normal_label_confirmed"),
            "class_labels": draft.get("class_labels") or [],
            "class_counts": draft.get("class_counts") or {},
            "class_count": draft.get("class_count"),
            "record_count": draft.get("record_count"),
            "labeled_record_count": draft.get("labeled_record_count"),
            "warnings": draft.get("warnings") or [],
            "training_readiness": draft.get("training_readiness") or {},
            "draft": draft,
            "registered_equipment_type": record.get("registered_equipment_type"),
            "registered_schema_version": record.get("registered_schema_version"),
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
            "confirmed_at": record.get("confirmed_at"),
        }
        if extra:
            body.update(extra)
        return body


ONBOARDING_SERVICE = EquipmentOnboardingService()
