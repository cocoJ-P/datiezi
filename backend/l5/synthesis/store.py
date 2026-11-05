"""SynthesisRun 存储。"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from l5.synthesis.database import connect


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class SynthesisStore:
    def __init__(self, db_path=None):
        self.db_path = db_path

    def create_or_get(self, *, content_key: str, meta: dict[str, Any], force_new: bool = False) -> tuple[dict, bool]:
        conn = connect(self.db_path)
        try:
            if not force_new:
                row = conn.execute(
                    "SELECT * FROM synthesis_runs WHERE content_key=?", (content_key,)
                ).fetchone()
                if row:
                    return self._run_from_row(row), False
            sid = "L5SYN-" + uuid.uuid4().hex[:12]
            # force_new：避免 UNIQUE(content_key) 冲突
            effective_key = content_key if not force_new else f"{content_key}|{sid}"
            conn.execute(
                """
                INSERT INTO synthesis_runs(
                  synthesis_run_id, content_key, status, prediction_run_ref,
                  source_l5_run_id, source_l2_run_id, source_l3_run_id, source_l4_run_id,
                  l3_reasoning_run_id, l4_reasoning_run_id, record_ids_json, meta_json, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    sid,
                    effective_key,
                    "queued",
                    meta.get("prediction_run_ref"),
                    meta.get("source_l5_run_id"),
                    meta.get("source_l2_run_id") or meta.get("diagnosis_run_id"),
                    meta.get("source_l3_run_id") or meta.get("analysis_run_id"),
                    meta.get("source_l4_run_id") or meta.get("l4_run_id"),
                    meta.get("l3_reasoning_run_id"),
                    meta.get("l4_reasoning_run_id"),
                    json.dumps(meta.get("record_ids") or [], ensure_ascii=False),
                    json.dumps(meta, ensure_ascii=False),
                    _now(),
                ),
            )
            conn.commit()
            return self.get(sid), True
        finally:
            conn.close()

    def get(self, synthesis_run_id: str) -> dict[str, Any]:
        conn = connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT * FROM synthesis_runs WHERE synthesis_run_id=?", (synthesis_run_id,)
            ).fetchone()
            if not row:
                raise KeyError(synthesis_run_id)
            return self._run_from_row(row)
        finally:
            conn.close()

    def update(self, synthesis_run_id: str, **fields: Any) -> dict[str, Any]:
        conn = connect(self.db_path)
        try:
            allowed = {"status", "finished_at", "error_summary", "meta_json"}
            sets = []
            vals = []
            for k, v in fields.items():
                if k not in allowed:
                    continue
                sets.append(f"{k}=?")
                vals.append(v)
            if sets:
                vals.append(synthesis_run_id)
                conn.execute(f"UPDATE synthesis_runs SET {', '.join(sets)} WHERE synthesis_run_id=?", vals)
                conn.commit()
            return self.get(synthesis_run_id)
        finally:
            conn.close()

    def save_record(self, synthesis_run_id: str, record: dict[str, Any]) -> None:
        conn = connect(self.db_path)
        try:
            conn.execute(
                """
                INSERT INTO synthesis_records(synthesis_run_id, record_id, status, assessment_json)
                VALUES(?,?,?,?)
                ON CONFLICT(synthesis_run_id, record_id) DO UPDATE SET
                  status=excluded.status, assessment_json=excluded.assessment_json
                """,
                (
                    synthesis_run_id,
                    record["record_id"],
                    record.get("status") or "succeeded",
                    json.dumps(record.get("assessment") or {}, ensure_ascii=False),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def list_runs(self, *, offset: int = 0, limit: int = 50) -> dict[str, Any]:
        conn = connect(self.db_path)
        try:
            total = conn.execute("SELECT COUNT(*) AS c FROM synthesis_runs").fetchone()["c"]
            rows = conn.execute(
                """
                SELECT * FROM synthesis_runs
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
            return {
                "total": int(total),
                "offset": offset,
                "limit": limit,
                "items": [self._run_from_row(r) for r in rows],
            }
        finally:
            conn.close()

    def list_records(self, synthesis_run_id: str, *, limit: int = 50) -> dict[str, Any]:
        conn = connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT * FROM synthesis_records WHERE synthesis_run_id=? LIMIT ?",
                (synthesis_run_id, limit),
            ).fetchall()
            items = []
            for r in rows:
                items.append(
                    {
                        "record_id": r["record_id"],
                        "status": r["status"],
                        "assessment": json.loads(r["assessment_json"] or "{}"),
                    }
                )
            return {"total": len(items), "items": items}
        finally:
            conn.close()

    def get_record(self, synthesis_run_id: str, record_id: str) -> dict[str, Any]:
        conn = connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT * FROM synthesis_records WHERE synthesis_run_id=? AND record_id=?",
                (synthesis_run_id, record_id),
            ).fetchone()
            if not row:
                raise KeyError(record_id)
            return {
                "record_id": row["record_id"],
                "status": row["status"],
                "assessment": json.loads(row["assessment_json"] or "{}"),
            }
        finally:
            conn.close()

    def _run_from_row(self, row: Any) -> dict[str, Any]:
        meta = json.loads(row["meta_json"] or "{}")
        return {
            "synthesis_run_id": row["synthesis_run_id"],
            "content_key": row["content_key"],
            "status": row["status"],
            "prediction_run_ref": row["prediction_run_ref"],
            "source_l5_run_id": row["source_l5_run_id"],
            "source_l2_run_id": row["source_l2_run_id"],
            "source_l3_run_id": row["source_l3_run_id"],
            "source_l4_run_id": row["source_l4_run_id"],
            "l3_reasoning_run_id": row["l3_reasoning_run_id"],
            "l4_reasoning_run_id": row["l4_reasoning_run_id"],
            "record_ids": json.loads(row["record_ids_json"] or "[]"),
            "meta": meta,
            "created_at": row["created_at"],
            "finished_at": row["finished_at"],
            "error_summary": row["error_summary"],
        }


SYNTHESIS_STORE = SynthesisStore()
