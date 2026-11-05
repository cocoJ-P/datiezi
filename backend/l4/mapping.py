"""设备身份映射：区分 exact_asset / equipment_type / unresolved。"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from l4 import EQUIPMENT_MAPPING_VERSION

_PATH = Path(__file__).resolve().parent / "equipment_mapping" / "mappings_v1.json"


@lru_cache(maxsize=1)
def _load() -> dict[str, Any]:
    return json.loads(_PATH.read_text(encoding="utf-8"))


def mapping_version() -> str:
    return str(_load().get("equipment_mapping_version") or EQUIPMENT_MAPPING_VERSION)


def resolve_equipment_mapping(
    *,
    equipment_id: str | None,
    equipment_type: str | None,
) -> dict[str, Any]:
    data = _load()
    eq_id = (equipment_id or "").strip()
    eq_type = (equipment_type or "").strip()

    asset = None
    for row in data.get("asset_mappings") or []:
        if eq_id and row.get("equipment_id") == eq_id:
            asset = dict(row)
            break

    type_row = None
    for row in data.get("type_mappings") or []:
        if eq_type and row.get("equipment_type") == eq_type:
            type_row = dict(row)
            break

    if asset and asset.get("mapping_level") == "exact_asset" and asset.get("graph_node_id"):
        return {
            "equipment_id": eq_id or None,
            "equipment_type": eq_type or asset.get("equipment_type"),
            "graph_node_id": asset.get("graph_node_id"),
            "graph_label": asset.get("graph_label"),
            "mapping_level": "exact_asset",
            "mapping_source": asset.get("mapping_source"),
            "verification_status": asset.get("verification_status"),
            "version": mapping_version(),
            "notes": asset.get("notes"),
            "type_fallback": None,
        }

    # 资产未精确绑定：允许类型级参考，但显式标记
    if type_row and type_row.get("mapping_level") == "equipment_type" and type_row.get("graph_node_id"):
        return {
            "equipment_id": eq_id or None,
            "equipment_type": eq_type or type_row.get("equipment_type"),
            "graph_node_id": type_row.get("graph_node_id"),
            "graph_label": type_row.get("graph_label"),
            "mapping_level": "equipment_type",
            "mapping_source": type_row.get("mapping_source"),
            "verification_status": type_row.get("verification_status"),
            "version": mapping_version(),
            "notes": (asset or {}).get("notes") or type_row.get("notes"),
            "asset_mapping": asset,
            "reference_topology_only": True,
        }

    return {
        "equipment_id": eq_id or None,
        "equipment_type": eq_type or None,
        "graph_node_id": None,
        "graph_label": None,
        "mapping_level": "unresolved",
        "mapping_source": (type_row or asset or {}).get("mapping_source") or "unmapped",
        "verification_status": "unresolved",
        "version": mapping_version(),
        "notes": (type_row or asset or {}).get("notes") or "无可用图谱设备映射",
        "reference_topology_only": True,
        "type_row": type_row,
        "asset_mapping": asset,
    }
