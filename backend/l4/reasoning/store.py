"""L4ReasoningRun 持久化。"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from l4.reasoning.database import connect, default_db_path, init_db


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _loads(raw: str | None) -> Any:
    if not raw:
        return {}
    return json.loads(raw)


class L4ReasoningStore:
    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or default_db_path()
        init_db(self.db_path)

    def create_or_get(
        self, *, content_key: str, meta: dict[str, Any], force_new: bool = False
    ) -> tuple[dict[str, Any], bool]:
        connection = connect(self.db_path)
        try:
            if not force_new:
                row = connection.execute(
                    "SELECT * FROM l4_reasoning_runs WHERE content_key = ?",
                    (content_key,),
                ).fetchone()
                if row:
                    return self._public(dict(row)), False
            rid = f"L4RR-{uuid.uuid4().hex[:12]}"
            key = content_key if not force_new else f"{content_key}|force|{rid}"
            connection.execute(
                """
                INSERT INTO l4_reasoning_runs (
                    reasoning_run_id, source_l4_run_id, source_analysis_run_id, llm_model,
                    prompt_version, tool_registry_version, policy_version, content_key,
                    status, selection_json, selected_record_count, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?)
                """,
                (
                    rid,
                    meta["source_l4_run_id"],
                    meta.get("source_analysis_run_id"),
                    meta.get("llm_model"),
                    meta.get("prompt_version"),
                    meta.get("tool_registry_version"),
                    meta.get("policy_version"),
                    key,
                    _dumps(meta.get("selection") or {}),
                    int(meta.get("selected_record_count") or 0),
                    _now(),
                ),
            )
            connection.commit()
            row = connection.execute(
                "SELECT * FROM l4_reasoning_runs WHERE reasoning_run_id = ?", (rid,)
            ).fetchone()
            return self._public(dict(row)), True
        finally:
            connection.close()

    def update(self, reasoning_run_id: str, **fields: Any) -> dict[str, Any]:
        allowed = {
            "status",
            "processed_record_count",
            "succeeded_record_count",
            "failed_record_count",
            "validation_failed_record_count",
            "token_usage_json",
            "error_summary",
            "started_at",
            "finished_at",
            "llm_model",
            "selection_json",
        }
        sets, values = [], []
        for k, v in fields.items():
            if k not in allowed:
                continue
            if k in {"selection_json", "token_usage_json"} and not isinstance(v, str):
                v = _dumps(v)
            sets.append(f"{k} = ?")
            values.append(v)
        if not sets:
            return self.get(reasoning_run_id)
        connection = connect(self.db_path)
        try:
            connection.execute(
                f"UPDATE l4_reasoning_runs SET {', '.join(sets)} WHERE reasoning_run_id = ?",
                (*values, reasoning_run_id),
            )
            connection.commit()
        finally:
            connection.close()
        return self.get(reasoning_run_id)

    def get(self, reasoning_run_id: str) -> dict[str, Any]:
        connection = connect(self.db_path)
        try:
            row = connection.execute(
                "SELECT * FROM l4_reasoning_runs WHERE reasoning_run_id = ?",
                (reasoning_run_id,),
            ).fetchone()
            if not row:
                raise KeyError(reasoning_run_id)
            return self._public(dict(row))
        finally:
            connection.close()

    def get_by_l4_run(self, l4_run_id: str) -> dict[str, Any] | None:
        connection = connect(self.db_path)
        try:
            row = connection.execute(
                """
                SELECT * FROM l4_reasoning_runs
                WHERE source_l4_run_id = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (l4_run_id,),
            ).fetchone()
            return self._public(dict(row)) if row else None
        finally:
            connection.close()

    def save_record(self, reasoning_run_id: str, record: dict[str, Any]) -> None:
        connection = connect(self.db_path)
        try:
            connection.execute(
                """
                INSERT INTO l4_reasoning_records (
                    reasoning_run_id, record_id, status, outcome, input_fingerprint,
                    assessment_json, tool_history_json, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(reasoning_run_id, record_id) DO UPDATE SET
                    status=excluded.status,
                    outcome=excluded.outcome,
                    input_fingerprint=excluded.input_fingerprint,
                    assessment_json=excluded.assessment_json,
                    tool_history_json=excluded.tool_history_json,
                    error_message=excluded.error_message
                """,
                (
                    reasoning_run_id,
                    record["record_id"],
                    record.get("status"),
                    record.get("outcome"),
                    record.get("input_fingerprint"),
                    _dumps(record.get("assessment") or {}),
                    _dumps(record.get("tool_history") or []),
                    record.get("error_message"),
                ),
            )
            connection.commit()
        finally:
            connection.close()

    def get_record(self, reasoning_run_id: str, record_id: str) -> dict[str, Any]:
        connection = connect(self.db_path)
        try:
            row = connection.execute(
                "SELECT * FROM l4_reasoning_records WHERE reasoning_run_id = ? AND record_id = ?",
                (reasoning_run_id, record_id),
            ).fetchone()
            if not row:
                raise KeyError(record_id)
            return {
                "record_id": row["record_id"],
                "status": row["status"],
                "outcome": row["outcome"],
                "input_fingerprint": row["input_fingerprint"],
                "assessment": _loads(row["assessment_json"]),
                "tool_history": _loads(row["tool_history_json"]),
                "error_message": row["error_message"],
            }
        finally:
            connection.close()

    def list_records(
        self,
        reasoning_run_id: str,
        *,
        offset: int = 0,
        limit: int = 50,
        record_id: str | None = None,
    ) -> dict[str, Any]:
        connection = connect(self.db_path)
        try:
            clauses = ["reasoning_run_id = ?"]
            params: list[Any] = [reasoning_run_id]
            if record_id:
                clauses.append("record_id = ?")
                params.append(record_id)
            where = " AND ".join(clauses)
            total = connection.execute(
                f"SELECT COUNT(*) AS c FROM l4_reasoning_records WHERE {where}", params
            ).fetchone()["c"]
            rows = connection.execute(
                f"""
                SELECT * FROM l4_reasoning_records WHERE {where}
                ORDER BY record_id LIMIT ? OFFSET ?
                """,
                (*params, limit, offset),
            ).fetchall()
            items = []
            for row in rows:
                items.append(
                    {
                        "record_id": row["record_id"],
                        "status": row["status"],
                        "outcome": row["outcome"],
                        "input_fingerprint": row["input_fingerprint"],
                        "assessment": _loads(row["assessment_json"]),
                        "tool_history": _loads(row["tool_history_json"]),
                        "error_message": row["error_message"],
                    }
                )
            return {"total": int(total), "offset": offset, "limit": limit, "items": items}
        finally:
            connection.close()

    def _public(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "reasoning_run_id": row["reasoning_run_id"],
            "source_l4_run_id": row["source_l4_run_id"],
            "source_analysis_run_id": row.get("source_analysis_run_id"),
            "llm_model": row.get("llm_model"),
            "prompt_version": row.get("prompt_version"),
            "tool_registry_version": row.get("tool_registry_version"),
            "policy_version": row.get("policy_version"),
            "content_key": row.get("content_key"),
            "status": row.get("status"),
            "selection": _loads(row.get("selection_json")),
            "selected_record_count": row.get("selected_record_count") or 0,
            "processed_record_count": row.get("processed_record_count") or 0,
            "succeeded_record_count": row.get("succeeded_record_count") or 0,
            "failed_record_count": row.get("failed_record_count") or 0,
            "validation_failed_record_count": row.get("validation_failed_record_count") or 0,
            "token_usage": _loads(row.get("token_usage_json")),
            "error_summary": row.get("error_summary"),
            "created_at": row.get("created_at"),
            "started_at": row.get("started_at"),
            "finished_at": row.get("finished_at"),
        }


L4_REASONING_STORE = L4ReasoningStore()
