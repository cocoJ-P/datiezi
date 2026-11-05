"""L4Run SQLite（独立于 L3 表）。"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from l2.inference.config import PROJECT_ROOT

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS l4_runs (
    l4_run_id TEXT PRIMARY KEY,
    source_analysis_run_id TEXT NOT NULL,
    source_reasoning_run_id TEXT,
    source_enrichment_run_id TEXT,
    causal_knowledge_version TEXT NOT NULL,
    equipment_mapping_version TEXT NOT NULL,
    content_key TEXT NOT NULL,
    selected_record_count INTEGER NOT NULL DEFAULT 0,
    processed_record_count INTEGER NOT NULL DEFAULT 0,
    succeeded_record_count INTEGER NOT NULL DEFAULT 0,
    failed_record_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    error_summary TEXT,
    selection_json TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    force_new INTEGER NOT NULL DEFAULT 0
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_l4_content ON l4_runs(content_key);
CREATE INDEX IF NOT EXISTS idx_l4_analysis ON l4_runs(source_analysis_run_id, created_at);

CREATE TABLE IF NOT EXISTS l4_records (
    l4_run_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    equipment_id TEXT,
    status TEXT NOT NULL,
    outcome TEXT,
    input_fingerprint TEXT,
    assessment_json TEXT NOT NULL,
    error_message TEXT,
    PRIMARY KEY (l4_run_id, record_id),
    FOREIGN KEY(l4_run_id) REFERENCES l4_runs(l4_run_id)
);

CREATE INDEX IF NOT EXISTS idx_l4_rec ON l4_records(l4_run_id, equipment_id);
"""


def default_db_path() -> Path:
    raw = os.getenv("L4_ANALYSIS_DB", "").strip()
    path = Path(raw) if raw else PROJECT_ROOT / "backend" / "data" / "l4_analysis.sqlite3"
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
