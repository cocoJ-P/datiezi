"""AlertRun 持久化。"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from l5.alerts.database import connect, default_db_path, init_db


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _dumps(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False)


def _loads(raw: str | None) -> Any:
    if not raw:
        return {}
    return json.loads(raw)


class AlertStore:
    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or default_db_path()
        init_db(self.db_path)

    def create_or_get(self, *, content_key: str, meta: dict[str, Any], force_new: bool = False) -> tuple[dict[str, Any], bool]:
        connection = connect(self.db_path)
        try:
            if not force_new:
                row = connection.execute("SELECT * FROM alert_runs WHERE content_key = ?", (content_key,)).fetchone()
                if row:
                    return self._public(dict(row)), False
            rid = f"L5AR-{uuid.uuid4().hex[:12]}"
            key = content_key if not force_new else f"{content_key}|force|{rid}"
            connection.execute(
                """
                INSERT INTO alert_runs (
                    alert_run_id, source_l5_run_id, diagnosis_run_id, analysis_run_id, l4_run_id,
                    catalog_version, content_key, status, selection_json, selected_record_count, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?)
                """,
                (
                    rid,
                    meta.get("source_l5_run_id"),
                    meta.get("diagnosis_run_id"),
                    meta.get("analysis_run_id"),
                    meta.get("l4_run_id"),
                    meta.get("catalog_version"),
                    key,
                    _dumps(meta.get("selection") or {}),
                    int(meta.get("selected_record_count") or 0),
                    _now(),
                ),
            )
            connection.commit()
            row = connection.execute("SELECT * FROM alert_runs WHERE alert_run_id = ?", (rid,)).fetchone()
            return self._public(dict(row)), True
        finally:
            connection.close()

    def update(self, alert_run_id: str, **fields: Any) -> dict[str, Any]:
        allowed = {
            "status",
            "processed_record_count",
            "succeeded_record_count",
            "failed_record_count",
            "formal_alert_total",
            "error_summary",
            "started_at",
            "finished_at",
            "selection_json",
        }
        sets, values = [], []
        for k, v in fields.items():
            if k not in allowed:
                continue
            if k.endswith("_json") and not isinstance(v, str):
                v = _dumps(v)
            sets.append(f"{k}=?")
            values.append(v)
        if not sets:
            return self.get(alert_run_id)
        connection = connect(self.db_path)
        try:
            connection.execute(
                f"UPDATE alert_runs SET {', '.join(sets)} WHERE alert_run_id = ?",
                (*values, alert_run_id),
            )
            connection.commit()
        finally:
            connection.close()
        return self.get(alert_run_id)

    def get(self, alert_run_id: str) -> dict[str, Any]:
        connection = connect(self.db_path)
        try:
            row = connection.execute("SELECT * FROM alert_runs WHERE alert_run_id = ?", (alert_run_id,)).fetchone()
            if not row:
                raise KeyError(alert_run_id)
            return self._public(dict(row))
        finally:
            connection.close()

    def get_by_l5(self, l5_run_id: str) -> dict[str, Any] | None:
        connection = connect(self.db_path)
        try:
            row = connection.execute(
                """
                SELECT * FROM alert_runs WHERE source_l5_run_id = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (l5_run_id,),
            ).fetchone()
            return self._public(dict(row)) if row else None
        finally:
            connection.close()

    def get_by_analysis(self, analysis_run_id: str) -> dict[str, Any] | None:
        connection = connect(self.db_path)
        try:
            row = connection.execute(
                """
                SELECT * FROM alert_runs WHERE analysis_run_id = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (analysis_run_id,),
            ).fetchone()
            return self._public(dict(row)) if row else None
        finally:
            connection.close()

    def save_record(self, alert_run_id: str, record: dict[str, Any]) -> None:
        connection = connect(self.db_path)
        try:
            connection.execute(
                """
                INSERT INTO alert_records (alert_run_id, record_id, status, assessment_json, error_message)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(alert_run_id, record_id) DO UPDATE SET
                    status=excluded.status,
                    assessment_json=excluded.assessment_json,
                    error_message=excluded.error_message
                """,
                (
                    alert_run_id,
                    record["record_id"],
                    record.get("status") or "succeeded",
                    _dumps(record.get("assessment") or {}),
                    record.get("error_message"),
                ),
            )
            connection.commit()
        finally:
            connection.close()

    def get_record(self, alert_run_id: str, record_id: str) -> dict[str, Any]:
        connection = connect(self.db_path)
        try:
            row = connection.execute(
                "SELECT * FROM alert_records WHERE alert_run_id = ? AND record_id = ?",
                (alert_run_id, record_id),
            ).fetchone()
            if not row:
                raise KeyError(record_id)
            return {
                "record_id": row["record_id"],
                "status": row["status"],
                "assessment": _loads(row["assessment_json"]),
                "error_message": row["error_message"],
            }
        finally:
            connection.close()

    def list_records(self, alert_run_id: str, *, offset: int = 0, limit: int = 50) -> dict[str, Any]:
        connection = connect(self.db_path)
        try:
            total = connection.execute(
                "SELECT COUNT(*) AS c FROM alert_records WHERE alert_run_id = ?",
                (alert_run_id,),
            ).fetchone()["c"]
            rows = connection.execute(
                """
                SELECT * FROM alert_records WHERE alert_run_id = ?
                ORDER BY record_id LIMIT ? OFFSET ?
                """,
                (alert_run_id, limit, offset),
            ).fetchall()
            items = [
                {
                    "record_id": r["record_id"],
                    "status": r["status"],
                    "assessment": _loads(r["assessment_json"]),
                    "error_message": r["error_message"],
                }
                for r in rows
            ]
            return {"total": total, "offset": offset, "limit": limit, "items": items}
        finally:
            connection.close()

    def _public(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "alert_run_id": row["alert_run_id"],
            "source_l5_run_id": row.get("source_l5_run_id"),
            "diagnosis_run_id": row.get("diagnosis_run_id"),
            "analysis_run_id": row.get("analysis_run_id"),
            "l4_run_id": row.get("l4_run_id"),
            "catalog_version": row.get("catalog_version"),
            "content_key": row.get("content_key"),
            "status": row.get("status"),
            "selection": _loads(row.get("selection_json")),
            "selected_record_count": row.get("selected_record_count"),
            "processed_record_count": row.get("processed_record_count"),
            "succeeded_record_count": row.get("succeeded_record_count"),
            "failed_record_count": row.get("failed_record_count"),
            "formal_alert_total": row.get("formal_alert_total"),
            "error_summary": row.get("error_summary"),
            "created_at": row.get("created_at"),
            "started_at": row.get("started_at"),
            "finished_at": row.get("finished_at"),
        }


ALERT_STORE = AlertStore()
