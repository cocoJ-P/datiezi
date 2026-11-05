"""ReasoningRun SQLite。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from l2.inference.config import PROJECT_ROOT

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS reasoning_runs (
    reasoning_run_id TEXT PRIMARY KEY,
    source_analysis_run_id TEXT NOT NULL,
    knowledge_enrichment_run_id TEXT,
    knowledge_snapshot_id TEXT,
    llm_model TEXT,
    prompt_version TEXT NOT NULL,
    tool_registry_version TEXT NOT NULL,
    reasoning_policy_version TEXT NOT NULL,
    content_key TEXT NOT NULL,
    status TEXT NOT NULL,
    selection_json TEXT,
    selected_record_count INTEGER NOT NULL DEFAULT 0,
    processed_record_count INTEGER NOT NULL DEFAULT 0,
    succeeded_record_count INTEGER NOT NULL DEFAULT 0,
    insufficient_record_count INTEGER NOT NULL DEFAULT 0,
    failed_record_count INTEGER NOT NULL DEFAULT 0,
    validation_failed_record_count INTEGER NOT NULL DEFAULT 0,
    token_usage_json TEXT,
    error_summary TEXT,
    created_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_reasoning_content
ON reasoning_runs(content_key);

CREATE TABLE IF NOT EXISTS reasoning_records (
    reasoning_run_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    status TEXT NOT NULL,
    selection_reason TEXT,
    assessment_json TEXT NOT NULL,
    trace_overlay_json TEXT,
    tool_history_json TEXT,
    mode TEXT,
    error_message TEXT,
    PRIMARY KEY (reasoning_run_id, record_id),
    FOREIGN KEY(reasoning_run_id) REFERENCES reasoning_runs(reasoning_run_id)
);
"""


def default_db_path() -> Path:
    import os

    raw = os.getenv("L3_REASONING_DB", "").strip()
    path = Path(raw) if raw else PROJECT_ROOT / "backend" / "data" / "l3_reasoning.sqlite3"
    if not path.is_absolute():
        path = (PROJECT_ROOT / path).resolve()
    return path


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=30, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


def _ensure_column(connection: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    cols = {row[1] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def init_db(db_path: Path | None = None) -> Path:
    path = db_path or default_db_path()
    connection = connect(path)
    try:
        connection.executescript(SCHEMA)
        # 兼容旧库：追加审计列，不破坏已有 ReasoningRun
        _ensure_column(connection, "reasoning_records", "audit_json", "TEXT")
        _ensure_column(connection, "reasoning_records", "outcome_class", "TEXT")
        connection.commit()
    finally:
        connection.close()
    return path
