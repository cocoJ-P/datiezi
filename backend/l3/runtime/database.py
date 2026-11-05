"""L3 AnalysisRun SQLite。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from l2.inference.config import PROJECT_ROOT

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS analysis_runs (
    analysis_run_id TEXT PRIMARY KEY,
    source_diagnosis_run_id TEXT NOT NULL,
    prepared_dataset_id TEXT NOT NULL,
    dataset_revision_id TEXT NOT NULL,
    equipment_type TEXT NOT NULL,
    model_bundle_id TEXT,
    selected_record_count INTEGER NOT NULL DEFAULT 0,
    processed_record_count INTEGER NOT NULL DEFAULT 0,
    succeeded_record_count INTEGER NOT NULL DEFAULT 0,
    failed_record_count INTEGER NOT NULL DEFAULT 0,
    inconclusive_record_count INTEGER NOT NULL DEFAULT 0,
    analyzer_config_hash TEXT NOT NULL,
    routing_policy_version TEXT NOT NULL,
    content_key TEXT NOT NULL,
    status TEXT NOT NULL,
    error_summary TEXT,
    result_dir TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    force_new INTEGER NOT NULL DEFAULT 0
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_l3_content
ON analysis_runs(content_key);

CREATE INDEX IF NOT EXISTS idx_l3_diag
ON analysis_runs(source_diagnosis_run_id, created_at);

CREATE TABLE IF NOT EXISTS analysis_records (
    analysis_run_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    equipment_id TEXT,
    status TEXT NOT NULL,
    trigger_reason TEXT,
    screening_result TEXT,
    evidence_json TEXT NOT NULL,
    error_message TEXT,
    PRIMARY KEY (analysis_run_id, record_id),
    FOREIGN KEY(analysis_run_id) REFERENCES analysis_runs(analysis_run_id)
);

CREATE INDEX IF NOT EXISTS idx_l3_rec_eq
ON analysis_records(analysis_run_id, equipment_id);
"""


def default_db_path() -> Path:
    import os

    raw = os.getenv("L3_ANALYSIS_DB", "").strip()
    path = Path(raw) if raw else PROJECT_ROOT / "backend" / "data" / "l3_analysis.sqlite3"
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


def init_db(db_path: Path | None = None) -> Path:
    path = db_path or default_db_path()
    connection = connect(path)
    try:
        connection.executescript(SCHEMA)
        connection.commit()
    finally:
        connection.close()
    return path
