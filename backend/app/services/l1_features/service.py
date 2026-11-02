"""P7 特征整理与标签隔离。"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import sqlite3

from app.services.l1_cleaning.service import retained_row_ids
from app.services.l1_prepare.context import (
    iter_row_records,
    load_compiled,
    membership_map,
    parse_timestamp_value,
    timestamp_field_for_region,
)
from app.services.l1_standardization.service import _should_scan

_RULE = "features_v1"

_ROLE_CATEGORY = {
    "identifier": "identifier",
    "timestamp": "context",
    "numerical_feature": "input_feature",
    "categorical_feature": "input_feature",
    "context": "context",
    "target_label": "target",
    "diagnostic_output": "diagnostic_output",
    "dataset_metadata": "metadata",
    "unmapped": "metadata",
}

_LABEL_IDS = {
    "fault_type",
    "fault_severity",
    "fault_location",
}
_DIAGNOSTIC_IDS = {
    "diagnosis_confidence",
    "spectrum_description",
    "root_cause",
    "recommended_action",
    "priority",
    "impact_level",
}
_METADATA_IDS = {"dataset_split"}


def _category(field: dict) -> str:
    sid = field.get("standard_field_id") or ""
    role = field.get("role") or ""
    if sid in _LABEL_IDS:
        return "target"
    if sid in _DIAGNOSTIC_IDS:
        return "diagnostic_output"
    if sid in _METADATA_IDS:
        return "metadata"
    cat = _ROLE_CATEGORY.get(role, "metadata")
    if cat == "input_feature" and field.get("eligibility") == "excluded":
        return "metadata"
    if field.get("confirmation_status") != "confirmed":
        return "quality_flag"
    if not field.get("enabled", True):
        return "quality_flag"
    return cat


def run_p7(
    connection: sqlite3.Connection,
    dataset_id: str,
    prepare_run_id: str,
    p4_run: sqlite3.Row,
    p6_run_id: str,
    version_id: str,
    config: dict,
) -> tuple[str, dict]:
    compiled = load_compiled(connection, p4_run)
    membership = membership_map(connection, p4_run["p2_run_id"])
    keep = retained_row_ids(connection, dataset_id, compiled, membership, p6_run_id)

    catalog = []
    for fld in compiled["fields"]:
        cat = _category(fld)
        catalog.append(
            {
                "standard_field_id": fld.get("standard_field_id"),
                "source_column_id": fld.get("source_column_id"),
                "header": fld.get("header"),
                "role": fld.get("role"),
                "category": cat,
                "target_type": fld.get("target_type"),
                "model_input": cat == "input_feature",
            }
        )

    model_input_fields = [f for f in compiled["fields"] if _category(f) == "input_feature" and _should_scan(f)]
    label_fields = [f for f in compiled["fields"] if _category(f) == "target"]
    diagnostic_fields = [f for f in compiled["fields"] if _category(f) == "diagnostic_output"]

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    run_id = "P7-" + uuid.uuid4().hex[:12]

    rows_to_insert = []
    row_count = 0
    for record in iter_row_records(connection, dataset_id, compiled, membership):
        if record.source_row_id not in keep:
            continue
        ts_text, ts_status = parse_timestamp_value(
            record,
            timestamp_field_for_region(compiled, record.region_id),
        )
        features: dict[str, object] = {}
        labels: dict[str, object] = {}
        diagnostics: dict[str, object] = {}
        context: dict[str, object] = {}
        quality: dict[str, object] = {"p5_flags": [], "timestamp_status": ts_status}
        for fld in compiled["fields"]:
            cat = _category(fld)
            cell = record.fields.get(fld["source_column_id"], {})
            payload = {
                "value": cell.get("normalized_value") if cell.get("status") == "success" else None,
                "text": cell.get("normalized_text") or cell.get("raw_value") or "",
                "status": cell.get("status"),
            }
            if cat == "input_feature":
                features[fld["standard_field_id"]] = payload
            elif cat == "target":
                labels[fld["standard_field_id"]] = payload
            elif cat == "diagnostic_output":
                diagnostics[fld["standard_field_id"]] = payload
            elif cat in {"identifier", "context"}:
                context[fld["standard_field_id"]] = payload
        validity = {
            "labels_isolated": True,
            "model_input_field_ids": [f["standard_field_id"] for f in model_input_fields],
            "label_field_ids": [f["standard_field_id"] for f in label_fields],
            "diagnostic_field_ids": [f["standard_field_id"] for f in diagnostic_fields],
        }
        quality["labels"] = labels
        quality["diagnostics"] = diagnostics
        rows_to_insert.append(
            (
                run_id,
                version_id,
                record.source_row_id,
                record.equipment_id,
                ts_text,
                json.dumps({"input_features": features, "context": context}, ensure_ascii=False, separators=(",", ":")),
                json.dumps(validity, ensure_ascii=False, separators=(",", ":")),
                json.dumps(quality, ensure_ascii=False, separators=(",", ":")),
            )
        )
        row_count += 1

    summary = {
        "rule_id": _RULE,
        "feature_catalog": catalog,
        "prepared_row_count": row_count,
        "model_input_field_count": len(model_input_fields),
        "label_field_count": len(label_fields),
        "diagnostic_field_count": len(diagnostic_fields),
        "labels_isolated_from_model_input": True,
        "resample": False,
        "interpolate": False,
    }
    connection.execute(
        """
        INSERT INTO p7_runs (run_id, prepare_run_id, dataset_id, status, created_at, p6_run_id, config_json, summary_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            prepare_run_id,
            dataset_id,
            "completed",
            now,
            p6_run_id,
            json.dumps(config, ensure_ascii=False, separators=(",", ":")),
            json.dumps(summary, ensure_ascii=False, separators=(",", ":")),
        ),
    )
    if rows_to_insert:
        connection.executemany(
            """
            INSERT INTO prepared_feature_rows (
                run_id, version_id, source_row_id, equipment_id, timestamp_text,
                features_json, validity_json, quality_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows_to_insert,
        )
    return run_id, summary
