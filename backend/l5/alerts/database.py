"""AlertRun SQLite（L5b，独立于 L5a 历史报告）。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from l2.inference.config import PROJECT_ROOT


def default_db_path() -> Path:
    return Path(PROJECT_ROOT) / "backend" / "data" / "l5_alerts.sqlite3"


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except Exception:
        pass
    return conn


def init_db(db_path: Path | None = None) -> None:
    connection = connect(db_path)
    try:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS alert_runs (
                alert_run_id TEXT PRIMARY KEY,
                source_l5_run_id TEXT,
                diagnosis_run_id TEXT,
                analysis_run_id TEXT,
                l4_run_id TEXT,
                catalog_version TEXT,
                content_key TEXT NOT NULL,
                status TEXT NOT NULL,
                selection_json TEXT,
                selected_record_count INTEGER DEFAULT 0,
                processed_record_count INTEGER DEFAULT 0,
                succeeded_record_count INTEGER DEFAULT 0,
                failed_record_count INTEGER DEFAULT 0,
                formal_alert_total INTEGER DEFAULT 0,
                error_summary TEXT,
                created_at TEXT,
                started_at TEXT,
                finished_at TEXT
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_alert_content ON alert_runs(content_key);
            CREATE INDEX IF NOT EXISTS idx_alert_analysis ON alert_runs(analysis_run_id);
            CREATE INDEX IF NOT EXISTS idx_alert_l5 ON alert_runs(source_l5_run_id);

            CREATE TABLE IF NOT EXISTS alert_records (
                alert_run_id TEXT NOT NULL,
                record_id TEXT NOT NULL,
                status TEXT,
                assessment_json TEXT,
                error_message TEXT,
                PRIMARY KEY (alert_run_id, record_id)
            );
            """
        )
        connection.commit()
    finally:
        connection.close()
