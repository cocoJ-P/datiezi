"""L4 Reasoning 只读工具白名单。"""

from __future__ import annotations

import json
from typing import Any

from l4.causal_knowledge.loader import get_catalog, list_entries_for_fault
from l4.mapping import resolve_equipment_mapping
from l4.reasoning import TOOL_REGISTRY_VERSION
from l4.reasoning.path_inspector import inspect_causal_path

MAX_RESULT_CHARS = 4500


def _truncate(obj: Any, *, limit: int = MAX_RESULT_CHARS) -> Any:
    text = json.dumps(obj, ensure_ascii=False)
    if len(text) <= limit:
        return obj
    return {"truncated": True, "preview": text[:limit], "note": "工具返回已截断；请用 ID 引用，勿复述全文"}


def _error(tool: str, message: str) -> dict[str, Any]:
    return {"ok": False, "tool": tool, "error": message}


def cache_key(name: str, args: dict[str, Any]) -> str:
    normalized = {k: args[k] for k in sorted(args) if args[k] is not None}
    return f"{name}:{json.dumps(normalized, ensure_ascii=False, sort_keys=True, default=str)}"


def _slim_candidate(c: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate_id": c.get("candidate_id"),
        "cause_name": c.get("cause_name"),
        "causal_path_id": c.get("causal_path_id"),
        "causal_knowledge_id": c.get("causal_knowledge_id"),
        "related_fault_type": c.get("related_fault_type"),
        "applicability_status": c.get("applicability_status"),
        "same_layer_as_resulting_fault": c.get("same_layer_as_resulting_fault"),
        "supporting_evidence_ids": list(c.get("supporting_evidence_ids") or []),
        "fault_manifestation_evidence_ids": list(c.get("fault_manifestation_evidence_ids") or []),
        "contradicting_evidence_ids": list(c.get("contradicting_evidence_ids") or []),
        "missing_evidence": list(c.get("missing_evidence") or [])[:8],
    }


def _slim_excluded(ex: dict[str, Any]) -> dict[str, Any]:
    return {
        "fault_type": ex.get("fault_type"),
        "eligibility": ex.get("eligibility"),
        "exclusion_reason": ex.get("exclusion_reason") or ex.get("reason"),
        "contradicting_evidence_ids": list(ex.get("contradicting_evidence_ids") or [])[:8],
    }


def _slim_evidence_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "evidence_id": item.get("evidence_id"),
        "feature_name": item.get("feature_name") or item.get("feature_id"),
        "observed_value": item.get("observed_value"),
        "assessment": item.get("assessment"),
        "related_fault_mode": item.get("related_fault_mode"),
        "analyzer_id": item.get("analyzer_id"),
    }


def _slim_ck_entry(e: dict[str, Any]) -> dict[str, Any]:
    return {
        "causal_knowledge_id": e.get("causal_knowledge_id"),
        "cause": e.get("cause") or e.get("cause_name"),
        "mechanism": e.get("mechanism"),
        "resulting_fault": e.get("resulting_fault") or e.get("fault_type"),
        "validation_status": e.get("validation_status"),
        "same_layer_as_resulting_fault": e.get("same_layer_as_resulting_fault"),
        "source_document_ids": [
            (loc.get("document") if isinstance(loc, dict) else loc)
            for loc in (e.get("source_document_locations") or e.get("sources") or [])[:4]
        ],
        "note": "知识≠本机已发生",
    }


TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_l4_candidates",
            "description": "读取 L4 Core 有效根因候选摘要、排除故障与证据 ID。只读。",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_l3_evidence",
            "description": "读取真实 L3 EvidenceItem 摘要与诊断限制。只引用 evidence_id。",
            "parameters": {
                "type": "object",
                "properties": {"include_items": {"type": "boolean"}},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_causal_knowledge",
            "description": "读取指定 CausalKnowledgeEntry 摘要。知识≠本机事实。",
            "parameters": {
                "type": "object",
                "properties": {
                    "causal_knowledge_id": {"type": "string"},
                    "fault_type": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_fault_knowledge",
            "description": "检索 L3 Knowledge 故障机理/案例摘要。案例≠当前观测。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "fault_mode": {"type": "string"},
                    "top_k": {"type": "integer"},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_equipment_dependencies",
            "description": "读取设备映射与上下游拓扑参考。CONNECTS_TO≠传播。",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "inspect_causal_path",
            "description": "服务端确定性逐环节检验 Cause→Mechanism→Fault→Observation。",
            "parameters": {
                "type": "object",
                "properties": {"causal_path_id": {"type": "string"}},
                "additionalProperties": False,
            },
        },
    },
]

