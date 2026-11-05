"""Handoff / DiagnosisRun 持久化。"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from l2.handoff.database import connect, init_db


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _j(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, separators=(",", ":"))


def _loads(raw: str | None) -> Any:
    if not raw:
        return {}
    return json.loads(raw)


class HandoffStore:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = init_db(db_path)

    def create_job(
        self,
        *,
        dataset_id: str,
        pd_version_id: str,
        prepare_run_id: str | None,
        status: str = "queued",
    ) -> dict[str, Any]:
        connection = connect(self.db_path)
        try:
            existing = connection.execute(
                "SELECT * FROM handoff_jobs WHERE pd_version_id = ?",
                (pd_version_id,),
            ).fetchone()
            if existing:
                return self._job_public(connection, dict(existing))
            handoff_id = f"HO-{uuid.uuid4().hex[:12]}"
            now = _now()
            connection.execute(
                """
                INSERT INTO handoff_jobs(
                    handoff_id, dataset_id, pd_version_id, prepare_run_id,
                    status, created_at, updated_at, summary_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, '{}')
                """,
                (handoff_id, dataset_id, pd_version_id, prepare_run_id, status, now, now),
            )
            connection.commit()
            return self.get_job(handoff_id, connection=connection)
        finally:
            connection.close()

    def get_job(self, handoff_id: str, *, connection=None) -> dict[str, Any]:
        owns = connection is None
        connection = connection or connect(self.db_path)
        try:
            row = connection.execute(
                "SELECT * FROM handoff_jobs WHERE handoff_id = ?",
                (handoff_id,),
            ).fetchone()
            if row is None:
                raise KeyError(handoff_id)
            return self._job_public(connection, dict(row))
        finally:
            if owns:
                connection.close()

    def get_job_by_pd(self, pd_version_id: str) -> dict[str, Any] | None:
        connection = connect(self.db_path)
        try:
            row = connection.execute(
                "SELECT * FROM handoff_jobs WHERE pd_version_id = ?",
                (pd_version_id,),
            ).fetchone()
            if row is None:
                return None
            return self._job_public(connection, dict(row))
        finally:
            connection.close()

    def get_latest_by_dataset(self, dataset_id: str) -> dict[str, Any] | None:
        connection = connect(self.db_path)
        try:
            row = connection.execute(
                """
                SELECT * FROM handoff_jobs
                WHERE dataset_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (dataset_id,),
            ).fetchone()
            if row is None:
                return None
            return self._job_public(connection, dict(row))
        finally:
            connection.close()

    def list_jobs(self, *, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        connection = connect(self.db_path)
        try:
            if status:
                rows = connection.execute(
                    """
                    SELECT * FROM handoff_jobs WHERE status = ?
                    ORDER BY created_at ASC LIMIT ?
                    """,
                    (status, limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM handoff_jobs ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [self._job_public(connection, dict(r)) for r in rows]
        finally:
            connection.close()

    def update_job(
        self,
        handoff_id: str,
        *,
        status: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        summary: dict[str, Any] | None = None,
        clear_error: bool = False,
    ) -> dict[str, Any]:
        connection = connect(self.db_path)
        try:
            row = connection.execute(
                "SELECT * FROM handoff_jobs WHERE handoff_id = ?",
                (handoff_id,),
            ).fetchone()
            if row is None:
                raise KeyError(handoff_id)
            fields: list[str] = ["updated_at = ?"]
            values: list[Any] = [_now()]
            if status is not None:
                fields.append("status = ?")
                values.append(status)
            if clear_error:
                fields.extend(["error_code = NULL", "error_message = NULL"])
            else:
                if error_code is not None:
                    fields.append("error_code = ?")
                    values.append(error_code)
                if error_message is not None:
                    fields.append("error_message = ?")
                    values.append(error_message)
            if summary is not None:
                fields.append("summary_json = ?")
                values.append(_j(summary))
            values.append(handoff_id)
            connection.execute(
                f"UPDATE handoff_jobs SET {', '.join(fields)} WHERE handoff_id = ?",
                values,
            )
            connection.commit()
            return self.get_job(handoff_id, connection=connection)
        finally:
            connection.close()

    def upsert_equipment_item(
        self,
        *,
        handoff_id: str,
        equipment_type: str | None,
        equipment_category: str | None,
        status: str,
        item_id: str | None = None,
        ped_id: str | None = None,
        ped_revision_id: str | None = None,
        match_status: str | None = None,
        model_bundle_id: str | None = None,
        diagnosis_run_id: str | None = None,
        training_job_id: str | None = None,
        message: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        connection = connect(self.db_path)
        try:
            now = _now()
            if item_id:
                existing = connection.execute(
                    "SELECT * FROM handoff_equipment WHERE item_id = ?",
                    (item_id,),
                ).fetchone()
            else:
                existing = connection.execute(
                    """
                    SELECT * FROM handoff_equipment
                    WHERE handoff_id = ? AND COALESCE(equipment_type, '') = ?
                      AND COALESCE(equipment_category, '') = ?
                    """,
                    (handoff_id, equipment_type or "", equipment_category or ""),
                ).fetchone()
            if existing:
                iid = existing["item_id"]
                connection.execute(
                    """
                    UPDATE handoff_equipment SET
                        equipment_type = ?, equipment_category = ?, status = ?,
                        ped_id = COALESCE(?, ped_id),
                        ped_revision_id = COALESCE(?, ped_revision_id),
                        match_status = COALESCE(?, match_status),
                        model_bundle_id = COALESCE(?, model_bundle_id),
                        diagnosis_run_id = COALESCE(?, diagnosis_run_id),
                        training_job_id = COALESCE(?, training_job_id),
                        message = ?,
                        details_json = ?,
                        updated_at = ?
                    WHERE item_id = ?
                    """,
                    (
                        equipment_type,
                        equipment_category,
                        status,
                        ped_id,
                        ped_revision_id,
                        match_status,
                        model_bundle_id,
                        diagnosis_run_id,
                        training_job_id,
                        message,
                        _j(details if details is not None else _loads(existing["details_json"])),
                        now,
                        iid,
                    ),
                )
            else:
                iid = item_id or f"HE-{uuid.uuid4().hex[:12]}"
                connection.execute(
                    """
                    INSERT INTO handoff_equipment(
                        item_id, handoff_id, equipment_type, equipment_category, status,
                        ped_id, ped_revision_id, match_status, model_bundle_id,
                        diagnosis_run_id, training_job_id, message, details_json, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        iid,
                        handoff_id,
                        equipment_type,
                        equipment_category,
                        status,
                        ped_id,
                        ped_revision_id,
                        match_status,
                        model_bundle_id,
                        diagnosis_run_id,
                        training_job_id,
                        message,
                        _j(details or {}),
                        now,
                    ),
                )
            connection.commit()
            row = connection.execute(
                "SELECT * FROM handoff_equipment WHERE item_id = ?",
                (iid,),
            ).fetchone()
            return self._eq_public(dict(row))
        finally:
            connection.close()

    def get_equipment_item(self, item_id: str) -> dict[str, Any]:
        connection = connect(self.db_path)
        try:
            row = connection.execute(
                "SELECT * FROM handoff_equipment WHERE item_id = ?",
                (item_id,),
            ).fetchone()
            if row is None:
                raise KeyError(item_id)
            return self._eq_public(dict(row))
        finally:
            connection.close()

    def list_recoverable_jobs(self) -> list[dict[str, Any]]:
        connection = connect(self.db_path)
        try:
            rows = connection.execute(
                """
                SELECT * FROM handoff_jobs
                WHERE status IN (
                    'queued', 'preparing_dataset', 'matching_model',
                    'inference_queued', 'inference_running', 'training_queued'
                )
                ORDER BY created_at ASC
                """
            ).fetchall()
            return [self._job_public(connection, dict(r)) for r in rows]
        finally:
            connection.close()

    def create_or_get_diagnosis_run(
        self,
        *,
        content_key: str,
        prepared_dataset_id: str,
        dataset_revision_id: str,
        equipment_type: str,
        model_bundle_id: str,
        schema_version: str | None,
        total_records: int,
        handoff_item_id: str | None,
        force_new: bool = False,
    ) -> tuple[dict[str, Any], bool]:
        """返回 (run, created)。force_new 时用新 content_key 后缀。"""
        connection = connect(self.db_path)
        try:
            key = content_key
            if force_new:
                key = f"{content_key}::rerun::{uuid.uuid4().hex[:8]}"
            else:
                existing = connection.execute(
                    "SELECT * FROM diagnosis_runs WHERE content_key = ?",
                    (key,),
                ).fetchone()
                if existing is not None:
                    return self._run_public(dict(existing)), False
            run_id = f"DR-{uuid.uuid4().hex[:12]}"
            now = _now()
            connection.execute(
                """
                INSERT INTO diagnosis_runs(
                    diagnosis_run_id, prepared_dataset_id, dataset_revision_id,
                    equipment_type, model_bundle_id, schema_version, status,
                    total_records, processed_records, succeeded_records, failed_records,
                    counts_json, content_key, started_at, handoff_item_id
                ) VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, 0, 0, 0, '{}', ?, ?, ?)
                """,
                (
                    run_id,
                    prepared_dataset_id,
                    dataset_revision_id,
                    equipment_type,
                    model_bundle_id,
                    schema_version,
                    total_records,
                    key,
                    now,
                    handoff_item_id,
                ),
            )
            connection.commit()
            row = connection.execute(
                "SELECT * FROM diagnosis_runs WHERE diagnosis_run_id = ?",
                (run_id,),
            ).fetchone()
            return self._run_public(dict(row)), True
        finally:
            connection.close()

    def update_diagnosis_run(
        self,
        diagnosis_run_id: str,
        *,
        status: str | None = None,
        processed_records: int | None = None,
        succeeded_records: int | None = None,
        failed_records: int | None = None,
        counts: dict[str, int] | None = None,
        result_dir: str | None = None,
        finished: bool = False,
    ) -> dict[str, Any]:
        connection = connect(self.db_path)
        try:
            fields: list[str] = []
            values: list[Any] = []
            if status is not None:
                fields.append("status = ?")
                values.append(status)
            if processed_records is not None:
                fields.append("processed_records = ?")
                values.append(processed_records)
            if succeeded_records is not None:
                fields.append("succeeded_records = ?")
                values.append(succeeded_records)
            if failed_records is not None:
                fields.append("failed_records = ?")
                values.append(failed_records)
            if counts is not None:
                fields.append("counts_json = ?")
                values.append(_j(counts))
            if result_dir is not None:
                fields.append("result_dir = ?")
                values.append(result_dir)
            if finished:
                fields.append("finished_at = ?")
                values.append(_now())
            if not fields:
                row = connection.execute(
                    "SELECT * FROM diagnosis_runs WHERE diagnosis_run_id = ?",
                    (diagnosis_run_id,),
                ).fetchone()
                return self._run_public(dict(row))
            values.append(diagnosis_run_id)
            connection.execute(
                f"UPDATE diagnosis_runs SET {', '.join(fields)} WHERE diagnosis_run_id = ?",
                values,
            )
            connection.commit()
            row = connection.execute(
                "SELECT * FROM diagnosis_runs WHERE diagnosis_run_id = ?",
                (diagnosis_run_id,),
            ).fetchone()
            return self._run_public(dict(row))
        finally:
            connection.close()

    def get_diagnosis_run(self, diagnosis_run_id: str) -> dict[str, Any]:
        connection = connect(self.db_path)
        try:
            row = connection.execute(
                "SELECT * FROM diagnosis_runs WHERE diagnosis_run_id = ?",
                (diagnosis_run_id,),
            ).fetchone()
            if row is None:
                raise KeyError(diagnosis_run_id)
            return self._run_public(dict(row))
        finally:
            connection.close()

    def save_diagnosis_records(
        self,
        diagnosis_run_id: str,
        records: list[dict[str, Any]],
    ) -> None:
        connection = connect(self.db_path)
        try:
            connection.executemany(
                """
                INSERT INTO diagnosis_records(
                    diagnosis_run_id, record_id, status, screening_result, result_json
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(diagnosis_run_id, record_id) DO UPDATE SET
                    status = excluded.status,
                    screening_result = excluded.screening_result,
                    result_json = excluded.result_json
                """,
                [
                    (
                        diagnosis_run_id,
                        str(r["record_id"]),
                        str(r.get("status") or "ok"),
                        r.get("screening_result"),
                        _j(r.get("result") or r),
                    )
                    for r in records
                ],
            )
            connection.commit()
        finally:
            connection.close()

    def list_diagnosis_records(
        self,
        diagnosis_run_id: str,
        *,
        offset: int = 0,
        limit: int = 50,
        screening_result: str | None = None,
    ) -> dict[str, Any]:
        connection = connect(self.db_path)
        try:
            if screening_result:
                total = connection.execute(
                    """
                    SELECT COUNT(*) AS c FROM diagnosis_records
                    WHERE diagnosis_run_id = ? AND screening_result = ?
                    """,
                    (diagnosis_run_id, screening_result),
                ).fetchone()["c"]
                rows = connection.execute(
                    """
                    SELECT * FROM diagnosis_records
                    WHERE diagnosis_run_id = ? AND screening_result = ?
                    ORDER BY record_id LIMIT ? OFFSET ?
                    """,
                    (diagnosis_run_id, screening_result, limit, offset),
                ).fetchall()
            else:
                total = connection.execute(
                    "SELECT COUNT(*) AS c FROM diagnosis_records WHERE diagnosis_run_id = ?",
                    (diagnosis_run_id,),
                ).fetchone()["c"]
                rows = connection.execute(
                    """
                    SELECT * FROM diagnosis_records
                    WHERE diagnosis_run_id = ?
                    ORDER BY record_id LIMIT ? OFFSET ?
                    """,
                    (diagnosis_run_id, limit, offset),
                ).fetchall()
            items = []
            for row in rows:
                payload = _loads(row["result_json"])
                if isinstance(payload, dict) and "spectral_reference" not in payload:
                    from l2.inference.spectral_reference import attach_spectral_reference

                    payload = attach_spectral_reference(payload)
                items.append(
                    {
                        "record_id": row["record_id"],
                        "status": row["status"],
                        "screening_result": row["screening_result"],
                        "result": payload,
                    }
                )
            return {"total": int(total), "offset": offset, "limit": limit, "items": items}
        finally:
            connection.close()

    def iter_l3_target_records(self, diagnosis_run_id: str) -> list[dict[str, Any]]:
        """按当前分流策略返回全部需进入 L3 的记录（含 result_json，不受前端截断）。"""
        from l2.routing_policy import L3_AUTO_SCREENINGS, ROUTING_POLICY_VERSION

        connection = connect(self.db_path)
        try:
            placeholders = ",".join("?" for _ in L3_AUTO_SCREENINGS)
            rows = connection.execute(
                f"""
                SELECT record_id, status, screening_result, result_json
                FROM diagnosis_records
                WHERE diagnosis_run_id = ? AND UPPER(COALESCE(screening_result, '')) IN ({placeholders})
                ORDER BY record_id
                """,
                (diagnosis_run_id, *sorted(L3_AUTO_SCREENINGS)),
            ).fetchall()
            items = []
            for row in rows:
                payload = _loads(row["result_json"])
                items.append(
                    {
                        "record_id": row["record_id"],
                        "status": row["status"],
                        "screening_result": row["screening_result"],
                        "result": payload if isinstance(payload, dict) else {},
                        "routing_policy_version": ROUTING_POLICY_VERSION,
                    }
                )
            return items
        finally:
            connection.close()

    def get_routing_summary(self, diagnosis_run_id: str) -> dict[str, Any]:
        """按设备型号/分组聚合 L2 筛查与 L3 待复核摘要（只读，不重跑推理）。"""
        run = self.get_diagnosis_run(diagnosis_run_id)
        connection = connect(self.db_path)
        try:
            rows = connection.execute(
                """
                SELECT record_id, screening_result, status
                FROM diagnosis_records
                WHERE diagnosis_run_id = ?
                ORDER BY record_id
                """,
                (diagnosis_run_id,),
            ).fetchall()
        finally:
            connection.close()

        equipment_type = str(run.get("equipment_type") or "")
        display_name = equipment_type
        try:
            from l2.equipment.registry import get_equipment_spec

            display_name = str(get_equipment_spec(equipment_type).display_name or equipment_type)
        except Exception:  # noqa: BLE001 - 展示名缺失时回退类型码
            pass

        record_equipment: dict[str, str | None] = {}
        equipment_group_id: str | None = None
        try:
            from l2.prepared.adapter import PreparedDatasetAdapter

            adapter = PreparedDatasetAdapter(str(run["prepared_dataset_id"]))
            meta = adapter.get_dataset_metadata()
            equipment_group_id = (
                str(meta.get("equipment_group_id")).strip()
                if meta.get("equipment_group_id")
                else None
            )
            metadata = adapter.load_metadata()
            id_col = "record_id" if "record_id" in metadata.columns else None
            if id_col is None:
                try:
                    from l2.equipment.registry import get_equipment_spec

                    spec = get_equipment_spec(equipment_type)
                    if spec.record_id_field in metadata.columns:
                        id_col = spec.record_id_field
                except Exception:  # noqa: BLE001
                    id_col = None
            if id_col and "equipment_id" in metadata.columns:
                for _, item in metadata.iterrows():
                    rid = str(item[id_col]).strip()
                    eid = item.get("equipment_id")
                    record_equipment[rid] = None if eid is None or str(eid).strip() == "" else str(eid).strip()
        except Exception:  # noqa: BLE001 - PED 元数据缺失时按设备类型保守聚合
            record_equipment = {}

        group_map: dict[str, dict[str, Any]] = {}

        def _ensure_group(key: str, *, equipment_id: str | None) -> dict[str, Any]:
            if key not in group_map:
                group_map[key] = {
                    "group_key": key,
                    "equipment_type": equipment_type,
                    "equipment_group_id": equipment_group_id,
                    "equipment_id": equipment_id,
                    "display_name": display_name if equipment_id is None else f"{display_name} · {equipment_id}",
                    "total_records": 0,
                    "l2_complete_count": 0,
                    "l3_review_count": 0,
                    "normal_count": 0,
                    "abnormal_count": 0,
                    "conflict_count": 0,
                    "uncertain_count": 0,
                    "failed_count": 0,
                    "l3_record_ids": [],
                }
            return group_map[key]

        from l2.routing_policy import L3_AUTO_SCREENINGS, ROUTING_POLICY_VERSION, effective_route

        screening_counts = {
            "NORMAL": 0,
            "ABNORMAL": 0,
            "CONFLICT": 0,
            "UNCERTAIN": 0,
            "FAILED": 0,
        }
        route_counts = {"l2_complete": 0, "l3_review_recommended": 0, "failed": 0}
        l3_record_ids: list[str] = []

        for row in rows:
            record_id = str(row["record_id"])
            screening = str(row["screening_result"] or "").upper() or "FAILED"
            if screening not in screening_counts:
                screening = "FAILED"
            screening_counts[screening] += 1

            equipment_id = record_equipment.get(record_id)
            if record_equipment and equipment_id:
                group_key = f"{equipment_type}|eq:{equipment_id}"
            elif equipment_group_id:
                group_key = f"{equipment_type}|group:{equipment_group_id}"
            else:
                group_key = f"{equipment_type}|type"
                equipment_id = None

            group = _ensure_group(group_key, equipment_id=equipment_id)
            group["total_records"] += 1

            route = effective_route(screening)
            if route == "l2_complete":
                route_counts["l2_complete"] += 1
                group["l2_complete_count"] += 1
                group["normal_count"] += 1
            elif screening in L3_AUTO_SCREENINGS:
                route_counts["l3_review_recommended"] += 1
                group["l3_review_count"] += 1
                group["l3_record_ids"].append(record_id)
                l3_record_ids.append(record_id)
                if screening == "ABNORMAL":
                    group["abnormal_count"] += 1
                elif screening == "CONFLICT":
                    group["conflict_count"] += 1
                else:
                    group["uncertain_count"] += 1
            else:
                route_counts["failed"] += 1
                group["failed_count"] += 1

        groups = []
        for group in group_map.values():
            needs_review = int(group["l3_review_count"]) > 0
            complete_count = int(group["l2_complete_count"])
            if needs_review:
                screening_status = "needs_l3_review"
                screening_label = f"建议 L3 复核（{int(group['l3_review_count'])} 条）"
            elif complete_count > 0:
                screening_status = "l2_complete"
                screening_label = "L2 筛查已完成"
            elif int(group["failed_count"]) > 0:
                screening_status = "failed"
                screening_label = "诊断失败"
            else:
                screening_status = "pending"
                screening_label = "尚未完成"
            # 总览不把数千条 ID 全部下发；保留有限引用供交接预览。
            record_ids = list(group["l3_record_ids"])
            groups.append(
                {
                    **{k: v for k, v in group.items() if k != "l3_record_ids"},
                    "screening_status": screening_status,
                    "screening_label": screening_label,
                    "l3_record_ids": record_ids[:200],
                    "l3_record_ids_truncated": len(record_ids) > 200,
                }
            )

        groups.sort(key=lambda item: (-int(item["l3_review_count"]), str(item["display_name"])))
        return {
            "diagnosis_run_id": run["diagnosis_run_id"],
            "prepared_dataset_id": run["prepared_dataset_id"],
            "dataset_revision_id": run["dataset_revision_id"],
            "equipment_type": equipment_type,
            "display_name": display_name,
            "model_bundle_id": run.get("model_bundle_id"),
            "status": run.get("status"),
            "routing_policy_version": ROUTING_POLICY_VERSION,
            "total_records": int(run.get("total_records") or len(rows)),
            "processed_records": int(run.get("processed_records") or len(rows)),
            "succeeded_records": int(run.get("succeeded_records") or 0),
            "failed_records": int(run.get("failed_records") or 0),
            "screening_counts": screening_counts,
            "route_counts": route_counts,
            "l2_complete_count": int(route_counts["l2_complete"]),
            "l3_review_count": int(route_counts["l3_review_recommended"]),
            "l3_record_ids": l3_record_ids[:200],
            "l3_record_ids_truncated": len(l3_record_ids) > 200,
            "groups": groups,
        }

    def _job_public(self, connection, row: dict[str, Any]) -> dict[str, Any]:
        eqs = connection.execute(
            "SELECT * FROM handoff_equipment WHERE handoff_id = ? ORDER BY updated_at",
            (row["handoff_id"],),
        ).fetchall()
        return {
            "handoff_id": row["handoff_id"],
            "dataset_id": row["dataset_id"],
            "pd_version_id": row["pd_version_id"],
            "prepare_run_id": row["prepare_run_id"],
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "error_code": row["error_code"],
            "error_message": row["error_message"],
            "summary": _loads(row["summary_json"]),
            "equipment_items": [self._eq_public(dict(e)) for e in eqs],
        }

    @staticmethod
    def _eq_public(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "item_id": row["item_id"],
            "handoff_id": row["handoff_id"],
            "equipment_type": row["equipment_type"],
            "equipment_category": row["equipment_category"],
            "status": row["status"],
            "ped_id": row["ped_id"],
            "ped_revision_id": row["ped_revision_id"],
            "match_status": row["match_status"],
            "model_bundle_id": row["model_bundle_id"],
            "diagnosis_run_id": row["diagnosis_run_id"],
            "training_job_id": row["training_job_id"],
            "message": row["message"],
            "details": _loads(row["details_json"]),
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _run_public(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "diagnosis_run_id": row["diagnosis_run_id"],
            "prepared_dataset_id": row["prepared_dataset_id"],
            "dataset_revision_id": row["dataset_revision_id"],
            "equipment_type": row["equipment_type"],
            "model_bundle_id": row["model_bundle_id"],
            "schema_version": row["schema_version"],
            "status": row["status"],
            "total_records": int(row["total_records"] or 0),
            "processed_records": int(row["processed_records"] or 0),
            "succeeded_records": int(row["succeeded_records"] or 0),
            "failed_records": int(row["failed_records"] or 0),
            "counts": _loads(row["counts_json"]),
            "content_key": row["content_key"],
            "result_dir": row["result_dir"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "handoff_item_id": row["handoff_item_id"],
        }


HANDOFF_STORE = HandoffStore()
