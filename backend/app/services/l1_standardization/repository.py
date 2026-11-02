"""P4 运行、字段快照和转换错误的存储。不复制原始单元格。"""

from __future__ import annotations

import sqlite3


def ensure_tables(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS p4_runs (
            run_id TEXT PRIMARY KEY,
            dataset_id TEXT NOT NULL,
            revision INTEGER NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            p3_run_id TEXT NOT NULL,
            p2_run_id TEXT,
            p1_run_id TEXT NOT NULL,
            p0_sha256 TEXT NOT NULL,
            template_id TEXT NOT NULL,
            template_version TEXT NOT NULL,
            rule_id TEXT NOT NULL,
            excel_date_system TEXT NOT NULL,
            publish_ready INTEGER NOT NULL,
            config_json TEXT NOT NULL,
            summary_json TEXT NOT NULL,
            UNIQUE (dataset_id, revision)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS p4_fields (
            run_id TEXT NOT NULL,
            source_column_id TEXT NOT NULL,
            region_id TEXT NOT NULL,
            sheet_id TEXT NOT NULL,
            sheet_name TEXT NOT NULL,
            column_id TEXT NOT NULL,
            column_number INTEGER NOT NULL,
            header TEXT NOT NULL,
            standard_field_id TEXT NOT NULL,
            standard_label TEXT NOT NULL,
            role TEXT NOT NULL,
            eligibility TEXT NOT NULL,
            confirmation_status TEXT NOT NULL,
            source_unit TEXT NOT NULL,
            target_unit TEXT NOT NULL,
            target_type TEXT NOT NULL,
            dimension TEXT NOT NULL,
            enabled INTEGER NOT NULL,
            field_status TEXT NOT NULL,
            rule_id TEXT NOT NULL,
            PRIMARY KEY (run_id, source_column_id)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS p4_issues (
            issue_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            severity TEXT NOT NULL,
            code TEXT NOT NULL,
            message TEXT NOT NULL,
            source_column_id TEXT NOT NULL,
            source_row_id TEXT NOT NULL,
            region_id TEXT NOT NULL,
            sheet_id TEXT NOT NULL,
            sheet_name TEXT NOT NULL,
            row_number INTEGER NOT NULL,
            column_number INTEGER NOT NULL,
            cell_id TEXT NOT NULL,
            coordinate TEXT NOT NULL,
            raw_value TEXT NOT NULL,
            cell_type TEXT NOT NULL,
            target_type TEXT NOT NULL,
            rule_id TEXT NOT NULL
        )
        """
    )
    connection.execute("CREATE INDEX IF NOT EXISTS idx_p4_issues_run ON p4_issues(run_id, issue_id)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_p4_fields_region ON p4_fields(run_id, region_id, column_number)")
