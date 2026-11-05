"""训练任务队列。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from l2.equipment.registry import get_equipment_spec
from l2.inference.config import PROJECT_ROOT
from l2.management.audit import append_event
from l2.management.config import ManagementConfig

ALLOWED_TRANSITIONS = {
    "queued": {"running", "interrupted"},
    "running": {"succeeded", "failed", "interrupted"},
    "succeeded": set(),
    "failed": set(),
    "interrupted": set(),
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_training_config(raw: str | None) -> dict[str, Any]:
    try:
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def enrich_job(row: dict[str, Any]) -> dict[str, Any]:
    """把 training_config_json 中的谱系字段提升到任务响应顶层。"""
    out = dict(row)
    cfg = _parse_training_config(out.get("training_config_json"))
    train_mode = cfg.get("train_mode")
    out["train_mode"] = train_mode if train_mode in {"new", "retrain"} else "new"
    parent = cfg.get("parent_model_bundle_id")
    out["parent_model_bundle_id"] = parent if parent else None
    return out


def create_training_job(
    connection: sqlite3.Connection,
    config: ManagementConfig,
    *,
    equipment_type: str,
    dataset_id: str,
    prepared_dataset_id: str | None = None,
    parent_model_bundle_id: str | None = None,
) -> dict[str, Any]:
    source_type = "legacy_csv"
    prepared_meta: dict[str, Any] | None = None
    if prepared_dataset_id:
        from l2.prepared.adapter import PreparedDatasetAdapter

        adapter = PreparedDatasetAdapter(prepared_dataset_id)
        prepared_meta = adapter.get_dataset_metadata()
        if prepared_meta.get("equipment_type") != equipment_type:
            raise ValueError(
                f"PreparedDataset {prepared_dataset_id} 不属于设备类型 {equipment_type}"
            )
        if not prepared_meta.get("training_ready"):
            raise ValueError(f"PreparedDataset {prepared_dataset_id} 不可用于训练。")
        adapter.validate_dataset()
        features_path = Path(adapter.bundle_dir / "features.csv")
        labels_path = Path(adapter.bundle_dir / "labels.csv")
        bound_dataset_id = prepared_dataset_id
        dataset_revision_id = str(prepared_meta.get("revision_id") or "")
        source_type = "l1_prepared"
    else:
        dataset = config.datasets.get(dataset_id)
        if dataset is None:
            raise KeyError(dataset_id)
        if dataset.equipment_type != equipment_type:
            raise ValueError(f"数据集 {dataset_id} 不属于设备类型 {equipment_type}")
        if not dataset.features_path.is_file() or not dataset.labels_path.is_file():
            raise FileNotFoundError("数据集文件不存在。")
        features_path = dataset.features_path
        labels_path = dataset.labels_path
        bound_dataset_id = dataset.dataset_id
        dataset_revision_id = dataset.dataset_revision_id

    try:
        spec = get_equipment_spec(equipment_type)
    except KeyError as exc:
        raise KeyError(f"未注册设备类型：{equipment_type}") from exc
    if prepared_meta and prepared_meta.get("schema_version") not in {None, spec.schema_version}:
        raise ValueError(
            f"数据集 Schema {prepared_meta.get('schema_version')} 与设备 {spec.schema_version} 不兼容。"
        )
    training_config_path = spec.resolve_training_config(PROJECT_ROOT)
    if not training_config_path.is_file():
        raise FileNotFoundError(f"训练配置不存在：{training_config_path}")

    training_config_text = training_config_path.read_text(encoding="utf-8")
    training_config_hash = hashlib.sha256(training_config_text.encode("utf-8")).hexdigest()
    source_data_hash = hashlib.sha256(
        (_sha256_file(features_path) + ":" + _sha256_file(labels_path)).encode("utf-8")
    ).hexdigest()
    job_id = "JOB-" + uuid.uuid4().hex[:12]
    created_at = _now()
    log_path = str((config.logs_root / f"{job_id}.log").resolve())
    config.logs_root.mkdir(parents=True, exist_ok=True)
    parent_id = (parent_model_bundle_id or "").strip() or None
    train_mode = "retrain" if parent_id else "new"
    connection.execute(
        """
        INSERT INTO training_jobs (
            job_id, equipment_type, dataset_id, dataset_revision_id, training_config_json,
            training_config_hash, source_data_hash, features_path, labels_path, output_root,
            status, created_at, log_path
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)
        """,
        (
            job_id,
            equipment_type,
            bound_dataset_id,
            dataset_revision_id,
            json.dumps(
                {
                    "config_path": str(training_config_path),
                    "schema_version": spec.schema_version,
                    "feature_count": spec.feature_count,
                    "class_count": spec.class_count,
                    "source_type": source_type,
                    "prepared_dataset_id": prepared_dataset_id,
                    "l1_prepared_version_id": None
                    if prepared_meta is None
                    else prepared_meta.get("l1_prepared_version_id"),
                    "train_mode": train_mode,
                    "parent_model_bundle_id": parent_id,
                },
                ensure_ascii=False,
            ),
            training_config_hash,
            source_data_hash,
            str(features_path),
            str(labels_path),
            str(config.models_root),
            created_at,
            log_path,
        ),
    )
    append_event(
        connection,
        event_type="training_job_submitted",
        status="ok",
        message=f"提交训练任务 {job_id}",
        equipment_type=equipment_type,
        training_job_id=job_id,
    )
    return get_job(connection, job_id)


def get_job(connection: sqlite3.Connection, job_id: str) -> dict[str, Any]:
    row = connection.execute("SELECT * FROM training_jobs WHERE job_id = ?", (job_id,)).fetchone()
    if row is None:
        raise KeyError(job_id)
    return enrich_job(dict(row))


def list_jobs(
    connection: sqlite3.Connection,
    *,
    status: str | None = None,
    offset: int = 0,
    limit: int = 50,
) -> dict[str, Any]:
    if status:
        total = int(
            connection.execute("SELECT COUNT(*) AS c FROM training_jobs WHERE status = ?", (status,)).fetchone()["c"]
        )
        rows = connection.execute(
            """
            SELECT * FROM training_jobs WHERE status = ?
            ORDER BY created_at DESC LIMIT ? OFFSET ?
            """,
            (status, limit, offset),
        ).fetchall()
    else:
        total = int(connection.execute("SELECT COUNT(*) AS c FROM training_jobs").fetchone()["c"])
        rows = connection.execute(
            "SELECT * FROM training_jobs ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "jobs": [enrich_job(dict(row)) for row in rows],
    }


def claim_next_job(connection: sqlite3.Connection, *, worker_pid: int) -> dict[str, Any] | None:
    connection.execute("BEGIN IMMEDIATE")
    try:
        running = connection.execute(
            "SELECT COUNT(*) AS c FROM training_jobs WHERE status = 'running'"
        ).fetchone()["c"]
        if int(running) > 0:
            connection.commit()
            return None
        row = connection.execute(
            """
            SELECT * FROM training_jobs
            WHERE status = 'queued'
            ORDER BY created_at ASC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            connection.commit()
            return None
        job_id = row["job_id"]
        cursor = connection.execute(
            """
            UPDATE training_jobs
            SET status = 'running', started_at = ?, worker_pid = ?
            WHERE job_id = ? AND status = 'queued'
            """,
            (_now(), worker_pid, job_id),
        )
        if cursor.rowcount != 1:
            connection.rollback()
            return None
        connection.commit()
        return get_job(connection, job_id)
    except Exception:
        connection.rollback()
        raise


