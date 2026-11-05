"""基于现有 graph.json 的设备/故障图谱查询（只读，不写回）。"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from l3.knowledge import GRAPH_QUERY_VERSION
from l3.knowledge.paths import GRAPH_PATH
from l3.knowledge.schema import scopes_for_equipment_type


class GraphQueryError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def load_graph() -> dict[str, Any]:
    if not GRAPH_PATH.is_file():
        raise GraphQueryError("未找到 graph.json")
    payload = json.loads(GRAPH_PATH.read_text(encoding="utf-8"))
    nodes = payload.get("nodes") or []
    edges = payload.get("edges") or []
    by_id = {n["id"]: n for n in nodes}
    return {"nodes": nodes, "edges": edges, "by_id": by_id, "path": str(GRAPH_PATH)}


def graph_status() -> dict[str, Any]:
    try:
        g = load_graph()
    except GraphQueryError as exc:
        return {"available": False, "error": str(exc), "version": GRAPH_QUERY_VERSION}
    types: dict[str, int] = {}
    rels: dict[str, int] = {}
    for n in g["nodes"]:
        t = (n.get("data") or {}).get("type") or "unknown"
        types[t] = types.get(t, 0) + 1
    for e in g["edges"]:
        r = (e.get("data") or {}).get("relation") or "unknown"
        rels[r] = rels.get(r, 0) + 1
    return {
        "available": True,
        "version": GRAPH_QUERY_VERSION,
        "path": g["path"],
        "entity_count": len(g["nodes"]),
        "relationship_count": len(g["edges"]),
        "entity_types": types,
        "relationship_types": rels,
    }


def _label(node: dict[str, Any]) -> str:
    return str((node.get("data") or {}).get("label") or node.get("id"))


def _type(node: dict[str, Any]) -> str:
    return str((node.get("data") or {}).get("type") or "unknown")


def _refs(obj: dict[str, Any]) -> list[dict[str, Any]]:
    return list(((obj.get("data") or {}).get("source_refs")) or [])


def find_nodes(*, node_type: str | None = None, label_contains: str | None = None) -> list[dict[str, Any]]:
    g = load_graph()
    out = []
    for node in g["nodes"]:
        if node_type and _type(node) != node_type:
            continue
        if label_contains and label_contains not in _label(node):
            continue
        out.append(
            {
                "node_id": node["id"],
                "entity_type": _type(node),
                "canonical_name": _label(node),
                "source_refs": _refs(node),
            }
        )
    return out


def neighbors(node_id: str, *, relation: str | None = None, direction: str = "out") -> list[dict[str, Any]]:
    g = load_graph()
    if node_id not in g["by_id"]:
        return []
    hits = []
    for edge in g["edges"]:
        rel = (edge.get("data") or {}).get("relation")
        if relation and rel != relation:
            continue
        if direction in {"out", "both"} and edge.get("source") == node_id:
            tid = edge.get("target")
            target = g["by_id"].get(tid)
            if target:
                hits.append(
                    {
                        "relationship_id": edge.get("id"),
                        "relationship_type": rel,
                        "source_node_id": node_id,
                        "target_node_id": tid,
                        "target": {
                            "node_id": tid,
                            "entity_type": _type(target),
                            "canonical_name": _label(target),
                        },
                        "knowledge_source": _refs(edge),
                        "version": GRAPH_QUERY_VERSION,
                    }
                )
        if direction in {"in", "both"} and edge.get("target") == node_id:
            sid = edge.get("source")
            source = g["by_id"].get(sid)
            if source:
                hits.append(
                    {
                        "relationship_id": edge.get("id"),
                        "relationship_type": rel,
                        "source_node_id": sid,
                        "target_node_id": node_id,
                        "target": {
                            "node_id": sid,
                            "entity_type": _type(source),
                            "canonical_name": _label(source),
                        },
                        "knowledge_source": _refs(edge),
                        "version": GRAPH_QUERY_VERSION,
                    }
                )
    return hits


def query_equipment_components(equipment_type: str) -> dict[str, Any]:
    aliases = scopes_for_equipment_type(equipment_type)
    devices = []
    for alias in aliases:
        devices.extend(find_nodes(node_type="device", label_contains=alias))
    # 去重
    seen = set()
    uniq = []
    for d in devices:
        if d["node_id"] in seen:
            continue
        seen.add(d["node_id"])
        uniq.append(d)
    components = []
    paths = []
    for device in uniq:
        for rel in neighbors(device["node_id"], relation="HAS_COMPONENT", direction="out"):
            components.append(rel["target"])
            paths.append(
                {
                    "path": [device["canonical_name"], "HAS_COMPONENT", rel["target"]["canonical_name"]],
                    "relationship_id": rel["relationship_id"],
                    "source_refs": rel["knowledge_source"],
                }
            )
    return {
        "equipment_type": equipment_type,
        "matched_devices": uniq,
        "components": components,
        "paths": paths[:50],
        "note": "图谱关系不表示当前故障已发生",
    }


def query_fault_features(fault_mode: str) -> dict[str, Any]:
    faults = find_nodes(node_type="fault", label_contains=fault_mode)
    features = []
    paths = []
    for fault in faults:
        for rel in neighbors(fault["node_id"], relation="HAS_FEATURE", direction="out"):
            features.append(rel["target"])
            paths.append(
                {
                    "path": [fault["canonical_name"], "HAS_FEATURE", rel["target"]["canonical_name"]],
                    "relationship_id": rel["relationship_id"],
                    "source_refs": rel["knowledge_source"],
                }
            )
        # 反向：feature 也可能通过其他关系关联；同时查 OCCURS_ON 入边设备
    return {
        "fault_mode": fault_mode,
        "matched_faults": faults,
        "features": features,
        "paths": paths[:50],
        "note": "典型特征为机理参考，需数值证据支持后才构成 SUPPORT",
    }


def query_fault_cases(fault_mode: str) -> dict[str, Any]:
    cases = find_nodes(node_type="case", label_contains=fault_mode)
    # 也按 OCCURS_ON 反查
    faults = find_nodes(node_type="fault", label_contains=fault_mode)
    linked = []
    for fault in faults:
        for rel in neighbors(fault["node_id"], relation="OCCURS_ON", direction="in"):
            if rel["target"]["entity_type"] == "case":
                linked.append(rel)
    return {
        "fault_mode": fault_mode,
        "cases": cases,
        "linked_via_occurs_on": linked[:30],
    }


def query_rules_for_equipment(equipment_type: str) -> dict[str, Any]:
    aliases = scopes_for_equipment_type(equipment_type)
    params = []
    for alias in aliases:
        params.extend(find_nodes(node_type="parameter", label_contains=alias.split()[0] if alias else ""))
    # 更稳妥：取设备 HAS_PARAMETER
    devices = []
    for alias in aliases:
        devices.extend(find_nodes(node_type="device", label_contains=alias))
    rules = []
    paths = []
    seen = set()
    for device in devices:
        if device["node_id"] in seen:
            continue
        seen.add(device["node_id"])
        for rel in neighbors(device["node_id"], relation="HAS_PARAMETER", direction="out"):
            rules.append(rel["target"])
            paths.append(
                {
                    "path": [device["canonical_name"], "HAS_PARAMETER", rel["target"]["canonical_name"]],
                    "relationship_id": rel["relationship_id"],
                    "source_refs": rel["knowledge_source"],
                    "applicability": "equipment_scoped",
                }
            )
    return {
        "equipment_type": equipment_type,
        "parameters": rules,
        "paths": paths[:50],
        "note": "设备规程优先于通用经验；不完整规则不得自动补齐",
    }