REGISTERED = {t["function"]["name"] for t in TOOLS}


class ToolContext:
    def __init__(self, reasoning_input: dict[str, Any]):
        self.inp = reasoning_input
        self.call_count = 0
        self.unique_exec_count = 0
        self.cache_hits = 0
        self.history: list[dict[str, Any]] = []
        self._cache: dict[str, dict[str, Any]] = {}

    def get_l4_candidates(self) -> dict[str, Any]:
        a = self.inp.get("root_cause_assessment") or {}
        return _truncate(
            {
                "ok": True,
                "tool": "get_l4_candidates",
                "tool_registry_version": TOOL_REGISTRY_VERSION,
                "active_candidates": [_slim_candidate(c) for c in (a.get("root_cause_candidates") or [])],
                "reference_candidate_ids": [
                    c.get("candidate_id") for c in (a.get("reference_candidates") or []) if c.get("candidate_id")
                ],
                "excluded_faults": [_slim_excluded(x) for x in (a.get("excluded_faults") or [])],
                "excluded_by_applicability": [
                    _slim_excluded(x) if isinstance(x, dict) else {"fault_type": x}
                    for x in (a.get("excluded_by_applicability") or [])
                ],
                "root_cause_confirmation": a.get("root_cause_confirmation") or "unconfirmed",
                "note": "不得自动恢复 excluded；最终 JSON 只引用 candidate_id / evidence_id",
            }
        )

    def get_l3_evidence(self, include_items: bool = True) -> dict[str, Any]:
        bundle = self.inp.get("evidence_bundle") or {}
        items = bundle.get("evidence_items") or []
        payload: dict[str, Any] = {
            "ok": True,
            "tool": "get_l3_evidence",
            "evidence_sufficiency": bundle.get("evidence_sufficiency"),
            "support_evidence_ids": [
                (x.get("evidence_id") if isinstance(x, dict) else x) for x in (bundle.get("support_evidence") or [])
            ][:40],
            "contradict_evidence_ids": [
                (x.get("evidence_id") if isinstance(x, dict) else x) for x in (bundle.get("contradict_evidence") or [])
            ][:40],
            "missing_evidence": list(bundle.get("missing_evidence") or [])[:20],
            "analysis_limitations": list(bundle.get("analysis_limitations") or [])[:12],
            "allowed_evidence_ids": list(self.inp.get("allowed_evidence_ids") or [])[:80],
        }
        if include_items:
            payload["evidence_items"] = [_slim_evidence_item(i) for i in items if isinstance(i, dict)][:60]
        return _truncate(payload)

    def get_causal_knowledge(
        self, *, causal_knowledge_id: str | None = None, fault_type: str | None = None
    ) -> dict[str, Any]:
        catalog = get_catalog()
        entries = list(catalog.get("entries") or [])
        if causal_knowledge_id:
            entries = [e for e in entries if e.get("causal_knowledge_id") == causal_knowledge_id]
        elif fault_type:
            entries = list_entries_for_fault(fault_type)
        allowed = set(self.inp.get("allowed_causal_knowledge_ids") or [])
        if allowed:
            entries = [e for e in entries if e.get("causal_knowledge_id") in allowed]
        return _truncate(
            {
                "ok": True,
                "tool": "get_causal_knowledge",
                "causal_knowledge_version": catalog.get("causal_knowledge_version"),
                "entries": [_slim_ck_entry(e) for e in entries],
                "note": "知识条目仅为候选机理，不等于当前设备已发生",
            }
        )

    def get_fault_knowledge(
        self, *, query: str, fault_mode: str | None = None, top_k: int = 5
    ) -> dict[str, Any]:
        try:
            from l3.knowledge.retrieval import hybrid_search

            hits = hybrid_search(query, fault_mode=fault_mode, top_k=min(int(top_k or 5), 5))
            slim_hits = []
            for h in hits or []:
                if not isinstance(h, dict):
                    continue
                slim_hits.append(
                    {
                        "title": h.get("title") or h.get("doc_id") or h.get("id"),
                        "fault_mode": h.get("fault_mode"),
                        "score": h.get("score"),
                        "snippet": str(h.get("snippet") or h.get("text") or "")[:240],
                    }
                )
            return _truncate(
                {
                    "ok": True,
                    "tool": "get_fault_knowledge",
                    "hits": slim_hits,
                    "note": "案例/文档相似≠本机根因相同",
                }
            )
        except Exception as exc:  # noqa: BLE001
            enrich = self.inp.get("knowledge_context") or {}
            return {
                "ok": True,
                "tool": "get_fault_knowledge",
                "hits": enrich.get("knowledge_references") or [],
                "fallback": True,
                "error": str(exc),
                "note": "检索不可用时仅返回已绑定 enrichment 摘要",
            }

    def get_equipment_dependencies(self) -> dict[str, Any]:
        a = self.inp.get("root_cause_assessment") or {}
        impact = a.get("impact_assessment") or {}
        mapping = resolve_equipment_mapping(
            equipment_id=self.inp.get("equipment_id"),
            equipment_type=self.inp.get("equipment_type"),
        )
        slim_mapping = {
            "mapping_level": mapping.get("mapping_level") if isinstance(mapping, dict) else None,
            "equipment_type": mapping.get("equipment_type") if isinstance(mapping, dict) else None,
            "equipment_id": mapping.get("equipment_id") if isinstance(mapping, dict) else None,
            "resolved": mapping.get("resolved") if isinstance(mapping, dict) else None,
            "upstream_labels": [
                (x.get("graph_label") if isinstance(x, dict) else x)
                for x in ((mapping.get("upstream") if isinstance(mapping, dict) else None) or [])
            ][:12],
            "downstream_labels": [
                (x.get("graph_label") if isinstance(x, dict) else x)
                for x in ((mapping.get("downstream") if isinstance(mapping, dict) else None) or [])
            ][:12],
        }
        return _truncate(
            {
                "ok": True,
                "tool": "get_equipment_dependencies",
                "equipment_mapping": slim_mapping,
                "impact_assessment": {
                    "propagation_status": impact.get("propagation_status"),
                    "mapping_level": (impact.get("equipment_mapping") or {}).get("mapping_level"),
                    "upstream_count": len(impact.get("upstream_dependencies") or []),
                    "downstream_count": len(impact.get("downstream_connections") or []),
                    "reference_topology_only": (impact.get("potential_impact") or {}).get("reference_topology_only"),
                    "impact_degree": None,
                },
                "note": "类型级拓扑仅为参考；不得宣称已传播",
            }
        )

    def inspect_causal_path(self, *, causal_path_id: str | None = None) -> dict[str, Any]:
        a = self.inp.get("root_cause_assessment") or {}
        return _truncate(inspect_causal_path(a, causal_path_id=causal_path_id))


