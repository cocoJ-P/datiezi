"""L3 AnalysisRun 持久化。"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from l3.runtime.database import connect, default_db_path, init_db


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _loads(raw: str | None) -> Any:
    if not raw:
        return {}
    return json.loads(raw)


class AnalysisStore:
    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or default_db_path()
        init_db(self.db_path)

    def create_or_get_run(
        self,
        *,
        content_key: str,
        source_diagnosis_run_id: str,
        prepared_dataset_id: str,
        dataset_revision_id: str,
        equipment_type: str,
        model_bundle_id: str | None,
        selected_record_count: int,
        analyzer_config_hash: str,
        routing_policy_version: str,
        force_new: bool = False,
    ) -> tuple[dict[str, Any], bool]:
        connection = connect(self.db_path)
        try:
            if not force_new:
                row = connection.execute(
                    "SELECT * FROM analysis_runs WHERE content_key = ?",
                    (content_key,),
                ).fetchone()
                if row:
                    return self._run_public(dict(row)), False
            run_id = f"AR-{uuid.uuid4().hex[:12]}"
            now = _now()
            # force_new 时使用唯一 content_key 后缀避免唯一约束冲突
            key = content_key if not force_new else f"{content_key}|force|{run_id}"
            connection.execute(
                """
                INSERT INTO analysis_runs (
                    analysis_run_id, source_diagnosis_run_id, prepared_dataset_id, dataset_revision_id,
                    equipment_type, model_bundle_id, selected_record_count, analyzer_config_hash,
                    routing_policy_version, content_key, status, created_at, force_new
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)
                """,
                (
                    run_id,
                    source_diagnosis_run_id,
                    prepared_dataset_id,
                    dataset_revision_id,
                    equipment_type,
                    model_bundle_id,
                    selected_record_count,
                    analyzer_config_hash,
                    routing_policy_version,
                    key,
                    now,
                    1 if force_new else 0,
                ),
            )
            connection.commit()
            row = connection.execute("SELECT * FROM analysis_runs WHERE analysis_run_id = ?", (run_id,)).fetchone()
            return self._run_public(dict(row)), True
        finally:
            connection.close()

    def get_run(self, analysis_run_id: str) -> dict[str, Any]:
        connection = connect(self.db_path)
        try:
            row = connection.execute(
                "SELECT * FROM analysis_runs WHERE analysis_run_id = ?",
                (analysis_run_id,),
            ).fetchone()
            if not row:
                raise KeyError(analysis_run_id)
            return self._run_public(dict(row))
        finally:
            connection.close()

    def get_run_by_diagnosis(self, diagnosis_run_id: str) -> dict[str, Any] | None:
        connection = connect(self.db_path)
        try:
            row = connection.execute(
                """
                SELECT * FROM analysis_runs
                WHERE source_diagnosis_run_id = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (diagnosis_run_id,),
            ).fetchone()
            return self._run_public(dict(row)) if row else None
        finally:
            connection.close()

    def list_queued(self, limit: int = 5) -> list[dict[str, Any]]:
        connection = connect(self.db_path)
        try:
            rows = connection.execute(
                """
                SELECT * FROM analysis_runs
                WHERE status IN ('queued', 'interrupted')
                ORDER BY created_at LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [self._run_public(dict(r)) for r in rows]
        finally:
            connection.close()

    def list_incomplete_for_compensation(self) -> list[dict[str, Any]]:
        connection = connect(self.db_path)
        try:
            rows = connection.execute(
                """
                SELECT * FROM analysis_runs
                WHERE status IN ('queued', 'running', 'interrupted')
                ORDER BY created_at
                """
            ).fetchall()
            return [self._run_public(dict(r)) for r in rows]
        finally:
            connection.close()

    def update_run(self, analysis_run_id: str, **fields: Any) -> dict[str, Any]:
        allowed = {
            "status",
            "processed_record_count",
            "succeeded_record_count",
            "failed_record_count",
            "inconclusive_record_count",
            "error_summary",
            "result_dir",
            "started_at",
            "finished_at",
        }
        sets = []
        values: list[Any] = []
        for key, value in fields.items():
            if key not in allowed:
                continue
            sets.append(f"{key} = ?")
            values.append(value)
        if not sets:
            return self.get_run(analysis_run_id)
        connection = connect(self.db_path)
        try:
            connection.execute(
                f"UPDATE analysis_runs SET {', '.join(sets)} WHERE analysis_run_id = ?",
                (*values, analysis_run_id),
            )
            connection.commit()
        finally:
            connection.close()
        return self.get_run(analysis_run_id)

    def save_records(self, analysis_run_id: str, rows: list[dict[str, Any]]) -> None:
        connection = connect(self.db_path)
        try:
            for row in rows:
                connection.execute(
                    """
                    INSERT INTO analysis_records (
                        analysis_run_id, record_id, equipment_id, status, trigger_reason,
                        screening_result, evidence_json, error_message
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(analysis_run_id, record_id) DO UPDATE SET
                        equipment_id = excluded.equipment_id,
                        status = excluded.status,
                        trigger_reason = excluded.trigger_reason,
                        screening_result = excluded.screening_result,
                        evidence_json = excluded.evidence_json,
                        error_message = excluded.error_message
                    """,
                    (
                        analysis_run_id,
                        row["record_id"],
                        row.get("equipment_id"),
                        row.get("status") or "ok",
                        row.get("trigger_reason"),
                        row.get("screening_result"),
                        _dumps(row.get("evidence") or {}),
                        row.get("error_message"),
                    ),
                )
            connection.commit()
        finally:
            connection.close()

    def list_results(
        self,
        analysis_run_id: str,
        *,
        offset: int = 0,
        limit: int = 50,
        equipment_id: str | None = None,
        record_id: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        connection = connect(self.db_path)
        try:
            clauses = ["analysis_run_id = ?"]
            params: list[Any] = [analysis_run_id]
            if equipment_id:
                clauses.append("equipment_id = ?")
                params.append(equipment_id)
            if record_id:
                clauses.append("record_id = ?")
                params.append(record_id)
            if status:
                clauses.append("status = ?")
                params.append(status)
            where = " AND ".join(clauses)
            total = connection.execute(
                f"SELECT COUNT(*) AS c FROM analysis_records WHERE {where}",
                params,
            ).fetchone()["c"]
            rows = connection.execute(
                f"""
                SELECT * FROM analysis_records
                WHERE {where}
                ORDER BY record_id
                LIMIT ? OFFSET ?
                """,
                (*params, limit, offset),
            ).fetchall()
            items = []
            for row in rows:
                items.append(
                    {
                        "record_id": row["record_id"],
                        "equipment_id": row["equipment_id"],
                        "status": row["status"],
                        "trigger_reason": row["trigger_reason"],
                        "screening_result": row["screening_result"],
                        "evidence": _loads(row["evidence_json"]),
                        "error_message": row["error_message"],
                    }
                )
            return {"total": int(total), "offset": offset, "limit": limit, "items": items}
        finally:
            connection.close()

    def get_record(self, analysis_run_id: str, record_id: str) -> dict[str, Any]:
        page = self.list_results(analysis_run_id, record_id=record_id, limit=1)
        if not page["items"]:
            raise KeyError(record_id)
        return page["items"][0]

    def _run_public(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "analysis_run_id": row["analysis_run_id"],
            "source_diagnosis_run_id": row["source_diagnosis_run_id"],
            "prepared_dataset_id": row["prepared_dataset_id"],
            "dataset_revision_id": row["dataset_revision_id"],
            "equipment_type": row["equipment_type"],
            "model_bundle_id": row.get("model_bundle_id"),
            "selected_record_count": int(row.get("selected_record_count") or 0),
            "processed_record_count": int(row.get("processed_record_count") or 0),
            "succeeded_record_count": int(row.get("succeeded_record_count") or 0),
            "failed_record_count": int(row.get("failed_record_count") or 0),
            "inconclusive_record_count": int(row.get("inconclusive_record_count") or 0),
            "analyzer_config_hash": row.get("analyzer_config_hash"),
            "routing_policy_version": row.get("routing_policy_version"),
            "content_key": row.get("content_key"),
            "status": row.get("status"),
            "error_summary": row.get("error_summary"),
            "result_dir": row.get("result_dir"),
            "created_at": row.get("created_at"),
            "started_at": row.get("started_at"),
            "finished_at": row.get("finished_at"),
        }


ANALYSIS_STORE = AnalysisStore()
