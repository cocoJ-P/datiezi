"""管理操作事件（只追加）。"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def append_event(
    connection: sqlite3.Connection,
    *,
    event_type: str,
    status: str,
    message: str,
    equipment_type: str | None = None,
    model_bundle_id: str | None = None,
    previous_model_bundle_id: str | None = None,
    training_job_id: str | None = None,
) -> str:
    event_id = "EVT-" + uuid.uuid4().hex[:12]
    connection.execute(
        """
        INSERT INTO management_events (
            event_id, event_type, equipment_type, model_bundle_id, previous_model_bundle_id,
            training_job_id, created_at, status, message
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            event_type,
            equipment_type,
            model_bundle_id,
            previous_model_bundle_id,
            training_job_id,
            _now(),
            status,
            message,
        ),
    )
    return event_id


def list_events(
    connection: sqlite3.Connection,
    *,
    offset: int = 0,
    limit: int = 50,
) -> dict[str, Any]:
    total = int(connection.execute("SELECT COUNT(*) AS c FROM management_events").fetchone()["c"])
    rows = connection.execute(
        """
        SELECT * FROM management_events
        ORDER BY created_at DESC, event_id DESC
        LIMIT ? OFFSET ?
        """,
        (limit, offset),
    ).fetchall()
    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "events": [dict(row) for row in rows],
    }
