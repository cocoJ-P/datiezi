"""L5Run 持久化。"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from l5.database import connect, default_db_path, init_db


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _loads(raw: str | None) -> Any:
    if not raw:
        return {}
    return json.loads(raw)


class L5Store:
    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or default_db_path()
        init_db(self.db_path)

    def create_or_get(self, *, content_key: str, meta: dict[str, Any], force_new: bool = False) -> tuple[dict[str, Any], bool]:
        connection = connect(self.db_path)
        try:
            if not force_new:
                row = connection.execute("SELECT * FROM l5_runs WHERE content_key = ?", (content_key,)).fetchone()
                if row:
                    return self._public(dict(row)), False
            rid = f"L5R-{uuid.uuid4().hex[:12]}"
            key = content_key if not force_new else f"{content_key}|force|{rid}"
            connection.execute(
                """
                INSERT INTO l5_runs (
                    l5_run_id, diagnosis_run_id, analysis_run_id, l4_run_id,
                    l3_reasoning_run_id, l4_reasoning_run_id, enrichment_run_id,
                    aggregation_schema_version, recommendation_rule_version, content_key,
                    status, selection_json, source_versions_json, selected_record_count, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?)
                """,
                (
                    rid,
                    meta.get("diagnosis_run_id"),
                    meta.get("analysis_run_id"),
                    meta.get("l4_run_id"),
                    meta.get("l3_reasoning_run_id"),
                    meta.get("l4_reasoning_run_id"),
                    meta.get("enrichment_run_id"),
                    meta.get("aggregation_schema_version"),
                    meta.get("recommendation_rule_version"),
                    key,
                    _dumps(meta.get("selection") or {}),
                    _dumps(meta.get("source_versions") or {}),
                    int(meta.get("selected_record_count") or 0),
                    _now(),
                ),
            )
            connection.commit()
            row = connection.execute("SELECT * FROM l5_runs WHERE l5_run_id = ?", (rid,)).fetchone()
            return self._public(dict(row)), True
        finally:
            connection.close()

    def update(self, l5_run_id: str, **fields: Any) -> dict[str, Any]:
        allowed = {
            "status",
            "processed_record_count",
            "succeeded_record_count",
            "partial_record_count",
            "failed_record_count",
            "error_summary",
            "started_at",
            "finished_at",
            "selection_json",
            "source_versions_json",
        }
        sets = []
        values = []
        for k, v in fields.items():
            if k not in allowed:
                continue
            if k.endswith("_json") and not isinstance(v, str):
                v = _dumps(v)
            sets.append(f"{k} = ?")
            values.append(v)
        if not sets:
            return self.get(l5_run_id)
        connection = connect(self.db_path)
        try:
            connection.execute(
                f"UPDATE l5_runs SET {', '.join(sets)} WHERE l5_run_id = ?",
                (*values, l5_run_id),
            )
            connection.commit()
        finally:
            connection.close()
        return self.get(l5_run_id)

    def get(self, l5_run_id: str) -> dict[str, Any]:
        connection = connect(self.db_path)
        try:
            row = connection.execute("SELECT * FROM l5_runs WHERE l5_run_id = ?", (l5_run_id,)).fetchone()
            if not row:
                raise KeyError(l5_run_id)
            return self._public(dict(row))
        finally:
            connection.close()

    def list_runs(self, *, offset: int = 0, limit: int = 50) -> dict[str, Any]:
        connection = connect(self.db_path)
        try:
            total = connection.execute("SELECT COUNT(*) AS c FROM l5_runs").fetchone()["c"]
            rows = connection.execute(
                "SELECT * FROM l5_runs ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            return {"total": total, "offset": offset, "limit": limit, "items": [self._public(dict(r)) for r in rows]}
        finally:
            connection.close()

    def save_record(self, l5_run_id: str, record: dict[str, Any]) -> None:
        connection = connect(self.db_path)
        try:
            connection.execute(
                """
                INSERT INTO l5_records (
                    l5_run_id, record_id, status, outcome, review_status, completeness,
                    input_fingerprint, assessment_json, provenance_json, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(l5_run_id, record_id) DO UPDATE SET
                    status = excluded.status,
                    outcome = excluded.outcome,
                    review_status = excluded.review_status,
                    completeness = excluded.completeness,
                    input_fingerprint = excluded.input_fingerprint,
                    assessment_json = excluded.assessment_json,
                    provenance_json = excluded.provenance_json,
                    error_message = excluded.error_message
                """,
                (
                    l5_run_id,
                    record["record_id"],
                    record.get("status") or "succeeded",
                    record.get("outcome"),
                    record.get("review_status"),
                    record.get("completeness"),
                    record.get("input_fingerprint"),
                    _dumps(record.get("assessment") or {}),
                    _dumps(record.get("provenance") or {}),
                    record.get("error_message"),
                ),
            )
            connection.commit()
        finally:
            connection.close()

    def get_record(self, l5_run_id: str, record_id: str) -> dict[str, Any]:
        connection = connect(self.db_path)
        try:
            row = connection.execute(
                "SELECT * FROM l5_records WHERE l5_run_id = ? AND record_id = ?",
                (l5_run_id, record_id),
            ).fetchone()
            if not row:
                raise KeyError(record_id)
            return {
                "record_id": row["record_id"],
                "status": row["status"],
                "outcome": row["outcome"],
                "review_status": row["review_status"],
                "completeness": row["completeness"],
                "input_fingerprint": row["input_fingerprint"],
                "assessment": _loads(row["assessment_json"]),
                "provenance": _loads(row["provenance_json"]),
                "error_message": row["error_message"],
            }
        finally:
            connection.close()

    def list_records(
        self,
        l5_run_id: str,
        *,
        offset: int = 0,
        limit: int = 50,
        record_id: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        connection = connect(self.db_path)
        try:
            clauses = ["l5_run_id = ?"]
            params: list[Any] = [l5_run_id]
            if record_id:
                clauses.append("record_id = ?")
                params.append(record_id)
            if status:
                clauses.append("status = ?")
                params.append(status)
            where = " AND ".join(clauses)
            total = connection.execute(f"SELECT COUNT(*) AS c FROM l5_records WHERE {where}", params).fetchone()["c"]
            rows = connection.execute(
                f"SELECT * FROM l5_records WHERE {where} ORDER BY record_id LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
            items = []
            for row in rows:
                items.append(
                    {
                        "record_id": row["record_id"],
                        "status": row["status"],
                        "outcome": row["outcome"],
                        "review_status": row["review_status"],
                        "completeness": row["completeness"],
                        "input_fingerprint": row["input_fingerprint"],
                        "assessment": _loads(row["assessment_json"]),
                        "error_message": row["error_message"],
                    }
                )
            return {"total": total, "offset": offset, "limit": limit, "items": items}
        finally:
            connection.close()

    def _public(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "l5_run_id": row["l5_run_id"],
            "diagnosis_run_id": row.get("diagnosis_run_id"),
            "analysis_run_id": row.get("analysis_run_id"),
            "l4_run_id": row.get("l4_run_id"),
            "l3_reasoning_run_id": row.get("l3_reasoning_run_id"),
            "l4_reasoning_run_id": row.get("l4_reasoning_run_id"),
            "enrichment_run_id": row.get("enrichment_run_id"),
            "aggregation_schema_version": row.get("aggregation_schema_version"),
            "recommendation_rule_version": row.get("recommendation_rule_version"),
            "content_key": row.get("content_key"),
            "status": row.get("status"),
            "selection": _loads(row.get("selection_json")),
            "source_versions": _loads(row.get("source_versions_json")),
            "selected_record_count": row.get("selected_record_count"),
            "processed_record_count": row.get("processed_record_count"),
            "succeeded_record_count": row.get("succeeded_record_count"),
            "partial_record_count": row.get("partial_record_count"),
            "failed_record_count": row.get("failed_record_count"),
            "error_summary": row.get("error_summary"),
            "created_at": row.get("created_at"),
            "started_at": row.get("started_at"),
            "finished_at": row.get("finished_at"),
        }


L5_STORE = L5Store()
