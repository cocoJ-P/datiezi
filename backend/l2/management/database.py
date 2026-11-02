"""SQLite 初始化与连接。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS training_jobs (
    job_id TEXT PRIMARY KEY,
    equipment_type TEXT NOT NULL,
    dataset_id TEXT NOT NULL,
    dataset_revision_id TEXT NOT NULL,
    training_config_json TEXT NOT NULL,
    training_config_hash TEXT NOT NULL,
    source_data_hash TEXT NOT NULL,
    features_path TEXT NOT NULL,
    labels_path TEXT NOT NULL,
    output_root TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    model_bundle_id TEXT,
    error_code TEXT,
    error_message TEXT,
    log_path TEXT,
    worker_pid INTEGER
);

CREATE TABLE IF NOT EXISTS model_registry (
    model_bundle_id TEXT PRIMARY KEY,
    equipment_type TEXT NOT NULL,
    feature_schema_version TEXT,
    dataset_id TEXT,
    dataset_revision_id TEXT,
    training_job_id TEXT,
    bundle_path TEXT NOT NULL UNIQUE,
    manifest_hash TEXT NOT NULL,
    created_at TEXT,
    registered_at TEXT NOT NULL,
    status TEXT NOT NULL,
    metrics_summary_json TEXT,
    calibration_status_json TEXT,
    activated_at TEXT,
    FOREIGN KEY(training_job_id) REFERENCES training_jobs(job_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_model_one_active
ON model_registry(equipment_type)
WHERE status = 'active';

CREATE TABLE IF NOT EXISTS management_events (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    equipment_type TEXT,
    model_bundle_id TEXT,
    previous_model_bundle_id TEXT,
    training_job_id TEXT,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON training_jobs(status, created_at);
CREATE INDEX IF NOT EXISTS idx_models_equipment_status ON model_registry(equipment_type, status, registered_at);
CREATE INDEX IF NOT EXISTS idx_events_created ON management_events(created_at);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(db_path), timeout=30, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


def init_db(db_path: Path) -> None:
    connection = connect(db_path)
    try:
        connection.executescript(SCHEMA)
        connection.commit()
    finally:
        connection.close()
