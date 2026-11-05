"""L4 Causal Trace 覆盖层：不修改 L3 ReasoningTrace。"""

from __future__ import annotations

from typing import Any

from l4 import CAUSAL_TRACE_VERSION


def build_causal_trace(
    *,
    candidates: list[dict[str, Any]],
    paths: list[dict[str, Any]],
    impact: dict[str, Any],
    evidence_refs: list[dict[str, Any]],
) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    node_ids: set[str] = set()

    def add_node(node: dict[str, Any]) -> None:
        nid = node["node_id"]
        if nid in node_ids:
            return
        node_ids.add(nid)
        nodes.append(node)

    def add_edge(frm: str, to: str, relation: str) -> None:
        if frm == to:
            return
        edges.append({"from": frm, "to": to, "relation": relation})

    for path in paths:
        pid = path["causal_path_id"]
        cause_id = f"l4:cause:{pid}"
        mech_id = f"l4:mech:{pid}"
        fault_id = f"l4:fault:{pid}"
        add_node(
            {
                "node_id": cause_id,
                "node_type": "ROOT_CAUSE_CANDIDATE",
                "label": path.get("cause"),
                "state": (path.get("link_states") or {}).get("cause", "hypothesized"),
                "source": "causal_knowledge",
                "causal_knowledge_id": path.get("causal_knowledge_id"),
                "fact_claim": False,
            }
        )
        add_node(
            {
                "node_id": mech_id,
                "node_type": "CAUSAL_MECHANISM",
                "label": path.get("mechanism"),
                "state": (path.get("link_states") or {}).get("mechanism", "knowledge_supported"),
                "source": "causal_knowledge",
                "fact_claim": False,
            }
        )
        add_node(
            {
                "node_id": fault_id,
                "node_type": "FAULT_REFERENCE",
                "label": path.get("related_fault_type") or path.get("resulting_fault"),
                "state": (path.get("link_states") or {}).get("fault_to_evidence", "unknown"),
                "source": "l3_fault_candidate",
                "fact_claim": False,
            }
        )
        add_edge(cause_id, mech_id, "MAY_LEAD_TO")
        add_edge(mech_id, fault_id, "MAY_MANIFEST_AS")

        for kid in path.get("source_knowledge_ids") or []:
            kid_node = f"l4:knowledge:{kid}"
            add_node(
                {
                    "node_id": kid_node,
                    "node_type": "KNOWLEDGE_REFERENCE",
                    "label": kid,
                    "state": "knowledge_supported",
                    "source": "knowledge_snapshot_or_catalog",
                    "fact_claim": False,
                }
            )
            add_edge(kid_node, mech_id, "DOCUMENTS")

        for eid in path.get("supporting_evidence_ids") or []:
            ev_node = f"l4:evidence:{eid}"
            add_node(
                {
                    "node_id": ev_node,
                    "node_type": "EVIDENCE_REFERENCE",
                    "label": eid,
                    "state": "observed",
                    "source": "l3_evidence_bundle",
                    "l3_evidence_id": eid,
                    "fact_claim": True,
                }
            )
            add_edge(ev_node, fault_id, "SUPPORTS")

        for eid in path.get("contradicting_evidence_ids") or []:
            ev_node = f"l4:evidence:{eid}"
            add_node(
                {
                    "node_id": ev_node,
                    "node_type": "EVIDENCE_REFERENCE",
                    "label": eid,
                    "state": "contradicted",
                    "source": "l3_evidence_bundle",
                    "l3_evidence_id": eid,
                    "fact_claim": True,
                }
            )
            add_edge(ev_node, fault_id, "CONTRADICTS")

    # 影响路径节点（潜在）
    for idx, path in enumerate((impact.get("topology_paths") or [])[:15]):
        labels = path.get("labels") or []
        if len(labels) < 2:
            continue
        impact_id = f"l4:impact:{idx}"
        add_node(
            {
                "node_id": impact_id,
                "node_type": "IMPACT_PATH",
                "label": " → ".join(labels),
                "state": "unknown",
                "source": "graph_connects_to",
                "propagation_status": "unknown",
                "reference_topology_only": True,
                "fact_claim": False,
            }
        )

    # 无环校验（简单 DFS）
    adjacency: dict[str, list[str]] = {}
    for edge in edges:
        adjacency.setdefault(edge["from"], []).append(edge["to"])

    def has_cycle() -> bool:
        visiting: set[str] = set()
        visited: set[str] = set()

        def dfs(node: str) -> bool:
            if node in visiting:
                return True
            if node in visited:
                return False
            visiting.add(node)
            for nxt in adjacency.get(node, []):
                if dfs(nxt):
                    return True
            visiting.remove(node)
            visited.add(node)
            return False

        return any(dfs(n) for n in list(adjacency))

    return {
        "trace_version": CAUSAL_TRACE_VERSION,
        "nodes": nodes,
        "edges": edges,
        "acyclic": not has_cycle(),
        "candidate_count": len(candidates),
        "notes": [
            "本 Trace 为 L4 覆盖层，不修改 L3 ReasoningTrace。",
            "因果知识节点 fact_claim=false，不得解释为已发生因果事实。",
        ],
    }
