"""模型仓库：发现、验证与登记。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from l2.management.audit import append_event
from l2.management.config import ManagementConfig
from l2.management.errors import ManagementError
from l2.training.artifacts import REQUIRED_FILES, load_model_bundle, verify_bundle


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_hash(manifest: dict[str, Any]) -> str:
    payload = json.dumps(manifest, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def resolve_trusted_bundle_path(config: ManagementConfig, bundle_path: Path) -> Path:
    root = config.models_root.resolve()
    path = bundle_path.resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ManagementError(
            "MODEL_BUNDLE_INVALID",
            "模型包路径不在可信模型根目录内。",
            details={"bundle_path": str(path), "models_root": str(root)},
        ) from exc
    if not path.is_dir():
        raise ManagementError("MODEL_BUNDLE_INVALID", f"模型包目录不存在：{path}")
    return path


def validate_bundle_for_registry(
    config: ManagementConfig,
    bundle_path: Path,
    *,
    expected_equipment_type: str | None = None,
) -> dict[str, Any]:
    path = resolve_trusted_bundle_path(config, bundle_path)
    missing = [name for name in REQUIRED_FILES if not (path / name).is_file()]
    if missing:
        raise ManagementError(
            "MODEL_BUNDLE_INVALID",
            f"模型包缺少必要文件：{missing}",
            details={"missing": missing},
        )
    try:
        report = verify_bundle(path)
        bundle = load_model_bundle(path)
    except Exception as exc:  # noqa: BLE001 - 统一转为管理错误
        raise ManagementError(
            "MODEL_BUNDLE_INVALID",
            f"模型包验证失败：{exc}",
            details={"path": str(path)},
        ) from exc

    manifest = bundle.manifest
    model_bundle_id = str(manifest.get("model_bundle_id") or "")
    equipment_type = str(manifest.get("equipment_type") or "")
    if not model_bundle_id:
        raise ManagementError("MODEL_BUNDLE_INVALID", "manifest 缺少 model_bundle_id。")
    if expected_equipment_type and equipment_type != expected_equipment_type:
        raise ManagementError(
            "MODEL_BUNDLE_INVALID",
            f"设备类型不一致：manifest={equipment_type}, expected={expected_equipment_type}",
        )
    if path.name != model_bundle_id:
        raise ManagementError(
            "MODEL_BUNDLE_INVALID",
            f"目录名 {path.name} 与 model_bundle_id {model_bundle_id} 不一致。",
        )

    artifact_hashes = manifest.get("artifact_hashes") or {}
    for name, expected in artifact_hashes.items():
        file_path = path / name
        if not file_path.is_file():
            continue
        actual = _sha256_file(file_path)
        if actual != expected:
            raise ManagementError(
                "MODEL_BUNDLE_INVALID",
                f"文件哈希不匹配：{name}",
                details={"expected": expected, "actual": actual},
            )

    metrics = bundle.metrics
    metrics_summary = {
        "binary_accuracy": (metrics.get("binary") or {}).get("final", {}).get("accuracy"),
        "multiclass_accuracy": (metrics.get("multiclass") or {}).get("final", {}).get("accuracy"),
        "binary_macro_f1": (metrics.get("binary") or {}).get("final", {}).get("macro_f1"),
        "multiclass_macro_f1": (metrics.get("multiclass") or {}).get("final", {}).get("macro_f1"),
        "binary_log_loss": (metrics.get("binary") or {}).get("final", {}).get("log_loss"),
        "multiclass_log_loss": (metrics.get("multiclass") or {}).get("final", {}).get("log_loss"),
        "binary_brier": (metrics.get("binary") or {}).get("final", {}).get("brier_score"),
        "multiclass_brier": (metrics.get("multiclass") or {}).get("final", {}).get("brier_score"),
        "conflict_count": (metrics.get("consistency") or {}).get("conflict_count"),
    }
    evaluation_summary = manifest.get("evaluation_summary") or {}
    for key, value in evaluation_summary.items():
        metrics_summary.setdefault(key, value)

    return {
        "model_bundle_id": model_bundle_id,
        "equipment_type": equipment_type,
        "feature_schema_version": str(
            bundle.feature_schema.get("version") or manifest.get("feature_schema_version") or ""
        ),
        "dataset_id": manifest.get("dataset_id"),
        "dataset_revision_id": manifest.get("dataset_revision_id"),
        "bundle_path": str(path),
        "manifest_hash": _manifest_hash(manifest),
        "created_at": manifest.get("created_at"),
        "status": str(manifest.get("status") or "candidate"),
        "metrics_summary": metrics_summary,
        "calibration_status": manifest.get("calibration_status") or {},
        "manifest": manifest,
        "verify_report": report,
        "bundle": bundle,
    }


def register_bundle(
    connection: sqlite3.Connection,
    config: ManagementConfig,
    bundle_path: Path,
    *,
    training_job_id: str | None = None,
    expected_equipment_type: str | None = None,
    force_status: str | None = None,
) -> dict[str, Any]:
    validated = validate_bundle_for_registry(
        config,
        bundle_path,
        expected_equipment_type=expected_equipment_type,
    )
    model_bundle_id = validated["model_bundle_id"]
    existing = connection.execute(
        "SELECT * FROM model_registry WHERE model_bundle_id = ?",
        (model_bundle_id,),
    ).fetchone()
    if existing is not None:
        return _serialize_model(dict(existing))

    status = force_status or "candidate"
    if status not in {"candidate", "active", "archived"}:
        raise ManagementError("MODEL_BUNDLE_INVALID", f"非法模型状态：{status}")
    if status == "active":
        raise ManagementError(
            "MODEL_VERSION_CONFLICT",
            "登记时不允许直接写入 active，请使用激活接口。",
        )

    registered_at = _now()
    connection.execute(
        """
        INSERT INTO model_registry (
            model_bundle_id, equipment_type, feature_schema_version, dataset_id,
            dataset_revision_id, training_job_id, bundle_path, manifest_hash,
            created_at, registered_at, status, metrics_summary_json, calibration_status_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            model_bundle_id,
            validated["equipment_type"],
            validated["feature_schema_version"],
            validated["dataset_id"],
            validated["dataset_revision_id"],
            training_job_id,
            validated["bundle_path"],
            validated["manifest_hash"],
            validated["created_at"],
            registered_at,
            status,
            json.dumps(validated["metrics_summary"], ensure_ascii=False),
            json.dumps(validated["calibration_status"], ensure_ascii=False),
        ),
    )
    append_event(
        connection,
        event_type="model_registered",
        status="ok",
        message=f"登记模型 {model_bundle_id} 为 {status}",
        equipment_type=validated["equipment_type"],
        model_bundle_id=model_bundle_id,
        training_job_id=training_job_id,
    )
    return get_model(connection, model_bundle_id)


