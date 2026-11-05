"""L5Run SQLite（独立于 L2–L4）。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from l2.inference.config import PROJECT_ROOT


def default_db_path() -> Path:
    return Path(PROJECT_ROOT) / "backend" / "data" / "l5_analysis.sqlite3"


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
            CREATE TABLE IF NOT EXISTS l5_runs (
                l5_run_id TEXT PRIMARY KEY,
                diagnosis_run_id TEXT,
                analysis_run_id TEXT,
                l4_run_id TEXT,
                l3_reasoning_run_id TEXT,
                l4_reasoning_run_id TEXT,
                enrichment_run_id TEXT,
                aggregation_schema_version TEXT,
                recommendation_rule_version TEXT,
                content_key TEXT NOT NULL,
                status TEXT NOT NULL,
                selection_json TEXT,
                source_versions_json TEXT,
                selected_record_count INTEGER DEFAULT 0,
                processed_record_count INTEGER DEFAULT 0,
                succeeded_record_count INTEGER DEFAULT 0,
                partial_record_count INTEGER DEFAULT 0,
                failed_record_count INTEGER DEFAULT 0,
                error_summary TEXT,
                created_at TEXT,
                started_at TEXT,
                finished_at TEXT
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_l5_content ON l5_runs(content_key);
            CREATE INDEX IF NOT EXISTS idx_l5_analysis ON l5_runs(analysis_run_id);
            CREATE INDEX IF NOT EXISTS idx_l5_l4 ON l5_runs(l4_run_id);

            CREATE TABLE IF NOT EXISTS l5_records (
                l5_run_id TEXT NOT NULL,
                record_id TEXT NOT NULL,
                status TEXT,
                outcome TEXT,
                review_status TEXT,
                completeness TEXT,
                input_fingerprint TEXT,
                assessment_json TEXT,
                provenance_json TEXT,
                error_message TEXT,
                PRIMARY KEY (l5_run_id, record_id)
            );
            """
        )
        connection.commit()
    finally:
        connection.close()