def update_job_status(
    connection: sqlite3.Connection,
    job_id: str,
    *,
    new_status: str,
    model_bundle_id: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> dict[str, Any]:
    job = get_job(connection, job_id)
    current = job["status"]
    if new_status not in ALLOWED_TRANSITIONS.get(current, set()):
        raise ValueError(f"非法状态转换：{current} -> {new_status}")
    connection.execute(
        """
        UPDATE training_jobs
        SET status = ?, finished_at = ?, model_bundle_id = COALESCE(?, model_bundle_id),
            error_code = ?, error_message = ?
        WHERE job_id = ?
        """,
        (
            new_status,
            _now() if new_status in {"succeeded", "failed", "interrupted"} else job.get("finished_at"),
            model_bundle_id,
            error_code,
            error_message,
            job_id,
        ),
    )
    return get_job(connection, job_id)


def mark_stale_running_jobs(connection: sqlite3.Connection) -> int:
    """Worker 重启时：将无有效归属的 running 任务标记 interrupted。"""
    rows = connection.execute("SELECT job_id FROM training_jobs WHERE status = 'running'").fetchall()
    count = 0
    for row in rows:
        connection.execute(
            """
            UPDATE training_jobs
            SET status = 'interrupted', finished_at = ?, error_code = 'WORKER_INTERRUPTED',
                error_message = 'Worker 中断，任务未完成。'
            WHERE job_id = ? AND status = 'running'
            """,
            (_now(), row["job_id"]),
        )
        append_event(
            connection,
            event_type="training_job_interrupted",
            status="failed",
            message=f"任务 {row['job_id']} 因 Worker 中断被标记 interrupted",
            training_job_id=row["job_id"],
        )
        count += 1
    return count


def verify_job_input_hashes(job: dict[str, Any]) -> None:
    features = Path(job["features_path"])
    labels = Path(job["labels_path"])
    if not features.is_file() or not labels.is_file():
        raise FileNotFoundError("训练输入文件丢失。")
    current = hashlib.sha256((_sha256_file(features) + ":" + _sha256_file(labels)).encode("utf-8")).hexdigest()
    if current != job["source_data_hash"]:
        raise ValueError("训练输入文件哈希已变化，拒绝继续执行旧任务。")
