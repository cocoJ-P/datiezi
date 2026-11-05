"""Synthesis SQLite。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parents[2] / "data" / "l5_synthesis.sqlite3"


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else DEFAULT_DB
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS synthesis_runs (
          synthesis_run_id TEXT PRIMARY KEY,
          content_key TEXT NOT NULL,
          status TEXT NOT NULL,
          prediction_run_ref TEXT,
          source_l5_run_id TEXT,
          source_l2_run_id TEXT,
          source_l3_run_id TEXT,
          source_l4_run_id TEXT,
          l3_reasoning_run_id TEXT,
          l4_reasoning_run_id TEXT,
          record_ids_json TEXT,
          meta_json TEXT,
          created_at TEXT,
          finished_at TEXT,
          error_summary TEXT
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_syn_content ON synthesis_runs(content_key);
        CREATE TABLE IF NOT EXISTS synthesis_records (
          synthesis_run_id TEXT NOT NULL,
          record_id TEXT NOT NULL,
          status TEXT,
          assessment_json TEXT,
          PRIMARY KEY (synthesis_run_id, record_id)
        );
        """
    )
    return conn
