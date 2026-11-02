"""P5–P8 与数据准备编排的 SQLite 表。"""

from __future__ import annotations

import sqlite3


def ensure_tables(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS prepare_runs (
            run_id TEXT PRIMARY KEY,
            dataset_id TEXT NOT NULL,
            revision INTEGER NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            finished_at TEXT,
            p0_sha256 TEXT NOT NULL,
            p4_run_id TEXT NOT NULL,
            signature TEXT NOT NULL,
            config_json TEXT NOT NULL,
            summary_json TEXT NOT NULL,
            p5_run_id TEXT,
            p6_run_id TEXT,
            p7_run_id TEXT,
            p8_run_id TEXT,
            version_id TEXT,
            UNIQUE (dataset_id, revision)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS p5_runs (
            run_id TEXT PRIMARY KEY,
            prepare_run_id TEXT NOT NULL,
            dataset_id TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            p4_run_id TEXT NOT NULL,
            config_json TEXT NOT NULL,
            summary_json TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS p5_issues (
            issue_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            severity TEXT NOT NULL,
            code TEXT NOT NULL,
            message TEXT NOT NULL,
            equipment_id TEXT NOT NULL,
            source_row_id TEXT NOT NULL,
            region_id TEXT NOT NULL,
            field_id TEXT NOT NULL,
            detail_json TEXT NOT NULL
        )
        """
    )
    connection.execute("CREATE INDEX IF NOT EXISTS idx_p5_issues_run ON p5_issues(run_id, issue_id)")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS p6_runs (
            run_id TEXT PRIMARY KEY,
            prepare_run_id TEXT NOT NULL,
            dataset_id TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            p5_run_id TEXT NOT NULL,
            config_json TEXT NOT NULL,
            summary_json TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS p6_decisions (
            decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            source_row_id TEXT NOT NULL,
            action TEXT NOT NULL,
            reason TEXT NOT NULL,
            rule_id TEXT NOT NULL
        )
        """
    )
    connection.execute("CREATE INDEX IF NOT EXISTS idx_p6_decisions_run ON p6_decisions(run_id, decision_id)")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS p7_runs (
            run_id TEXT PRIMARY KEY,
            prepare_run_id TEXT NOT NULL,
            dataset_id TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            p6_run_id TEXT NOT NULL,
            config_json TEXT NOT NULL,
            summary_json TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS p8_runs (
            run_id TEXT PRIMARY KEY,
            prepare_run_id TEXT NOT NULL,
            dataset_id TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            p7_run_id TEXT NOT NULL,
            config_json TEXT NOT NULL,
            summary_json TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS prepared_datasets (
            version_id TEXT PRIMARY KEY,
            prepare_run_id TEXT NOT NULL,
            dataset_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            p0_sha256 TEXT NOT NULL,
            p4_run_id TEXT NOT NULL,
            manifest_path TEXT NOT NULL,
            readiness_json TEXT NOT NULL,
            row_count INTEGER NOT NULL,
            feature_count INTEGER NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS prepared_feature_rows (
            row_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            version_id TEXT NOT NULL,
            source_row_id TEXT NOT NULL,
            equipment_id TEXT NOT NULL,
            timestamp_text TEXT NOT NULL,
            features_json TEXT NOT NULL,
            validity_json TEXT NOT NULL,
            quality_json TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_prepared_rows_version ON prepared_feature_rows(version_id, row_id)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_prepared_rows_equipment ON prepared_feature_rows(version_id, equipment_id, timestamp_text)"
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS data_preparation_reports (
            prepare_run_id TEXT PRIMARY KEY,
            report_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
