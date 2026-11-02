"""L2 已发布 PreparedEquipmentDataset 目录。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from l2.prepared.contract import read_manifest
from l2.prepared.publisher import prepared_root


def list_published_datasets(
    *,
    equipment_type: str | None = None,
    training_ready: bool | None = None,
) -> list[dict[str, Any]]:
    index_path = prepared_root() / "index.json"
    items: list[dict[str, Any]] = []
    if index_path.is_file():
        raw = json.loads(index_path.read_text(encoding="utf-8"))
        items = list(raw)
    else:
        # 扫描目录恢复
        root = prepared_root()
        for manifest in root.glob("*/*/manifest.json"):
            body = read_manifest(manifest)
            body["bundle_dir"] = str(manifest.parent)
            items.append(body)

    out = []
    for item in items:
        if equipment_type and item.get("equipment_type") != equipment_type:
            continue
        if training_ready is not None and bool(item.get("training_ready")) != training_ready:
            continue
        out.append(_public(item))
    return out


def get_published_dataset(prepared_dataset_id: str) -> dict[str, Any]:
    for item in list_published_datasets():
        if item["prepared_dataset_id"] == prepared_dataset_id:
            # 附加完整 manifest
            bundle = Path(item["bundle_dir"])
            manifest = read_manifest(bundle / "manifest.json")
            return {**_public(manifest | {"bundle_dir": str(bundle)}), "manifest": manifest}
    raise KeyError(prepared_dataset_id)


def resolve_bundle_dir(prepared_dataset_id: str) -> Path:
    meta = get_published_dataset(prepared_dataset_id)
    path = Path(meta["bundle_dir"])
    if not path.is_dir():
        raise FileNotFoundError(prepared_dataset_id)
    return path


def _public(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "prepared_dataset_id": item.get("prepared_dataset_id"),
        "dataset_id": item.get("dataset_id"),
        "revision_id": item.get("revision_id"),
        "equipment_type": item.get("equipment_type"),
        "schema_version": item.get("schema_version"),
        "source_type": item.get("source_type", "l1_prepared"),
        "source_stage": item.get("source_stage"),
        "release_level": item.get("release_level"),
        "release_status": item.get("release_status"),
        "training_ready": bool(item.get("training_ready")),
        "inference_ready": bool(item.get("inference_ready")),
        "record_count": item.get("record_count"),
        "feature_count": item.get("feature_count"),
        "label_count": item.get("label_count"),
        "warning_count": item.get("warning_count", 0),
        "l1_prepared_version_id": item.get("l1_prepared_version_id"),
        "bundle_dir": item.get("bundle_dir"),
        "created_at": item.get("created_at"),
    }
