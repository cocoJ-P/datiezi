"""Active 模型运行时匹配（不允许仅按设备名兼容）。"""

from __future__ import annotations

from typing import Any

from l2.equipment.registry import has_equipment
from l2.inference.model_manager import MODEL_MANAGER
from l2.management.registry import get_active_model, list_models
from l2.management.service import MANAGEMENT_SERVICE


MATCHED = "MATCHED"
NO_ACTIVE_MODEL = "NO_ACTIVE_MODEL"
EQUIPMENT_CONFIG_REQUIRED = "EQUIPMENT_CONFIG_REQUIRED"
DATASET_NOT_READY = "DATASET_NOT_READY"
SCHEMA_INCOMPATIBLE = "SCHEMA_INCOMPATIBLE"


def match_active_for_ped(ped_meta: dict[str, Any]) -> dict[str, Any]:
    """根据 PED 元数据与运行时 Active 判断是否可自动推理。"""
    equipment_type = str(ped_meta.get("equipment_type") or "")
    if not equipment_type or not has_equipment(equipment_type):
        return {
            "match_status": EQUIPMENT_CONFIG_REQUIRED,
            "message": "设备类别未注册或未确认。",
            "equipment_type": equipment_type or None,
            "model_bundle_id": None,
            "candidates": [],
        }
    if not ped_meta.get("inference_ready"):
        return {
            "match_status": DATASET_NOT_READY,
            "message": "PED 尚未满足推理条件。",
            "equipment_type": equipment_type,
            "model_bundle_id": None,
            "candidates": [],
            "details": {"release_status": ped_meta.get("release_status")},
        }

    data_schema = str(ped_meta.get("schema_version") or "")
    connection = MANAGEMENT_SERVICE.connection()
    try:
        active = get_active_model(connection, equipment_type)
        candidates = list_models(
            connection,
            equipment_type=equipment_type,
            status=None,
            offset=0,
            limit=50,
        )
    finally:
        connection.close()

    compatible_inactive = []
    for item in candidates.get("models") or []:
        if item.get("status") == "active":
            continue
        model_schema = str(item.get("feature_schema_version") or "")
        if data_schema and model_schema and model_schema != data_schema:
            continue
        if item.get("status") in {"candidate", "archived"}:
            compatible_inactive.append(
                {
                    "model_bundle_id": item.get("model_bundle_id"),
                    "status": item.get("status"),
                    "feature_schema_version": model_schema,
                }
            )

    if active is None:
        # 兼容：Registry 无 Active，但运行时可能仍加载了 Candidate（开发模式）——不允许自动推理
        return {
            "match_status": NO_ACTIVE_MODEL,
            "message": "设备已注册但没有兼容的 Active 模型。",
            "equipment_type": equipment_type,
            "model_bundle_id": None,
            "candidates": compatible_inactive,
            "details": {
                "runtime_loaded": MODEL_MANAGER.is_ready(equipment_type),
            },
        }

    active_id = str(active["model_bundle_id"])
    active_schema = str(active.get("feature_schema_version") or "")
    if data_schema and active_schema and active_schema != data_schema:
        return {
            "match_status": SCHEMA_INCOMPATIBLE,
            "message": "Active 模型 Feature Schema 与 PED 不兼容。",
            "equipment_type": equipment_type,
            "model_bundle_id": active_id,
            "candidates": compatible_inactive,
            "details": {"model_schema": active_schema, "dataset_schema": data_schema},
        }

    if not MODEL_MANAGER.is_ready(equipment_type):
        return {
            "match_status": NO_ACTIVE_MODEL,
            "message": "Registry 有 Active，但运行时未加载该模型。",
            "equipment_type": equipment_type,
            "model_bundle_id": active_id,
            "candidates": compatible_inactive,
            "details": {"registry_active": active_id, "runtime_loaded": False},
        }

    state = MODEL_MANAGER.require(equipment_type)
    runtime_id = str(state.bundle.manifest.get("model_bundle_id") or "")
    runtime_schema = str(state.bundle.feature_schema.get("version") or "")
    if runtime_id != active_id:
        return {
            "match_status": NO_ACTIVE_MODEL,
            "message": "运行时模型与 Registry Active 不一致，拒绝自动推理。",
            "equipment_type": equipment_type,
            "model_bundle_id": active_id,
            "candidates": compatible_inactive,
            "details": {"registry_active": active_id, "runtime_model_bundle_id": runtime_id},
        }
    if data_schema and runtime_schema and runtime_schema != data_schema:
        return {
            "match_status": SCHEMA_INCOMPATIBLE,
            "message": "运行时 Active 模型 Schema 与 PED 不兼容。",
            "equipment_type": equipment_type,
            "model_bundle_id": runtime_id,
            "candidates": compatible_inactive,
            "details": {"model_schema": runtime_schema, "dataset_schema": data_schema},
        }

    return {
        "match_status": MATCHED,
        "message": "已匹配兼容 Active 模型。",
        "equipment_type": equipment_type,
        "model_bundle_id": runtime_id,
        "candidates": compatible_inactive,
        "details": {
            "schema_version": data_schema,
            "runtime_model_bundle_id": runtime_id,
        },
    }
