"""L4Run 持久化。"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from l4.database import connect, default_db_path, init_db


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _loads(raw: str | None) -> Any:
    if not raw:
        return {}
    return json.loads(raw)


class L4Store:
    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or default_db_path()
        init_db(self.db_path)

    def create_or_get(
        self,
        *,
        content_key: str,
        meta: dict[str, Any],
        force_new: bool = False,
    ) -> tuple[dict[str, Any], bool]:
        connection = connect(self.db_path)
        try:
            if not force_new:
                row = connection.execute(
                    "SELECT * FROM l4_runs WHERE content_key = ?",
                    (content_key,),
                ).fetchone()
                if row:
                    return self._public(dict(row)), False
            rid = f"L4R-{uuid.uuid4().hex[:12]}"
            key = content_key if not force_new else f"{content_key}|force|{rid}"
            connection.execute(
                """
                INSERT INTO l4_runs (
                    l4_run_id, source_analysis_run_id, source_reasoning_run_id, source_enrichment_run_id,
                    causal_knowledge_version, equipment_mapping_version, content_key,
                    selected_record_count, status, selection_json, created_at, force_new
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?)
                """,
                (
                    rid,
                    meta["source_analysis_run_id"],
                    meta.get("source_reasoning_run_id"),
                    meta.get("source_enrichment_run_id"),
                    meta["causal_knowledge_version"],
                    meta["equipment_mapping_version"],
                    key,
                    int(meta.get("selected_record_count") or 0),
                    _dumps(meta.get("selection") or {}),
                    _now(),
                    1 if force_new else 0,
                ),
            )
            connection.commit()
            row = connection.execute("SELECT * FROM l4_runs WHERE l4_run_id = ?", (rid,)).fetchone()
            return self._public(dict(row)), True
        finally:
            connection.close()

    def update(self, l4_run_id: str, **fields: Any) -> dict[str, Any]:
        allowed = {
            "status",
            "processed_record_count",
            "succeeded_record_count",
            "failed_record_count",
            "error_summary",
            "started_at",
            "finished_at",
            "selection_json",
        }
        sets = []
        values = []
        for k, v in fields.items():
            if k not in allowed:
                continue
            if k == "selection_json" and not isinstance(v, str):
                v = _dumps(v)
            sets.append(f"{k} = ?")
            values.append(v)
        if not sets:
            return self.get(l4_run_id)
        connection = connect(self.db_path)
        try:
            connection.execute(
                f"UPDATE l4_runs SET {', '.join(sets)} WHERE l4_run_id = ?",
                (*values, l4_run_id),
            )
            connection.commit()
        finally:
            connection.close()
        return self.get(l4_run_id)

    def get(self, l4_run_id: str) -> dict[str, Any]:
        connection = connect(self.db_path)
        try:
            row = connection.execute("SELECT * FROM l4_runs WHERE l4_run_id = ?", (l4_run_id,)).fetchone()
            if not row:
                raise KeyError(l4_run_id)
            return self._public(dict(row))
        finally:
            connection.close()

    def get_by_analysis(self, analysis_run_id: str) -> dict[str, Any] | None:
        connection = connect(self.db_path)
        try:
            row = connection.execute(
                """
                SELECT * FROM l4_runs
                WHERE source_analysis_run_id = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (analysis_run_id,),
            ).fetchone()
            return self._public(dict(row)) if row else None
        finally:
            connection.close()

    def list_queued(self, limit: int = 5) -> list[dict[str, Any]]:
        connection = connect(self.db_path)
        try:
            rows = connection.execute(
                """
                SELECT * FROM l4_runs
                WHERE status IN ('queued', 'interrupted')
                ORDER BY created_at LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [self._public(dict(r)) for r in rows]
        finally:
            connection.close()

    def save_record(self, l4_run_id: str, record: dict[str, Any]) -> None:
        connection = connect(self.db_path)
        try:
            connection.execute(
                """
                INSERT INTO l4_records (
                    l4_run_id, record_id, equipment_id, status, outcome,
                    input_fingerprint, assessment_json, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(l4_run_id, record_id) DO UPDATE SET
                    equipment_id = excluded.equipment_id,
                    status = excluded.status,
                    outcome = excluded.outcome,
                    input_fingerprint = excluded.input_fingerprint,
                    assessment_json = excluded.assessment_json,
                    error_message = excluded.error_message
                """,
                (
                    l4_run_id,
                    record["record_id"],
                    record.get("equipment_id"),
                    record.get("status") or "succeeded",
                    record.get("outcome"),
                    record.get("input_fingerprint"),
                    _dumps(record.get("assessment") or {}),
                    record.get("error_message"),
                ),
            )
            connection.commit()
        finally:
            connection.close()

    def get_record(self, l4_run_id: str, record_id: str) -> dict[str, Any]:
        connection = connect(self.db_path)
        try:
            row = connection.execute(
                "SELECT * FROM l4_records WHERE l4_run_id = ? AND record_id = ?",
                (l4_run_id, record_id),
            ).fetchone()
            if not row:
                raise KeyError(record_id)
            return {
                "record_id": row["record_id"],
                "equipment_id": row["equipment_id"],
                "status": row["status"],
                "outcome": row["outcome"],
                "input_fingerprint": row["input_fingerprint"],
                "assessment": _loads(row["assessment_json"]),
                "error_message": row["error_message"],
            }
        finally:
            connection.close()

    def list_records(
        self,
        l4_run_id: str,
        *,
        offset: int = 0,
        limit: int = 50,
        record_id: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        connection = connect(self.db_path)
        try:
            clauses = ["l4_run_id = ?"]
            params: list[Any] = [l4_run_id]
            if record_id:
                clauses.append("record_id = ?")
                params.append(record_id)
            if status:
                clauses.append("status = ?")
                params.append(status)
            where = " AND ".join(clauses)
            total = connection.execute(
                f"SELECT COUNT(*) AS c FROM l4_records WHERE {where}",
                params,
            ).fetchone()["c"]
            rows = connection.execute(
                f"""
                SELECT * FROM l4_records WHERE {where}
                ORDER BY record_id LIMIT ? OFFSET ?
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
                        "outcome": row["outcome"],
                        "input_fingerprint": row["input_fingerprint"],
                        "assessment": _loads(row["assessment_json"]),
                        "error_message": row["error_message"],
                    }
                )
            return {"total": int(total), "offset": offset, "limit": limit, "items": items}
        finally:
            connection.close()

    def _public(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "l4_run_id": row["l4_run_id"],
            "source_analysis_run_id": row["source_analysis_run_id"],
            "source_reasoning_run_id": row.get("source_reasoning_run_id"),
            "source_enrichment_run_id": row.get("source_enrichment_run_id"),
            "causal_knowledge_version": row.get("causal_knowledge_version"),
            "equipment_mapping_version": row.get("equipment_mapping_version"),
            "content_key": row.get("content_key"),
            "selected_record_count": int(row.get("selected_record_count") or 0),
            "processed_record_count": int(row.get("processed_record_count") or 0),
            "succeeded_record_count": int(row.get("succeeded_record_count") or 0),
            "failed_record_count": int(row.get("failed_record_count") or 0),
            "status": row.get("status"),
            "error_summary": row.get("error_summary"),
            "selection": _loads(row.get("selection_json")),
            "created_at": row.get("created_at"),
            "started_at": row.get("started_at"),
            "finished_at": row.get("finished_at"),
        }


L4_STORE = L4Store()
