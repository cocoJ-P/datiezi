"""将 L1 PD-* 发布为不可变 L2 PreparedEquipmentDataset 快照。"""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

import pandas as pd

from l2.equipment.registry import get_equipment_spec
from l2.inference.config import PROJECT_ROOT
from l2.prepared.contract import PreparedManifest, sha256_file, utc_now
from l2.prepared.l1_source import (
    get_l1_prepared_meta,
    get_prepare_source_revisions,
    iter_l1_prepared_rows,
)
from l2.prepared.mapping import map_l1_features_to_spec, resolve_equipment_type

DEFAULT_ROOT = PROJECT_ROOT / "data" / "runtime" / "l2_prepared"


class PreparedPublishError(Exception):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def prepared_root() -> Path:
    import os

    raw = os.getenv("L2_PREPARED_ROOT", "").strip()
    path = Path(raw) if raw else DEFAULT_ROOT
    if not path.is_absolute():
        path = (PROJECT_ROOT / path).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def publish_from_l1_prepared(
    l1_version_id: str,
    *,
    equipment_type: str | None = None,
    release_mode: str = "training_and_inference",
) -> dict[str, Any]:
    meta = get_l1_prepared_meta(l1_version_id)
    # 取一行探测设备类别
    probe_category = None
    for row in iter_l1_prepared_rows(l1_version_id):
        probe_category = row.get("equipment_category")
        break
    mapping = resolve_equipment_type(
        dataset_id=meta.dataset_id,
        original_filename=meta.original_filename,
        equipment_category=probe_category,
    )
    if equipment_type:
        if mapping.equipment_type and mapping.equipment_type != equipment_type:
            raise PreparedPublishError(
                "EQUIPMENT_MAPPING_CONFLICT",
                f"请求设备 {equipment_type} 与自动映射 {mapping.equipment_type} 不一致。",
            )
        mapping_equipment = equipment_type
    else:
        mapping_equipment = mapping.equipment_type
    if not mapping_equipment or mapping.mapping_status != "confirmed":
        raise PreparedPublishError(
            "EQUIPMENT_CONFIG_REQUIRED",
            "设备类型未确认，无法发布到 L2。",
            details={"mapping": mapping.__dict__},
        )

    spec = get_equipment_spec(mapping_equipment)
    source_revisions = get_prepare_source_revisions(meta.prepare_run_id)
    source_revisions["p0_sha256"] = meta.p0_sha256
    source_revisions["l1_prepared_version_id"] = meta.version_id

    feature_rows: list[dict[str, Any]] = []
    label_rows: list[dict[str, Any]] = []
    meta_rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    excluded = 0
    label_values: set[str] = set()

    bridge_notes: list[str] = []
    for row in iter_l1_prepared_rows(l1_version_id):
        mapped, missing, applied = map_l1_features_to_spec(row["input_features"], spec)
        if missing:
            excluded += 1
            continue
        for note in applied:
            if note not in bridge_notes:
                bridge_notes.append(note)
        fault_cell = (row.get("labels") or {}).get("fault_type") or {}
        fault_label = None
        if isinstance(fault_cell, dict):
            fault_label = fault_cell.get("value") or fault_cell.get("text")
        has_label = fault_label is not None and str(fault_label).strip() != ""
        record_id = str(row["record_id"])
        feature_rows.append({spec.record_id_field: record_id, **mapped})
        label_rows.append(
            {
                spec.record_id_field: record_id,
                spec.target_field: str(fault_label).strip() if has_label else None,
            }
        )
        if has_label:
            label_values.add(str(fault_label).strip())
        meta_rows.append(
            {
                "record_id": record_id,
                "equipment_id": row.get("equipment_id"),
                "equipment_type": mapping_equipment,
                "collection_time": row.get("timestamp_text"),
                "source_row_id": row.get("source_row_id"),
                "l1_prepared_version_id": meta.version_id,
                "has_label": has_label,
            }
        )
    if bridge_notes:
        warnings.extend(bridge_notes)

    if not feature_rows:
        raise PreparedPublishError(
            "SCHEMA_INCOMPATIBLE",
            "没有可映射到 EquipmentSpec 的有效特征行。",
            details={"excluded": excluded, "equipment_type": mapping_equipment},
        )

    labeled = [r for r in label_rows if r[spec.target_field]]
    illegal = sorted(set(label_values) - set(spec.class_labels))
    if illegal:
        warnings.append(f"存在 EquipmentSpec 外标签：{illegal}")
    training_ready = bool(labeled) and not illegal and len(label_values & set(spec.class_labels)) >= 2
    if labeled and "正常" not in label_values and spec.normal_label not in label_values:
        training_ready = False
        warnings.append("缺少正常类别标签，不能训练。")
    inference_ready = True
    if release_mode == "training" and not training_ready:
        raise PreparedPublishError(
            "NOT_TRAINING_READY",
            "数据集不满足训练发布条件。",
            details={"warnings": warnings, "labeled": len(labeled)},
        )
    if release_mode == "inference" and not inference_ready:
        raise PreparedPublishError("NOT_INFERENCE_READY", "数据集不满足推理发布条件。")

    # 幂等：相同 L1 PD + equipment + schema + content → 复用
    features_df = pd.DataFrame(feature_rows)
    labels_df = pd.DataFrame(label_rows)
    metadata_df = pd.DataFrame(meta_rows)
    content_key = hashlib.sha256(
        (
            meta.version_id
            + "|"
            + mapping_equipment
            + "|"
            + spec.schema_version
            + "|"
            + features_df.to_csv(index=False)
            + "|"
            + labels_df.to_csv(index=False)
        ).encode("utf-8")
    ).hexdigest()
    existing = _find_by_content_key(content_key)
    if existing is not None:
        return {"reused": True, **existing}

    prepared_dataset_id = "PED-" + uuid.uuid4().hex[:12]
    revision_id = f"rev-{meta.version_id}"
    out_dir = prepared_root() / mapping_equipment / prepared_dataset_id
    out_dir.mkdir(parents=True, exist_ok=False)

    features_path = out_dir / "features.csv"
    labels_path = out_dir / "labels.csv"
    metadata_path = out_dir / "metadata.csv"
    schema_path = out_dir / "feature_schema.json"
    validation_path = out_dir / "validation_report.json"
    features_df.to_csv(features_path, index=False, encoding="utf-8-sig")
    labels_df.to_csv(labels_path, index=False, encoding="utf-8-sig")
    metadata_df.to_csv(metadata_path, index=False, encoding="utf-8-sig")
    schema_path.write_text(
        json.dumps(spec.feature_schema_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    validation = {
        "excluded_record_count": excluded,
        "warnings": warnings,
        "illegal_labels": illegal,
        "labeled_record_count": len(labeled),
        "label_values": sorted(label_values),
        "content_key": content_key,
        "fault_classification_readiness": (meta.readiness or {}).get("fault_classification"),
    }
    validation_path.write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8")

    release_status = "ready" if (training_ready or inference_ready) and not illegal else "needs_review"
    if warnings and release_status == "ready":
        release_status = "ready"

    manifest = PreparedManifest(
        prepared_dataset_id=prepared_dataset_id,
        dataset_id=meta.dataset_id,
        revision_id=revision_id,
        equipment_type=mapping_equipment,
        equipment_group_id=mapping.equipment_group_id,
        schema_version=spec.schema_version,
        source_stage="P8",
        source_revision_ids=source_revisions,
        created_at=utc_now(),
        record_count=len(feature_rows),
        trainable_record_count=len(labeled),
        feature_count=spec.feature_count,
        label_count=len(label_values),
        class_labels=list(spec.class_labels),
        release_status=release_status,
        release_level="development" if (meta.readiness or {}).get("fault_classification") != "ready" else "official",
        training_ready=training_ready,
        inference_ready=inference_ready,
        source_type="l1_prepared",
        l1_prepared_version_id=meta.version_id,
        validation_summary=validation,
        content_hashes={
            "features.csv": sha256_file(features_path),
            "labels.csv": sha256_file(labels_path),
            "metadata.csv": sha256_file(metadata_path),
            "content_key": content_key,
        },
        artifact_files={
            "features": _artifact_ref(features_path),
            "labels": _artifact_ref(labels_path),
            "metadata": _artifact_ref(metadata_path),
            "feature_schema": _artifact_ref(schema_path),
            "validation_report": _artifact_ref(validation_path),
        },
        warning_count=len(warnings),
        excluded_record_count=excluded,
        notes=[
            f"sourced_from={meta.version_id}",
            f"original_filename={meta.original_filename}",
        ],
    )
    manifest_path = out_dir / "manifest.json"
    manifest.write(manifest_path)
    _index_append(manifest.to_dict() | {"manifest_hash": manifest.manifest_hash(), "bundle_dir": str(out_dir)})
    return {
        "reused": False,
        "prepared_dataset_id": prepared_dataset_id,
        "revision_id": revision_id,
        "equipment_type": mapping_equipment,
        "schema_version": spec.schema_version,
        "training_ready": training_ready,
        "inference_ready": inference_ready,
        "release_status": release_status,
        "record_count": len(feature_rows),
        "feature_count": spec.feature_count,
        "label_count": len(label_values),
        "bundle_dir": str(out_dir),
        "manifest_path": str(manifest_path),
        "l1_prepared_version_id": meta.version_id,
    }


def _artifact_ref(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT.resolve())).replace("\\", "/")
    except ValueError:
        # 测试或自定义 L2_PREPARED_ROOT 可能在仓库外
        return path.name


def _index_path() -> Path:
    return prepared_root() / "index.json"


def _index_append(item: dict[str, Any]) -> None:
    path = _index_path()
    items = []
    if path.is_file():
        items = json.loads(path.read_text(encoding="utf-8"))
    items = [x for x in items if x.get("prepared_dataset_id") != item.get("prepared_dataset_id")]
    items.insert(0, item)
    path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def _find_by_content_key(content_key: str) -> dict[str, Any] | None:
    path = _index_path()
    if not path.is_file():
        return None
    items = json.loads(path.read_text(encoding="utf-8"))
    for item in items:
        hashes = item.get("content_hashes") or {}
        if hashes.get("content_key") == content_key:
            return {
                "prepared_dataset_id": item["prepared_dataset_id"],
                "revision_id": item["revision_id"],
                "equipment_type": item["equipment_type"],
                "schema_version": item["schema_version"],
                "training_ready": item.get("training_ready"),
                "inference_ready": item.get("inference_ready"),
                "release_status": item.get("release_status"),
                "record_count": item.get("record_count"),
                "feature_count": item.get("feature_count"),
                "label_count": item.get("label_count"),
                "bundle_dir": item.get("bundle_dir"),
                "manifest_path": str(Path(item["bundle_dir"]) / "manifest.json") if item.get("bundle_dir") else None,
                "l1_prepared_version_id": item.get("l1_prepared_version_id"),
            }
    return None
