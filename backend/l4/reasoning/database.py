"""L4 ReasoningRun SQLite（独立于 L4 Core / L3 Reasoning）。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from l2.inference.config import PROJECT_ROOT


def default_db_path() -> Path:
    return Path(PROJECT_ROOT) / "backend" / "data" / "l4_reasoning.sqlite3"


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path | None = None) -> None:
    connection = connect(db_path)
    try:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS l4_reasoning_runs (
                reasoning_run_id TEXT PRIMARY KEY,
                source_l4_run_id TEXT NOT NULL,
                source_analysis_run_id TEXT,
                llm_model TEXT,
                prompt_version TEXT,
                tool_registry_version TEXT,
                policy_version TEXT,
                content_key TEXT NOT NULL,
                status TEXT NOT NULL,
                selection_json TEXT,
                selected_record_count INTEGER DEFAULT 0,
                processed_record_count INTEGER DEFAULT 0,
                succeeded_record_count INTEGER DEFAULT 0,
                failed_record_count INTEGER DEFAULT 0,
                validation_failed_record_count INTEGER DEFAULT 0,
                token_usage_json TEXT,
                error_summary TEXT,
                created_at TEXT,
                started_at TEXT,
                finished_at TEXT
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_l4rr_content ON l4_reasoning_runs(content_key);
            CREATE INDEX IF NOT EXISTS idx_l4rr_l4 ON l4_reasoning_runs(source_l4_run_id);

            CREATE TABLE IF NOT EXISTS l4_reasoning_records (
                reasoning_run_id TEXT NOT NULL,
                record_id TEXT NOT NULL,
                status TEXT,
                outcome TEXT,
                input_fingerprint TEXT,
                assessment_json TEXT,
                tool_history_json TEXT,
                error_message TEXT,
                PRIMARY KEY (reasoning_run_id, record_id)
            );
            """
        )
        connection.commit()
    finally:
        connection.close()
