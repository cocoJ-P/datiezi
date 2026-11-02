"""Schema 模板和 P3 执行结果的存储。已发布模板版本不原地修改。"""

from __future__ import annotations

import json
import sqlite3

from app.services.l1_schema.definitions import FIELDS, TEMPLATE_ID, TEMPLATE_NAME, TEMPLATE_SCOPE, TEMPLATE_SUMMARY, TEMPLATE_VERSION


def ensure_tables(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_templates (
            template_id TEXT NOT NULL,
            version TEXT NOT NULL,
            name TEXT NOT NULL,
            summary TEXT NOT NULL,
            scope TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (template_id, version)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_fields (
            template_id TEXT NOT NULL,
            version TEXT NOT NULL,
            field_id TEXT NOT NULL,
            label TEXT NOT NULL,
            description TEXT NOT NULL,
            physical_meaning TEXT NOT NULL,
            role TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_unit TEXT NOT NULL,
            dimension TEXT NOT NULL,
            quantity TEXT NOT NULL,
            required INTEGER NOT NULL,
            eligibility TEXT NOT NULL,
            uncertain INTEGER NOT NULL,
            unit_sensitive INTEGER NOT NULL,
            aliases_json TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            PRIMARY KEY (template_id, version, field_id)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS p3_runs (
            run_id TEXT PRIMARY KEY,
            dataset_id TEXT NOT NULL,
            revision INTEGER NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            p1_run_id TEXT NOT NULL,
            p2_run_id TEXT,
            template_id TEXT NOT NULL,
            template_version TEXT NOT NULL,
            rule_id TEXT NOT NULL,
            p0_sha256 TEXT NOT NULL,
            config_json TEXT NOT NULL,
            summary_json TEXT NOT NULL,
            UNIQUE (dataset_id, revision)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS p3_mappings (
            run_id TEXT NOT NULL,
            source_column_id TEXT NOT NULL,
            dataset_id TEXT NOT NULL,
            region_id TEXT NOT NULL,
            sheet_id TEXT NOT NULL,
            sheet_name TEXT NOT NULL,
            column_id TEXT NOT NULL,
            column_number INTEGER NOT NULL,
            header TEXT NOT NULL,
            standard_field_id TEXT NOT NULL,
            role TEXT NOT NULL,
            eligibility TEXT NOT NULL,
            source_unit TEXT NOT NULL,
            target_unit TEXT NOT NULL,
            target_type TEXT NOT NULL,
            match_method TEXT NOT NULL,
            match_reason TEXT NOT NULL,
            confirmation_status TEXT NOT NULL,
            candidates_json TEXT NOT NULL,
            sample_json TEXT NOT NULL,
            PRIMARY KEY (run_id, source_column_id)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS p3_issues (
            issue_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            severity TEXT NOT NULL,
            code TEXT NOT NULL,
            message TEXT NOT NULL,
            source_column_id TEXT NOT NULL,
            standard_field_id TEXT NOT NULL,
            region_id TEXT NOT NULL
        )
        """
    )
    connection.execute("CREATE INDEX IF NOT EXISTS idx_p3_mappings_region ON p3_mappings(run_id, region_id, column_number)")
    _seed_template(connection)


def _seed_template(connection: sqlite3.Connection) -> None:
    found = connection.execute(
        "SELECT version FROM schema_templates WHERE template_id = ? AND version = ?",
        (TEMPLATE_ID, TEMPLATE_VERSION),
    ).fetchone()
    if found is not None:
        return
    connection.execute(
        """
        INSERT INTO schema_templates (template_id, version, name, summary, scope, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (TEMPLATE_ID, TEMPLATE_VERSION, TEMPLATE_NAME, TEMPLATE_SUMMARY, TEMPLATE_SCOPE, "2026-11-02T00:00:00Z"),
    )
    connection.executemany(
        """
        INSERT INTO schema_fields (
            template_id, version, field_id, label, description, physical_meaning, role, target_type,
            target_unit, dimension, quantity, required, eligibility, uncertain, unit_sensitive, aliases_json, ordinal
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                TEMPLATE_ID,
                TEMPLATE_VERSION,
                field.field_id,
                field.label,
                field.description,
                field.physical_meaning,
                field.role,
                field.target_type,
                field.target_unit,
                field.dimension,
                field.quantity,
                1 if field.required else 0,
                field.eligibility,
                1 if field.uncertain else 0,
                1 if field.unit_sensitive else 0,
                json.dumps(list(field.aliases), ensure_ascii=False, separators=(",", ":")),
                index,
            )
            for index, field in enumerate(FIELDS)
        ],
    )


def load_template(connection: sqlite3.Connection, template_id: str, version: str | None) -> dict | None:
    if version:
        row = connection.execute(
            "SELECT * FROM schema_templates WHERE template_id = ? AND version = ?",
            (template_id, version),
        ).fetchone()
    else:
        row = connection.execute(
            "SELECT * FROM schema_templates WHERE template_id = ? ORDER BY version DESC LIMIT 1",
            (template_id,),
        ).fetchone()
    if row is None:
        return None
    fields = connection.execute(
        """
        SELECT * FROM schema_fields
        WHERE template_id = ? AND version = ?
        ORDER BY ordinal
        """,
        (row["template_id"], row["version"]),
    ).fetchall()
    return {
        "template_id": row["template_id"],
        "version": row["version"],
        "name": row["name"],
        "summary": row["summary"],
        "scope": row["scope"],
        "fields": [_public_field(item) for item in fields],
    }


def list_templates(connection: sqlite3.Connection) -> list[dict]:
    rows = connection.execute("SELECT template_id, version, name, summary, scope FROM schema_templates ORDER BY template_id, version").fetchall()
    return [
        {
            "template_id": row["template_id"],
            "version": row["version"],
            "name": row["name"],
            "summary": row["summary"],
            "scope": row["scope"],
        }
        for row in rows
    ]


def _public_field(row: sqlite3.Row) -> dict:
    return {
        "field_id": row["field_id"],
        "label": row["label"],
        "description": row["description"],
        "physical_meaning": row["physical_meaning"],
        "role": row["role"],
        "target_type": row["target_type"],
        "target_unit": row["target_unit"],
        "dimension": row["dimension"],
        "quantity": row["quantity"],
        "required": bool(row["required"]),
        "eligibility": row["eligibility"],
        "uncertain": bool(row["uncertain"]),
        "unit_sensitive": bool(row["unit_sensitive"]),
        "aliases": json.loads(row["aliases_json"]),
    }
