"""KnowledgeEnrichmentRun SQLite。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from l2.inference.config import PROJECT_ROOT

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS knowledge_enrichment_runs (
    enrichment_run_id TEXT PRIMARY KEY,
    source_analysis_run_id TEXT NOT NULL,
    knowledge_snapshot_id TEXT NOT NULL,
    retriever_version TEXT NOT NULL,
    content_key TEXT NOT NULL,
    status TEXT NOT NULL,
    selected_record_count INTEGER NOT NULL DEFAULT 0,
    processed_record_count INTEGER NOT NULL DEFAULT 0,
    succeeded_record_count INTEGER NOT NULL DEFAULT 0,
    failed_record_count INTEGER NOT NULL DEFAULT 0,
    error_summary TEXT,
    created_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_enrich_content
ON knowledge_enrichment_runs(content_key);

CREATE TABLE IF NOT EXISTS knowledge_enrichment_records (
    enrichment_run_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    status TEXT NOT NULL,
    context_json TEXT NOT NULL,
    trace_overlay_json TEXT,
    PRIMARY KEY (enrichment_run_id, record_id),
    FOREIGN KEY(enrichment_run_id) REFERENCES knowledge_enrichment_runs(enrichment_run_id)
);
"""


def default_db_path() -> Path:
    import os

    raw = os.getenv("L3_KNOWLEDGE_DB", "").strip()
    path = Path(raw) if raw else PROJECT_ROOT / "backend" / "data" / "l3_knowledge.sqlite3"
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
