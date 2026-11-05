"""L1→L2 Handoff / DiagnosisRun SQLite。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from l2.inference.config import PROJECT_ROOT

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS handoff_jobs (
    handoff_id TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL,
    pd_version_id TEXT NOT NULL,
    prepare_run_id TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    error_code TEXT,
    error_message TEXT,
    summary_json TEXT NOT NULL DEFAULT '{}'
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_handoff_pd
ON handoff_jobs(pd_version_id);

CREATE INDEX IF NOT EXISTS idx_handoff_dataset
ON handoff_jobs(dataset_id, created_at);

CREATE TABLE IF NOT EXISTS handoff_equipment (
    item_id TEXT PRIMARY KEY,
    handoff_id TEXT NOT NULL,
    equipment_type TEXT,
    equipment_category TEXT,
    status TEXT NOT NULL,
    ped_id TEXT,
    ped_revision_id TEXT,
    match_status TEXT,
    model_bundle_id TEXT,
    diagnosis_run_id TEXT,
    training_job_id TEXT,
    message TEXT,
    details_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    FOREIGN KEY(handoff_id) REFERENCES handoff_jobs(handoff_id)
);

CREATE INDEX IF NOT EXISTS idx_handoff_eq
ON handoff_equipment(handoff_id, status);

CREATE TABLE IF NOT EXISTS diagnosis_runs (
    diagnosis_run_id TEXT PRIMARY KEY,
    prepared_dataset_id TEXT NOT NULL,
    dataset_revision_id TEXT NOT NULL,
    equipment_type TEXT NOT NULL,
    model_bundle_id TEXT NOT NULL,
    schema_version TEXT,
    status TEXT NOT NULL,
    total_records INTEGER NOT NULL DEFAULT 0,
    processed_records INTEGER NOT NULL DEFAULT 0,
    succeeded_records INTEGER NOT NULL DEFAULT 0,
    failed_records INTEGER NOT NULL DEFAULT 0,
    counts_json TEXT NOT NULL DEFAULT '{}',
    content_key TEXT NOT NULL,
    result_dir TEXT,
    started_at TEXT,
    finished_at TEXT,
    handoff_item_id TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_diag_content
ON diagnosis_runs(content_key);

CREATE TABLE IF NOT EXISTS diagnosis_records (
    diagnosis_run_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    status TEXT NOT NULL,
    screening_result TEXT,
    result_json TEXT NOT NULL,
    PRIMARY KEY (diagnosis_run_id, record_id),
    FOREIGN KEY(diagnosis_run_id) REFERENCES diagnosis_runs(diagnosis_run_id)
);

CREATE INDEX IF NOT EXISTS idx_diag_records_screen
ON diagnosis_records(diagnosis_run_id, screening_result);
"""


def default_db_path() -> Path:
    import os

    raw = os.getenv("L2_HANDOFF_DB", "").strip()
    path = Path(raw) if raw else PROJECT_ROOT / "backend" / "data" / "l2_handoff.sqlite3"
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
