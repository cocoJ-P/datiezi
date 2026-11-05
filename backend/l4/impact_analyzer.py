"""ImpactAnalyzer：区分上游依赖 / 下游关联；CONNECTS_TO ≠ 故障传播。"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from l2.inference.config import PROJECT_ROOT
from l4.mapping import resolve_equipment_mapping

MAX_DEPTH = 3


@lru_cache(maxsize=1)
def _load_graph() -> dict[str, Any]:
    path = PROJECT_ROOT / "data" / "processed" / "graph" / "graph.json"
    if not path.exists():
        return {"nodes": [], "edges": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _node_label(nodes: dict[str, dict[str, Any]], node_id: str) -> str:
    node = nodes.get(node_id) or {}
    data = node.get("data") if isinstance(node.get("data"), dict) else {}
    return str(data.get("label") or node.get("label") or node_id)


def _neighbors(graph: dict[str, Any], node_id: str, *, direction: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for edge in graph.get("edges") or []:
        data = edge.get("data") if isinstance(edge.get("data"), dict) else {}
        rel = data.get("relation")
        if rel != "CONNECTS_TO":
            continue
        src, tgt = edge.get("source"), edge.get("target")
        if direction in {"out", "both"} and src == node_id and tgt:
            out.append((str(tgt), "CONNECTS_TO"))
        if direction in {"in", "both"} and tgt == node_id and src:
            out.append((str(src), "CONNECTS_TO_REVERSE"))
    return out


def _walk(
    graph: dict[str, Any],
    start_id: str,
    *,
    direction: str,
    max_depth: int = MAX_DEPTH,
) -> list[dict[str, Any]]:
    nodes = {n["id"]: n for n in graph.get("nodes") or [] if n.get("id")}
    paths: list[dict[str, Any]] = []
    stack: list[tuple[str, list[str], int]] = [(start_id, [start_id], 0)]
    visited_edges: set[tuple[str, str]] = set()

    while stack:
        current, chain, depth = stack.pop()
        if depth >= max_depth:
            continue
        for nxt, rel in _neighbors(graph, current, direction=direction):
            edge_key = (current, nxt)
            if edge_key in visited_edges:
                continue
            if nxt in chain:
                continue
            visited_edges.add(edge_key)
            new_chain = chain + [nxt]
            paths.append(
                {
                    "direction": direction,
                    "graph_edge_direction": "out" if direction == "out" else "in",
                    "depth": depth + 1,
                    "node_ids": new_chain,
                    "labels": [_node_label(nodes, i) for i in new_chain],
                    "relation": rel,
                    "source_edge": "graph.json:CONNECTS_TO",
                    "note": "图谱连接方向 ≠ 故障传播方向",
                }
            )
            stack.append((nxt, new_chain, depth + 1))
    return paths


def _observed_impact(ctx: dict[str, Any]) -> dict[str, Any]:
    bundle = ctx.get("evidence_bundle") or {}
    support = bundle.get("support_evidence") or []
    sufficiency = bundle.get("evidence_sufficiency")
    l2 = ctx.get("l2_context") or {}
    indicators = []
    for row in support:
        if isinstance(row, dict):
            indicators.append(
                {
                    "evidence_id": row.get("evidence_id"),
                    "feature_name": row.get("feature_name"),
                    "assessment": row.get("assessment"),
                }
            )
    return {
        "scope": "self_equipment_only",
        "equipment_id": ctx.get("equipment_id"),
        "equipment_type": ctx.get("equipment_type"),
        "screening_result": l2.get("screening_result"),
        "preliminary_fault": l2.get("preliminary_fault"),
        "evidence_sufficiency": sufficiency,
        "anomaly_indicators": indicators[:20],
        "note": "仅记录本机已保存 L3 证据中的异常指示；不表示其他设备已受影响。",
    }


def _classify_related(
    paths: list[dict[str, Any]],
    *,
    mapping_level: str,
) -> dict[str, list[dict[str, Any]]]:
    upstream: list[dict[str, Any]] = []
    downstream: list[dict[str, Any]] = []
    other: list[dict[str, Any]] = []
    seen: set[str] = set()
    reference_only = mapping_level != "exact_asset"
    binding = "reference_topology_only" if reference_only else "exact_asset_topology"

    for path in paths:
        labels = path.get("labels") or []
        if len(labels) < 2:
            continue
        label = labels[-1]
        node_id = path["node_ids"][-1]
        key = f"{path.get('direction')}:{node_id}"
        if key in seen:
            continue
        seen.add(key)
        item = {
            "graph_label": label,
            "graph_node_id": node_id,
            "relation_direction": path["direction"],
            "graph_edge_direction": path.get("graph_edge_direction"),
            "depth": path["depth"],
            "binding": binding,
            "reference_topology_only": reference_only,
            "category": (
                "downstream_connections"
                if path["direction"] == "out"
                else "upstream_dependencies"
                if path["direction"] == "in"
                else "other_coupled_connections"
            ),
            "note": "CONNECTS_TO 方向仅表示图谱连接，不是已观测传播。",
        }
        if path["direction"] == "out":
            downstream.append(item)
        elif path["direction"] == "in":
            upstream.append(item)
        else:
            other.append(item)
    return {
        "upstream_dependencies": upstream,
        "downstream_connections": downstream,
        "other_coupled_connections": other,
    }


def analyze_impact(ctx: dict[str, Any]) -> dict[str, Any]:
    mapping = resolve_equipment_mapping(
        equipment_id=ctx.get("equipment_id"),
        equipment_type=ctx.get("equipment_type"),
    )
    observed = _observed_impact(ctx)
    graph = _load_graph()

    if mapping.get("mapping_level") == "unresolved" or not mapping.get("graph_node_id"):
        return {
            "observed_impact": observed,
            "potential_impact": {
                "status": "unavailable",
                "title": "潜在关联范围",
                "reason": "equipment_mapping_unresolved",
                "devices": [],
                "upstream_dependencies": [],
                "downstream_connections": [],
                "other_coupled_connections": [],
                "reference_topology_only": True,
            },
            "unknown_impact": {
                "propagation_to_other_assets": "unknown",
                "reason": "无准确设备映射，且缺少其他设备同步观测",
            },
            "related_equipment": [],
            "upstream_dependencies": [],
            "downstream_connections": [],
            "other_coupled_connections": [],
            "topology_paths": [],
            "propagation_status": "unknown",
            "missing_observations": [
                "其他关联设备在同时间窗的有效观测",
                "经人工确认的 exact_asset 图谱映射（如需具体设备级关联）",
            ],
            "evidence_references": [i.get("evidence_id") for i in observed.get("anomaly_indicators") or []],
            "equipment_mapping": mapping,
            "impact_severity_level": None,
            "notes": [
                "本阶段不输出影响程度等级。",
                "mapping_unresolved 时不生成具体设备级影响结论。",
                "标题使用「潜在关联范围」，禁止表述为已影响设备。",
            ],
        }

    start = str(mapping["graph_node_id"])
    out_paths = _walk(graph, start, direction="out")
    in_paths = _walk(graph, start, direction="in")
    classified = _classify_related(
        out_paths + in_paths, mapping_level=str(mapping.get("mapping_level") or "")
    )
    related = (
        classified["upstream_dependencies"]
        + classified["downstream_connections"]
        + classified["other_coupled_connections"]
    )
    reference_only = mapping.get("mapping_level") != "exact_asset"

    return {
        "observed_impact": observed,
        "potential_impact": {
            "status": "topology_reference",
            "title": "潜在关联范围",
            "reference_topology_only": reference_only,
            "devices": related,
            "upstream_dependencies": classified["upstream_dependencies"],
            "downstream_connections": classified["downstream_connections"],
            "other_coupled_connections": classified["other_coupled_connections"],
            "note": "潜在关联来自 CONNECTS_TO 拓扑参考，不是已发生故障传播。"
            + (" 当前为设备类型级参考映射。" if reference_only else ""),
        },
        "unknown_impact": {
            "propagation_to_other_assets": "unknown",
            "reason": "缺少其他设备有效观测与关联验证",
        },
        "related_equipment": related,
        "upstream_dependencies": classified["upstream_dependencies"],
        "downstream_connections": classified["downstream_connections"],
        "other_coupled_connections": classified["other_coupled_connections"],
        "topology_paths": (out_paths + in_paths)[:40],
        "propagation_status": "unknown",
        "missing_observations": [
            "下游关联/上游依赖设备同步状态观测",
            "传播机理的独立验证",
        ],
        "evidence_references": [i.get("evidence_id") for i in observed.get("anomaly_indicators") or []],
        "equipment_mapping": mapping,
        "impact_severity_level": None,
        "notes": [
            "propagation_status 固定为 unknown，除非未来提供他机观测证据。",
            "禁止将拓扑连接解释为 confirmed 传播。",
            "上游依赖与下游关联必须分列，不得统一称为潜在下游影响。",
            "不输出 1–5 级或高/中/低影响等级。",
        ],
    }
