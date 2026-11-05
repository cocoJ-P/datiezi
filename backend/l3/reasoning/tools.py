"""L3 Reasoning 只读工具白名单（服务端受控执行）。"""

from __future__ import annotations

import json
from typing import Any, Callable

from l3.knowledge.graph_query import (
    query_equipment_components,
    query_fault_cases,
    query_fault_features,
    query_rules_for_equipment,
)
from l3.knowledge.retrieval import hybrid_search
from l3.reasoning import TOOL_REGISTRY_VERSION

MAX_RESULT_CHARS = 6000


def _truncate(obj: Any, *, limit: int = MAX_RESULT_CHARS) -> Any:
    text = json.dumps(obj, ensure_ascii=False)
    if len(text) <= limit:
        return obj
    return {"truncated": True, "preview": text[:limit], "note": "工具返回已截断，完整内容不进入上下文"}


def _error(tool: str, message: str) -> dict[str, Any]:
    return {"ok": False, "tool": tool, "error": message}


TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_l2_diagnosis",
            "description": "读取当前记录已保存的 L2 状态、候选故障、概率与分流原因。只读。",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_l3_evidence",
            "description": "读取已保存 EvidenceBundle 摘要：观测、支持/反对证据、缺失条件与 evidence_id。不重算数值。",
            "parameters": {
                "type": "object",
                "properties": {
                    "include_items": {"type": "boolean", "description": "是否包含 evidence_items 列表"},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_reasoning_trace",
            "description": "读取现有 ReasoningTrace 节点/边摘要，用于确认结论是否有真实来源。",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_fault_knowledge",
            "description": "Hybrid RAG 检索故障/规程/案例知识。返回 knowledge_id 与来源；相似度≠诊断置信度。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "fault_mode": {"type": "string"},
                    "component": {"type": "string"},
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
            "name": "query_equipment_graph",
            "description": "查询设备部件、故障特征、设备规程参数。图谱关系不表示当前故障已发生。",
            "parameters": {
                "type": "object",
                "properties": {
                    "view": {
                        "type": "string",
                        "enum": ["components", "fault_features", "rules", "all"],
                    },
                    "fault_mode": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_related_cases",
            "description": "检索相关专家案例。案例测量值不是当前记录观测。",
            "parameters": {
                "type": "object",
                "properties": {
                    "fault_mode": {"type": "string"},
                    "query": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
    },
]

REGISTERED = {t["function"]["name"] for t in TOOLS}


class ToolContext:
    def __init__(self, reasoning_input: dict[str, Any]):
        self.inp = reasoning_input
        self.equipment_type = str(reasoning_input.get("equipment_type") or "")
        self.evidence = reasoning_input.get("_evidence_bundle") or {}
        self.call_count = 0
        self.history: list[dict[str, Any]] = []

    def _l2(self) -> dict[str, Any]:
        summary = self.inp.get("evidence_summary") or {}
        return {
            "ok": True,
            "tool": "get_l2_diagnosis",
            "record_id": self.inp.get("record_id"),
            "equipment_type": self.equipment_type,
            "l2": summary.get("l2_context") or {},
            "note": "不得修改 L2 概率或类别",
        }

    def _evidence(self, include_items: bool = True) -> dict[str, Any]:
        summary = dict(self.inp.get("evidence_summary") or {})
        if not include_items:
            summary = {k: v for k, v in summary.items() if k != "evidence_items"}
        return {
            "ok": True,
            "tool": "get_l3_evidence",
            "evidence": summary,
            "allowed_evidence_ids": self.inp.get("allowed_evidence_ids") or [],
            "note": "数值只能引用 evidence_id；不得伪造测量值",
        }

    def _trace(self) -> dict[str, Any]:
        trace = self.evidence.get("reasoning_trace") or {}
        nodes = [
            {
                "node_id": n.get("node_id"),
                "node_type": n.get("node_type"),
                "evidence_id": n.get("evidence_id"),
                "assessment": n.get("assessment"),
                "related_fault_mode": n.get("related_fault_mode"),
                "conclusion_type": n.get("conclusion_type"),
            }
            for n in (trace.get("nodes") or [])[:40]
        ]
        edges = (trace.get("edges") or [])[:60]
        return {
            "ok": True,
            "tool": "get_reasoning_trace",
            "build_status": trace.get("build_status"),
            "node_count": len(trace.get("nodes") or []),
            "nodes": nodes,
            "edges": edges,
            "limitations": trace.get("limitations") or [],
        }

    def _search(self, query: str, fault_mode: str | None = None, component: str | None = None, top_k: int = 5) -> dict[str, Any]:
        q = query.strip()
        if component:
            q = f"{q} {component}".strip()
        out = hybrid_search(
            query=q,
            equipment_type=self.equipment_type or None,
            fault_mode=fault_mode,
            top_k=min(max(top_k, 1), 8),
            snapshot_id=self.inp.get("knowledge_snapshot_id"),
        )
        return {
            "ok": True,
            "tool": "search_fault_knowledge",
            "message": out.get("message"),
            "mode": out.get("mode"),
            "count": out.get("count"),
            "results": out.get("results") or [],
            "note": "检索相似度不是诊断置信度；不适用设备知识不得引用为当前事实",
        }

    def _graph(self, view: str = "all", fault_mode: str | None = None) -> dict[str, Any]:
        allowed_views = {"components", "fault_features", "rules", "all"}
        view_norm = str(view or "all").strip().lower()
        if view_norm not in allowed_views:
            return _error(
                "query_equipment_graph",
                f"非法 view={view!r}。合法值：components|fault_features|rules|all",
            )
        if not self.equipment_type:
            return _error("query_equipment_graph", "缺少 equipment_type，无法查询图谱。")
        if view_norm == "fault_features" and not (fault_mode and str(fault_mode).strip()):
            return _error(
                "query_equipment_graph",
                "view=fault_features 时必须提供非空 fault_mode。",
            )
        payload: dict[str, Any] = {
            "ok": True,
            "tool": "query_equipment_graph",
            "equipment_type": self.equipment_type,
            "view": view_norm,
        }
        if view_norm in {"components", "all"}:
            payload["components"] = query_equipment_components(self.equipment_type)
        if view_norm in {"fault_features", "all"} and fault_mode:
            payload["fault_features"] = query_fault_features(str(fault_mode).strip())
        if view_norm in {"rules", "all"}:
            payload["rules"] = query_rules_for_equipment(self.equipment_type)
        payload["note"] = "图谱关系不表示当前故障已发生"
        return payload

    def _cases(self, fault_mode: str | None = None, query: str | None = None) -> dict[str, Any]:
        mode = fault_mode or (self.inp.get("evidence_summary") or {}).get("l2_context", {}).get("preliminary_fault")
        cases = query_fault_cases(str(mode)) if mode else {"cases": [], "linked_via_occurs_on": []}
        rag = None
        if query or mode:
            rag = hybrid_search(
                query=query or f"{mode} 专家案例",
                equipment_type=self.equipment_type or None,
                fault_mode=str(mode) if mode else None,
                top_k=5,
                snapshot_id=self.inp.get("knowledge_snapshot_id"),
            )
        return {
            "ok": True,
            "tool": "get_related_cases",
            "graph_cases": cases,
            "rag_cases": [
                r
                for r in (rag.get("results") if rag else []) or []
                if r.get("knowledge_type") in {"expert_case", "retrieval_chunk"} or "EXP-" in str(r.get("canonical_name") or "")
            ],
            "note": "案例中的测量值不是当前记录观测；案例结论不是当前真值",
        }


def execute_tool(name: str, arguments: str | dict | None, ctx: ToolContext) -> dict[str, Any]:
    if name not in REGISTERED:
        return _error(name or "unknown", f"未知工具：{name}。只能调用白名单工具。")
    if isinstance(arguments, str):
        try:
            args = json.loads(arguments) if arguments.strip() else {}
        except json.JSONDecodeError:
            return _error(name, "工具参数不是合法 JSON。")
    elif arguments is None:
        args = {}
    elif isinstance(arguments, dict):
        args = arguments
    else:
        return _error(name, "工具参数类型无效。")
    if not isinstance(args, dict):
        return _error(name, "工具参数必须是对象。")

    handlers: dict[str, Callable[[], dict[str, Any]]] = {
        "get_l2_diagnosis": ctx._l2,
        "get_l3_evidence": lambda: ctx._evidence(bool(args.get("include_items", True))),
        "get_reasoning_trace": ctx._trace,
        "search_fault_knowledge": lambda: ctx._search(
            str(args.get("query") or ""),
            fault_mode=str(args["fault_mode"]) if args.get("fault_mode") else None,
            component=str(args["component"]) if args.get("component") else None,
            top_k=int(args.get("top_k") or 5),
        ),
        "query_equipment_graph": lambda: ctx._graph(
            view=str(args.get("view") if args.get("view") is not None else "all"),
            fault_mode=str(args["fault_mode"]).strip() if args.get("fault_mode") else None,
        ),
        "get_related_cases": lambda: ctx._cases(
            fault_mode=str(args["fault_mode"]) if args.get("fault_mode") else None,
            query=str(args["query"]) if args.get("query") else None,
        ),
    }
    try:
        result = handlers[name]()
    except Exception as exc:  # noqa: BLE001
        result = _error(name, f"工具执行失败：{exc}")
    result = _truncate(result)
    ctx.call_count += 1
    ctx.history.append({"tool": name, "arguments": args, "ok": bool(result.get("ok")), "result_ref": f"tool:{ctx.call_count}:{name}"})
    return result
