"""P8 验证、报告与数据发布。"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import sqlite3

from app.services.l1_store import L1Error, data_dir

_RULE = "publish_v1"


def run_p8(
    connection: sqlite3.Connection,
    dataset_id: str,
    prepare_run_id: str,
    dataset_row: sqlite3.Row,
    p4_run: sqlite3.Row,
    p7_run_id: str,
    p7_summary: dict,
    p5_summary: dict,
    p6_summary: dict,
    version_id: str,
    config: dict,
) -> tuple[str, dict, dict]:
    p7_run = connection.execute("SELECT * FROM p7_runs WHERE run_id = ?", (p7_run_id,)).fetchone()
    if p7_run is None:
        raise L1Error("p7_not_found", "缺少 P7 特征整理记录。", 500)

    row_count = int(
        connection.execute(
            "SELECT COUNT(*) AS total FROM prepared_feature_rows WHERE version_id = ?",
            (version_id,),
        ).fetchone()["total"]
    )
    if row_count != p7_summary.get("prepared_row_count"):
        raise L1Error("prepare_count_mismatch", "准备行数与 P7 摘要不一致。", 500)

    path = data_dir() / "files" / str(dataset_row["stored_name"])
    if not path.is_file():
        raise L1Error("storage_missing", "原始文件不存在，无法验证哈希。", 409)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != dataset_row["sha256"]:
        raise L1Error("hash_changed", "原始文件哈希已变化，数据准备结果无效。", 409)
    if digest != p4_run["p0_sha256"]:
        raise L1Error("upstream_hash_stale", "P4 记录的原件哈希与当前不一致。", 409)

    labels_isolated = bool(p7_summary.get("labels_isolated_from_model_input"))
    readiness = _readiness(p5_summary, p6_summary, p7_summary, p4_run, labels_isolated)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    artifact_dir = data_dir() / "prepared" / version_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = artifact_dir / "manifest.json"
    manifest = {
        "version_id": version_id,
        "dataset_id": dataset_id,
        "prepare_run_id": prepare_run_id,
        "p4_run_id": p4_run["run_id"],
        "p0_sha256": digest,
        "created_at": now,
        "row_count": row_count,
        "feature_count": p7_summary.get("model_input_field_count", 0),
        "readinessByAnalysisType": readiness,
        "labels_isolated": labels_isolated,
        "artifacts": [],
    }
    features_path = _write_features_artifact(artifact_dir, version_id, connection)
    manifest["artifacts"] = [{"name": "manifest.json", "path": str(manifest_path)}]
    if features_path:
        manifest["artifacts"].append({"name": features_path.name, "path": str(features_path)})

    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    run_id = "P8-" + uuid.uuid4().hex[:12]
    values = list(readiness.values())
    all_blocked = bool(values) and all(v == "blocked" for v in values)
    any_usable = any(v in {"ready", "ready_with_warnings"} for v in values)
    warning = any(v == "ready_with_warnings" for v in values) or any(v == "blocked" for v in values) and any_usable
    # 按用途判定：部分用途阻断时仍可发布其他用途产物
    status = "failed" if all_blocked or not any_usable else "completed_with_warnings" if warning else "completed"
    summary = {
        "rule_id": _RULE,
        "version_id": version_id,
        "row_count": row_count,
        "hash_verified": True,
        "labels_isolated": labels_isolated,
        "readinessByAnalysisType": readiness,
        "manifest_path": str(manifest_path),
    }
    connection.execute(
        """
        INSERT INTO p8_runs (run_id, prepare_run_id, dataset_id, status, created_at, p7_run_id, config_json, summary_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            prepare_run_id,
            dataset_id,
            status,
            now,
            p7_run_id,
            json.dumps(config, ensure_ascii=False, separators=(",", ":")),
            json.dumps(summary, ensure_ascii=False, separators=(",", ":")),
        ),
    )
    connection.execute(
        """
        INSERT INTO prepared_datasets (
            version_id, prepare_run_id, dataset_id, created_at, p0_sha256, p4_run_id,
            manifest_path, readiness_json, row_count, feature_count
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            version_id,
            prepare_run_id,
            dataset_id,
            now,
            digest,
            p4_run["run_id"],
            str(manifest_path),
            json.dumps(readiness, ensure_ascii=False, separators=(",", ":")),
            row_count,
            int(p7_summary.get("model_input_field_count") or 0),
        ),
    )
    return run_id, summary, readiness


def _readiness(p5_summary: dict, p6_summary: dict, p7_summary: dict, p4_run: sqlite3.Row, labels_isolated: bool) -> dict:
    p5_errors = int(p5_summary.get("error_count") or 0)
    p5_warnings = int(p5_summary.get("warning_count") or 0)
    quarantined = int(p6_summary.get("quarantined_row_count") or 0)
    partial_p4 = p4_run["status"] == "partial"
    base = "blocked" if p5_errors or not labels_isolated else "ready_with_warnings" if p5_warnings or quarantined or partial_p4 else "ready"
    return {
        "timeseries_anomaly": base,
        "fault_classification": "blocked" if not labels_isolated else base,
        "bearing_diagnosis": base,
        "general_exploration": "ready" if base != "blocked" else "ready_with_warnings",
    }


def _write_features_artifact(artifact_dir: Path, version_id: str, connection: sqlite3.Connection) -> Path | None:
    rows = connection.execute(
        """
        SELECT source_row_id, equipment_id, timestamp_text, features_json, validity_json, quality_json
        FROM prepared_feature_rows WHERE version_id = ? ORDER BY row_id
        """,
        (version_id,),
    ).fetchall()
    if not rows:
        return None
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError:
        out = artifact_dir / "features.jsonl"
        with out.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(
                    json.dumps(
                        {
                            "source_row_id": row["source_row_id"],
                            "equipment_id": row["equipment_id"],
                            "timestamp_text": row["timestamp_text"],
                            "features": json.loads(row["features_json"]),
                            "validity": json.loads(row["validity_json"]),
                            "quality": json.loads(row["quality_json"]),
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    + "\n"
                )
        return out
    records = []
    for row in rows:
        records.append(
            {
                "source_row_id": row["source_row_id"],
                "equipment_id": row["equipment_id"],
                "timestamp_text": row["timestamp_text"],
                "features_json": row["features_json"],
                "validity_json": row["validity_json"],
                "quality_json": row["quality_json"],
            }
        )
    table = pa.Table.from_pylist(records)
    out = artifact_dir / "features.parquet"
    pq.write_table(table, out)
    return out
