"""从 L1 已发布 PD-* 读取标准化行（不打开 Excel）。"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from app.services.l1_store import data_dir


@dataclass(frozen=True)
class L1PreparedMeta:
    version_id: str
    dataset_id: str
    created_at: str
    p0_sha256: str
    p4_run_id: str
    prepare_run_id: str
    row_count: int
    feature_count: int
    readiness: dict[str, Any]
    original_filename: str
    manifest_path: str | None


def _connect() -> sqlite3.Connection:
    db = Path(data_dir()) / "l1.sqlite3"
    connection = sqlite3.connect(str(db), timeout=30)
    connection.row_factory = sqlite3.Row
    return connection


def list_l1_prepared_datasets() -> list[L1PreparedMeta]:
    connection = _connect()
    try:
        rows = connection.execute(
            """
            SELECT pd.*, d.original_filename
            FROM prepared_datasets pd
            JOIN datasets d ON d.dataset_id = pd.dataset_id
            ORDER BY pd.created_at DESC
            """
        ).fetchall()
        return [_row_meta(row) for row in rows]
    finally:
        connection.close()


def get_l1_prepared_meta(version_id: str) -> L1PreparedMeta:
    connection = _connect()
    try:
        row = connection.execute(
            """
            SELECT pd.*, d.original_filename
            FROM prepared_datasets pd
            JOIN datasets d ON d.dataset_id = pd.dataset_id
            WHERE pd.version_id = ?
            """,
            (version_id,),
        ).fetchone()
        if row is None:
            raise KeyError(version_id)
        return _row_meta(row)
    finally:
        connection.close()


def get_prepare_source_revisions(prepare_run_id: str) -> dict[str, str]:
    connection = _connect()
    try:
        row = connection.execute(
            "SELECT * FROM prepare_runs WHERE run_id = ?",
            (prepare_run_id,),
        ).fetchone()
        if row is None:
            return {"prepare_run_id": prepare_run_id}
        return {
            "prepare_run_id": prepare_run_id,
            "p4_run_id": str(row["p4_run_id"] or ""),
            "p5_run_id": str(row["p5_run_id"] or ""),
            "p6_run_id": str(row["p6_run_id"] or ""),
            "p7_run_id": str(row["p7_run_id"] or ""),
            "p8_run_id": str(row["p8_run_id"] or ""),
            "revision": str(row["revision"]),
            "p0_sha256": str(row["p0_sha256"] or ""),
        }
    finally:
        connection.close()


def iter_l1_prepared_rows(version_id: str) -> Iterator[dict[str, Any]]:
    connection = _connect()
    try:
        cursor = connection.execute(
            """
            SELECT source_row_id, equipment_id, timestamp_text, features_json, validity_json, quality_json
            FROM prepared_feature_rows
            WHERE version_id = ?
            ORDER BY row_id
            """,
            (version_id,),
        )
        for row in cursor:
            features = json.loads(row["features_json"] or "{}")
            quality = json.loads(row["quality_json"] or "{}")
            validity = json.loads(row["validity_json"] or "{}")
            context = features.get("context") if isinstance(features, dict) else {}
            input_features = features.get("input_features") if isinstance(features, dict) else {}
            record_id = _cell_text((context or {}).get("record_id")) or row["source_row_id"]
            yield {
                "source_row_id": row["source_row_id"],
                "record_id": record_id,
                "equipment_id": row["equipment_id"],
                "timestamp_text": row["timestamp_text"],
                "input_features": input_features or {},
                "context": context or {},
                "labels": quality.get("labels") or {},
                "diagnostics": quality.get("diagnostics") or {},
                "validity": validity,
                "equipment_category": _cell_text((context or {}).get("equipment_category")),
            }
    finally:
        connection.close()


def _cell_text(cell: Any) -> str | None:
    if cell is None:
        return None
    if isinstance(cell, dict):
        value = cell.get("value")
        if value is not None and str(value).strip():
            return str(value).strip()
        text = cell.get("text")
        if text is not None and str(text).strip():
            return str(text).strip()
        return None
    text = str(cell).strip()
    return text or None


def _row_meta(row: sqlite3.Row) -> L1PreparedMeta:
    readiness = {}
    try:
        readiness = json.loads(row["readiness_json"] or "{}")
    except json.JSONDecodeError:
        readiness = {}
    return L1PreparedMeta(
        version_id=str(row["version_id"]),
        dataset_id=str(row["dataset_id"]),
        created_at=str(row["created_at"]),
        p0_sha256=str(row["p0_sha256"]),
        p4_run_id=str(row["p4_run_id"]),
        prepare_run_id=str(row["prepare_run_id"]),
        row_count=int(row["row_count"]),
        feature_count=int(row["feature_count"]),
        readiness=readiness,
        original_filename=str(row["original_filename"] or ""),
        manifest_path=str(row["manifest_path"] or "") or None,
    )