def execute_tool(name: str, arguments: Any, ctx: ToolContext) -> dict[str, Any]:
    args = arguments if isinstance(arguments, dict) else {}
    if isinstance(arguments, str):
        try:
            args = json.loads(arguments or "{}")
        except json.JSONDecodeError:
            args = {}

    key = cache_key(name, args if isinstance(args, dict) else {})
    if key in ctx._cache:
        ctx.cache_hits += 1
        ctx.call_count += 1
        cached = dict(ctx._cache[key])
        cached["cached"] = True
        ctx.history.append(
            {
                "tool": name,
                "arguments": args,
                "result_ok": bool(cached.get("ok")),
                "cached": True,
                "result": {"ok": cached.get("ok"), "tool": name, "cached": True},
            }
        )
        return cached

    ctx.call_count += 1
    if name not in REGISTERED:
        result = _error(name, "unknown_or_forbidden_tool")
        ctx.history.append({"tool": name, "arguments": args, "result_ok": False, "result": result})
        return result

    try:
        if name == "get_l4_candidates":
            result = ctx.get_l4_candidates()
        elif name == "get_l3_evidence":
            result = ctx.get_l3_evidence(include_items=bool(args.get("include_items", True)))
        elif name == "get_causal_knowledge":
            result = ctx.get_causal_knowledge(
                causal_knowledge_id=args.get("causal_knowledge_id"),
                fault_type=args.get("fault_type"),
            )
        elif name == "get_fault_knowledge":
            result = ctx.get_fault_knowledge(
                query=str(args.get("query") or ""),
                fault_mode=args.get("fault_mode"),
                top_k=int(args.get("top_k") or 5),
            )
        elif name == "get_equipment_dependencies":
            result = ctx.get_equipment_dependencies()
        elif name == "inspect_causal_path":
            result = ctx.inspect_causal_path(causal_path_id=args.get("causal_path_id"))
        else:
            result = _error(name, "unhandled_tool")
    except Exception as exc:  # noqa: BLE001
        result = _error(name, str(exc))

    ctx.unique_exec_count += 1
    ctx._cache[key] = result
    ctx.history.append(
        {
            "tool": name,
            "arguments": args,
            "result_ok": bool(result.get("ok")),
            "cached": False,
            "result": result,
        }
    )
    return result


def budget_used(ctx: ToolContext) -> int:
    """计入工具预算的是唯一执行次数（缓存命中不额外耗预算）。"""
    return int(ctx.unique_exec_count)