def discover_local_bundles(config: ManagementConfig, equipment_type: str) -> list[Path]:
    root = (config.models_root / equipment_type).resolve()
    if not root.is_dir():
        return []
    out: list[Path] = []
    for child in sorted(root.iterdir()):
        if child.is_dir() and (child / "manifest.json").is_file():
            out.append(child)
    return out


def bootstrap_existing_candidates(
    connection: sqlite3.Connection,
    config: ManagementConfig,
    *,
    equipment_type: str = "main_mill_motor",
) -> list[dict[str, Any]]:
    registered: list[dict[str, Any]] = []
    for path in discover_local_bundles(config, equipment_type):
        try:
            row = register_bundle(
                connection,
                config,
                path,
                expected_equipment_type=equipment_type,
                force_status="candidate",
            )
            registered.append(row)
        except ManagementError:
            continue
    return registered


def get_model(connection: sqlite3.Connection, model_bundle_id: str) -> dict[str, Any]:
    row = connection.execute(
        "SELECT * FROM model_registry WHERE model_bundle_id = ?",
        (model_bundle_id,),
    ).fetchone()
    if row is None:
        raise ManagementError("MODEL_NOT_FOUND", f"模型不存在：{model_bundle_id}", status_code=404)
    return _serialize_model(dict(row))


def get_active_model(connection: sqlite3.Connection, equipment_type: str) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT * FROM model_registry
        WHERE equipment_type = ? AND status = 'active'
        LIMIT 1
        """,
        (equipment_type,),
    ).fetchone()
    return None if row is None else _serialize_model(dict(row))


def list_models(
    connection: sqlite3.Connection,
    *,
    equipment_type: str | None = None,
    status: str | None = None,
    offset: int = 0,
    limit: int = 50,
) -> dict[str, Any]:
    clauses: list[str] = []
    params: list[Any] = []
    if equipment_type:
        clauses.append("equipment_type = ?")
        params.append(equipment_type)
    if status:
        clauses.append("status = ?")
        params.append(status)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    total = int(
        connection.execute(f"SELECT COUNT(*) AS c FROM model_registry{where}", params).fetchone()["c"]
    )
    rows = connection.execute(
        f"""
        SELECT * FROM model_registry{where}
        ORDER BY registered_at DESC, model_bundle_id DESC
        LIMIT ? OFFSET ?
        """,
        [*params, limit, offset],
    ).fetchall()
    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "models": [_serialize_model(dict(row)) for row in rows],
    }


def _parent_from_bundle(bundle_path: str | None) -> str | None:
    if not bundle_path:
        return None
    manifest_path = Path(bundle_path) / "manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(manifest, dict):
        return None
    parent = manifest.get("parent_model_bundle_id")
    text = str(parent).strip() if parent is not None else ""
    return text or None


def _serialize_model(row: dict[str, Any]) -> dict[str, Any]:
    metrics = row.get("metrics_summary_json")
    calibration = row.get("calibration_status_json")
    try:
        metrics_obj = json.loads(metrics) if isinstance(metrics, str) and metrics else {}
    except json.JSONDecodeError:
        metrics_obj = {}
    try:
        calibration_obj = json.loads(calibration) if isinstance(calibration, str) and calibration else {}
    except json.JSONDecodeError:
        calibration_obj = {}
    out = dict(row)
    out["metrics_summary"] = metrics_obj
    out["calibration_status"] = calibration_obj
    out.pop("metrics_summary_json", None)
    out.pop("calibration_status_json", None)
    out["parent_model_bundle_id"] = _parent_from_bundle(out.get("bundle_path"))
    return out
