"""Onboarding 草案 SQLite 存储。"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from l2.inference.config import PROJECT_ROOT

SCHEMA = """
CREATE TABLE IF NOT EXISTS onboarding_drafts (
    onboarding_id TEXT PRIMARY KEY,
    content_key TEXT NOT NULL UNIQUE,
    dataset_id TEXT NOT NULL,
    pd_revision_id TEXT NOT NULL,
    equipment_group_id TEXT,
    equipment_category TEXT,
    status TEXT NOT NULL,
    draft_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    confirmed_at TEXT,
    registered_equipment_type TEXT,
    registered_schema_version TEXT
);
CREATE INDEX IF NOT EXISTS idx_onboarding_pd ON onboarding_drafts(pd_revision_id, status);
"""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def default_db_path() -> Path:
    import os

    raw = os.getenv("L2_ONBOARDING_DB", "").strip()
    path = Path(raw) if raw else PROJECT_ROOT / "backend" / "data" / "l2_onboarding.sqlite3"
    if not path.is_absolute():
        path = (PROJECT_ROOT / path).resolve()
    return path


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=30, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    return connection


def init_db(db_path: Path | None = None) -> Path:
    path = db_path or default_db_path()
    connection = connect(path)
    try:
        connection.executescript(SCHEMA)
        connection.commit()
    finally:
        connection.close()
    return path


class OnboardingStore:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = init_db(db_path)

    def get_by_content_key(self, content_key: str) -> dict[str, Any] | None:
        connection = connect(self.db_path)
        try:
            row = connection.execute(
                "SELECT * FROM onboarding_drafts WHERE content_key = ?",
                (content_key,),
            ).fetchone()
            return None if row is None else self._public(dict(row))
        finally:
            connection.close()

    def get(self, onboarding_id: str) -> dict[str, Any]:
        connection = connect(self.db_path)
        try:
            row = connection.execute(
                "SELECT * FROM onboarding_drafts WHERE onboarding_id = ?",
                (onboarding_id,),
            ).fetchone()
            if row is None:
                raise KeyError(onboarding_id)
            return self._public(dict(row))
        finally:
            connection.close()

    def create_or_get(
        self,
        *,
        content_key: str,
        dataset_id: str,
        pd_revision_id: str,
        equipment_group_id: str | None,
        equipment_category: str | None,
        draft: dict[str, Any],
        status: str = "needs_confirmation",
    ) -> tuple[dict[str, Any], bool]:
        existing = self.get_by_content_key(content_key)
        if existing is not None:
            # 已注册草案直接复用；未确认则可刷新草案内容
            if existing["status"] in {"registered", "confirmed"}:
                return existing, False
            return self.update(existing["onboarding_id"], draft=draft, status=status), False

        onboarding_id = f"OB-{uuid.uuid4().hex[:12]}"
        now = _now()
        connection = connect(self.db_path)
        try:
            connection.execute(
                """
                INSERT INTO onboarding_drafts(
                    onboarding_id, content_key, dataset_id, pd_revision_id,
                    equipment_group_id, equipment_category, status, draft_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    onboarding_id,
                    content_key,
                    dataset_id,
                    pd_revision_id,
                    equipment_group_id,
                    equipment_category,
                    status,
                    json.dumps(draft, ensure_ascii=False, separators=(",", ":")),
                    now,
                    now,
                ),
            )
            connection.commit()
            return self.get(onboarding_id), True
        finally:
            connection.close()

    def update(
        self,
        onboarding_id: str,
        *,
        draft: dict[str, Any] | None = None,
        status: str | None = None,
        registered_equipment_type: str | None = None,
        registered_schema_version: str | None = None,
        mark_confirmed: bool = False,
    ) -> dict[str, Any]:
        connection = connect(self.db_path)
        try:
            row = connection.execute(
                "SELECT * FROM onboarding_drafts WHERE onboarding_id = ?",
                (onboarding_id,),
            ).fetchone()
            if row is None:
                raise KeyError(onboarding_id)
            fields = ["updated_at = ?"]
            values: list[Any] = [_now()]
            if draft is not None:
                fields.append("draft_json = ?")
                values.append(json.dumps(draft, ensure_ascii=False, separators=(",", ":")))
            if status is not None:
                fields.append("status = ?")
                values.append(status)
            if registered_equipment_type is not None:
                fields.append("registered_equipment_type = ?")
                values.append(registered_equipment_type)
            if registered_schema_version is not None:
                fields.append("registered_schema_version = ?")
                values.append(registered_schema_version)
            if mark_confirmed:
                fields.append("confirmed_at = ?")
                values.append(_now())
            values.append(onboarding_id)
            connection.execute(
                f"UPDATE onboarding_drafts SET {', '.join(fields)} WHERE onboarding_id = ?",
                values,
            )
            connection.commit()
            return self.get(onboarding_id)
        finally:
            connection.close()

    @staticmethod
    def _public(row: dict[str, Any]) -> dict[str, Any]:
        draft = json.loads(row["draft_json"] or "{}")
        return {
            "onboarding_id": row["onboarding_id"],
            "content_key": row["content_key"],
            "dataset_id": row["dataset_id"],
            "pd_revision_id": row["pd_revision_id"],
            "equipment_group_id": row["equipment_group_id"],
            "equipment_category": row["equipment_category"],
            "status": row["status"],
            "configuration_status": row["status"],
            "draft": draft,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "confirmed_at": row["confirmed_at"],
            "registered_equipment_type": row["registered_equipment_type"],
            "registered_schema_version": row["registered_schema_version"],
        }


ONBOARDING_STORE = OnboardingStore()
