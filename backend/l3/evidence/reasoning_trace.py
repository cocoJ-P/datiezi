"""Reasoning Trace v1：由现有 Analyzer / EvidenceItem 组装可追溯 DAG（不重跑分析）。"""

from __future__ import annotations

from typing import Any

from l3.core.analyzer_base import AnalyzerResult
from l3.core.context import AnalysisContext

TRACE_VERSION = "reasoning_trace_v1"

NODE_OBSERVATION = "OBSERVATION"
NODE_ANALYSIS = "ANALYSIS"
NODE_RULE = "RULE"
NODE_EVIDENCE = "EVIDENCE"
NODE_CONCLUSION = "CONCLUSION"
NODE_KNOWLEDGE_REFERENCE = "KNOWLEDGE_REFERENCE"  # L3 Knowledge 扩展；Core 构建时不生成
NODE_HYPOTHESIS = "HYPOTHESIS"  # L3 Reasoning 扩展
NODE_TOOL_RESULT = "TOOL_RESULT"  # L3 Reasoning 扩展

EDGE_DERIVED_FROM = "DERIVED_FROM"
EDGE_EVALUATED_BY = "EVALUATED_BY"
EDGE_SUPPORTS = "SUPPORTS"
EDGE_CONTRADICTS = "CONTRADICTS"
EDGE_CONTRIBUTES_TO = "CONTRIBUTES_TO"


class ReasoningTraceError(RuntimeError):
    """Trace 构建失败（不得伪装为完整成功结果）。"""


def _slug(value: str) -> str:
    return str(value).replace(" ", "_").replace("/", "_").replace("(", "").replace(")", "").replace("°", "")


def validate_trace(trace: dict[str, Any]) -> None:
    nodes = trace.get("nodes") or []
    edges = trace.get("edges") or []
    by_id = {n["node_id"]: n for n in nodes}
    if len(by_id) != len(nodes):
        raise ReasoningTraceError("节点 ID 不唯一")
    for edge in edges:
        if edge.get("from") not in by_id or edge.get("to") not in by_id:
            raise ReasoningTraceError(f"边引用不存在的节点：{edge}")
    # 环检测（Kahn）
    indeg: dict[str, int] = {nid: 0 for nid in by_id}
    outs: dict[str, list[str]] = {nid: [] for nid in by_id}
    for edge in edges:
        outs[edge["from"]].append(edge["to"])
        indeg[edge["to"]] += 1
    queue = [nid for nid, d in indeg.items() if d == 0]
    seen = 0
    while queue:
        nid = queue.pop()
        seen += 1
        for nxt in outs[nid]:
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                queue.append(nxt)
    if seen != len(by_id):
        raise ReasoningTraceError("存在循环依赖")
    for cid in trace.get("conclusion_node_ids") or []:
        if cid not in by_id:
            raise ReasoningTraceError(f"结论节点不存在：{cid}")
        if by_id[cid].get("node_type") != NODE_CONCLUSION:
            raise ReasoningTraceError(f"结论引用类型错误：{cid}")


def build_reasoning_trace(
    context: AnalysisContext,
    analyzer_results: list[AnalyzerResult],
    review: dict[str, Any],
) -> dict[str, Any]:
    """复用已有分析结果构建 Trace；失败时抛出 ReasoningTraceError。"""
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    limitations: list[str] = list(review.get("analysis_limitations") or [])
    conclusion_ids: list[str] = []

    def add_node(node: dict[str, Any]) -> str:
        nid = node["node_id"]
        if nid in nodes:
            return nid
        nodes[nid] = node
        return nid

    def add_edge(frm: str, to: str, relation: str) -> None:
        if frm not in nodes or to not in nodes:
            raise ReasoningTraceError(f"边端点缺失：{frm} -> {to}")
        key = (frm, to, relation)
        if any((e["from"], e["to"], e["relation"]) == key for e in edges):
            return
        edges.append({"from": frm, "to": to, "relation": relation})

    # --- OBSERVATION：仅当前记录实际存在的特征 ---
    for name, value in sorted(context.features.items()):
        obs_id = f"obs:{context.record_id}:{_slug(name)}"
        add_node(
            {
                "node_id": obs_id,
                "node_type": NODE_OBSERVATION,
                "feature_name": name,
                "value": value,
                "unit": None,
                "record_id": context.record_id,
                "source_reference": {
                    "prepared_dataset_id": context.prepared_dataset_id,
                    "dataset_revision_id": context.dataset_revision_id,
                    "equipment_id": context.equipment_id,
                    "collection_time": context.collection_time,
                },
            }
        )

    # 历史参考观测（聚合，不展开全部历史记录）
    hist_obs_ids: dict[str, str] = {}
    if context.history_before and context.history_note not in {
        "insufficient_history",
        "invalid_or_missing_collection_time",
        "no_equipment_id_single_record_only",
    }:
        # 从 statistical observations 提取已计算的历史中位数
        for result in analyzer_results:
            if result.analyzer_id != "statistical":
                continue
            for obs in result.observations:
                name = obs.get("feature_name")
                med = obs.get("history_median")
                if name is None or med is None:
                    continue
                hid = f"obs:{context.record_id}:hist_median:{_slug(str(name))}"
                hist_obs_ids[str(name)] = add_node(
                    {
                        "node_id": hid,
                        "node_type": NODE_OBSERVATION,
                        "feature_name": f"{name}#history_median",
                        "value": med,
                        "unit": None,
                        "record_id": context.record_id,
                        "source_reference": {
                            "kind": "historical_observation_reference",
                            "history_note": context.history_note,
                            "history_sample_count": obs.get("history_sample_count"),
                            "equipment_id": context.equipment_id,
                            "window": "records_before_current_collection_time",
                            "not_future": True,
                        },
                    }
                )

    # --- RULE：仅实际配置且被 threshold 使用的规则 ---
    used_rule_ids: set[str] = set()
    for result in analyzer_results:
        if result.analyzer_id != "threshold":
            continue
        for obs in result.observations:
            rid = obs.get("rule_id")
            if not rid:
                continue
            used_rule_ids.add(str(rid))
            rule_node = f"rule:{rid}"
            add_node(
                {
                    "node_id": rule_node,
                    "node_type": NODE_RULE,
                    "rule_id": rid,
                    "rule_version": obs.get("rule_version"),
                    "equipment_scope": context.equipment_type,
                    "condition": {
                        "parameter": obs.get("parameter"),
                        "threshold": obs.get("threshold"),
                        "threshold_type": obs.get("threshold_type"),
                        "unit": obs.get("unit"),
                    },
                    "source_reference": obs.get("source_reference"),
                }
            )

    # --- ANALYSIS + EVIDENCE from evidence_items ---
    evidence_node_ids: list[str] = []
    for item in review.get("evidence_items") or []:
        eid = str(item.get("evidence_id") or "")
        if not eid:
            raise ReasoningTraceError("EvidenceItem 缺少 evidence_id")
        feature = item.get("feature_name")
        analyzer_id = str(item.get("analyzer_id") or "unknown")
        analyzer_version = str(item.get("analyzer_version") or "")
        obs_id = f"obs:{context.record_id}:{_slug(str(feature))}" if feature else None
        if obs_id and obs_id not in nodes:
            # 证据引用了不存在的观测 → 失败，而不是伪造
            raise ReasoningTraceError(f"证据引用缺失观测：{feature}")

        method = str(item.get("calculation_description") or analyzer_id)
        an_id = f"an:{analyzer_id}:{_slug(method)[:80]}:{_slug(str(feature or 'na'))}"
        input_ids = [obs_id] if obs_id else []
        if feature and str(feature) in hist_obs_ids:
            input_ids.append(hist_obs_ids[str(feature)])
        add_node(
            {
                "node_id": an_id,
                "node_type": NODE_ANALYSIS,
                "analyzer_id": analyzer_id,
                "analyzer_version": analyzer_version,
                "method": method,
                "parameters": {
                    "related_fault_mode": item.get("related_fault_mode"),
                    "reference_value": item.get("reference_value"),
                    "reference_source": item.get("reference_source"),
                },
                "result": {
                    "observed_value": item.get("observed_value"),
                    "assessment": item.get("assessment"),
                },
                "input_node_ids": input_ids,
            }
        )
        for iid in input_ids:
            add_edge(iid, an_id, EDGE_DERIVED_FROM)

        # threshold 规则边
        if analyzer_id == "threshold":
            for rid in used_rule_ids:
                rule_node = f"rule:{rid}"
                # 仅连接参数匹配的规则
                rule = nodes.get(rule_node) or {}
                if (rule.get("condition") or {}).get("parameter") == feature and rule_node in nodes:
                    add_edge(rule_node, an_id, EDGE_EVALUATED_BY)

        ev_node = f"ev:{eid}"
        add_node(
            {
                "node_id": ev_node,
                "node_type": NODE_EVIDENCE,
                "evidence_id": eid,
                "related_fault_mode": item.get("related_fault_mode"),
                "assessment": item.get("assessment"),
                "supporting_node_ids": [an_id],
                "limitations": list(item.get("limitations") or []) if isinstance(item.get("limitations"), list) else [],
                "feature_name": feature,
            }
        )
        add_edge(an_id, ev_node, EDGE_DERIVED_FROM)
        evidence_node_ids.append(ev_node)

    # 同源依赖：共享观测节点已自然处理；额外记录 limitation
    for dep in review.get("evidence_dependencies") or []:
        if dep.get("dependency_type") == "common_source":
            limitations.append(str(dep.get("note") or "common_source_dependency"))

    # --- CONCLUSION from fault_type_review + threshold/location notes ---
    for review_item in review.get("fault_type_review") or []:
        mode = str(review_item.get("fault_mode") or "unknown")
        verdict = str(review_item.get("verdict") or "INCONCLUSIVE")
        cid = f"concl:fault_review:{_slug(mode)}"
        related_eids = [
            f"ev:{e.get('evidence_id')}"
            for e in (review.get("evidence_items") or [])
            if e.get("related_fault_mode") == mode and e.get("evidence_id")
        ]
        related_eids = [x for x in related_eids if x in nodes]
        add_node(
            {
                "node_id": cid,
                "node_type": NODE_CONCLUSION,
                "conclusion_type": "fault_type_review",
                "assessment": verdict,
                "evidence_ids": [nodes[x]["evidence_id"] for x in related_eids],
                "related_fault_mode": mode,
                "limitations": [
                    str(review_item.get("note") or ""),
                    *list(review.get("missing_evidence") or [])[:8],
                ],
                "summary": (
                    f"对候选「{mode}」的复核结果为 {verdict}；"
                    f"支持 {review_item.get('support_count', 0)} / 反对 {review_item.get('contradict_count', 0)}。"
                ),
            }
        )
        conclusion_ids.append(cid)
        for evn in related_eids:
            assessment = str(nodes[evn].get("assessment") or "")
            if assessment == "SUPPORT":
                add_edge(evn, cid, EDGE_SUPPORTS)
            elif assessment == "CONTRADICT":
                add_edge(evn, cid, EDGE_CONTRADICTS)
            else:
                add_edge(evn, cid, EDGE_CONTRIBUTES_TO)

    # 阈值越限结论（若有）
    exceed = (review.get("severity_assessment") or {}).get("threshold_exceedances") or []
    if exceed:
        cid = "concl:threshold_risk"
        related = [nid for nid in evidence_node_ids if nodes[nid].get("assessment") == "SUPPORT"]
        # 仅关联 threshold 分析证据
        thr_evs = [
            nid
            for nid in evidence_node_ids
            if any(
                (e.get("evidence_id") == nodes[nid].get("evidence_id") and e.get("analyzer_id") == "threshold")
                for e in (review.get("evidence_items") or [])
            )
        ]
        use_evs = thr_evs or related[:5]
        add_node(
            {
                "node_id": cid,
                "node_type": NODE_CONCLUSION,
                "conclusion_type": "threshold_risk_indicator",
                "assessment": "SUPPORT" if use_evs else "INCONCLUSIVE",
                "evidence_ids": [nodes[x]["evidence_id"] for x in use_evs if x in nodes],
                "limitations": ["越限仅为风险征兆，不等于正式严重度等级", "severity_level=null"],
                "summary": f"检测到 {len(exceed)} 项适用限值越限征兆。",
            }
        )
        conclusion_ids.append(cid)
        for evn in use_evs:
            if evn in nodes:
                add_edge(evn, cid, EDGE_SUPPORTS)

    # 位置不足结论
    loc = review.get("location_candidates") or {}
    if not (loc.get("candidates") or []):
        cid = "concl:location_insufficient"
        add_node(
            {
                "node_id": cid,
                "node_type": NODE_CONCLUSION,
                "conclusion_type": "location_assessment",
                "assessment": "INCONCLUSIVE",
                "evidence_ids": [],
                "limitations": list(loc.get("limitations") or ["传感器-部件映射不足"]),
                "summary": "缺少足够证据判断具体故障位置。",
            }
        )
        conclusion_ids.append(cid)

    # 无证据时最小 Trace：观测 + INCONCLUSIVE 结论
    if not conclusion_ids:
        cid = "concl:insufficient_evidence"
        add_node(
            {
                "node_id": cid,
                "node_type": NODE_CONCLUSION,
                "conclusion_type": "evidence_sufficiency",
                "assessment": "INCONCLUSIVE",
                "evidence_ids": [],
                "limitations": list(review.get("missing_evidence") or []) + limitations,
                "summary": f"证据充分性={review.get('evidence_sufficiency') or 'insufficient'}，无法形成明确故障复核。",
            }
        )
        conclusion_ids.append(cid)
        for evn in evidence_node_ids:
            add_edge(evn, cid, EDGE_CONTRIBUTES_TO)

    # 明确：不把 L2 spectral_reference 作为验证证据节点
    if context.l2_spectral_reference:
        limitations.append("l2_spectral_reference_is_reference_only_not_verified_evidence")

    # 扩展点占位（无业务逻辑）
    extension_hooks = {
        "future_node_types": ["KNOWLEDGE_REFERENCE", "HYPOTHESIS", "TOOL_RESULT"],
        "llm_private_thoughts": False,
    }

    trace = {
        "trace_id": f"TRACE-{context.analysis_run_id}-{context.record_id}",
        "trace_version": TRACE_VERSION,
        "analyzer_config_version": context.analyzer_config_version,
        "routing_policy_version": context.routing_policy_version,
        "dataset_revision_id": context.dataset_revision_id,
        "nodes": list(nodes.values()),
        "edges": edges,
        "conclusion_node_ids": conclusion_ids,
        "limitations": sorted(set(x for x in limitations if x)),
        "extension_hooks": extension_hooks,
        "build_status": "ok",
    }
    validate_trace(trace)
    return trace


def build_reasoning_trace_safe(
    context: AnalysisContext,
    analyzer_results: list[AnalyzerResult],
    review: dict[str, Any],
) -> dict[str, Any]:
    try:
        return build_reasoning_trace(context, analyzer_results, review)
    except Exception as exc:  # noqa: BLE001 - 必须显式失败记录
        return {
            "trace_id": f"TRACE-{context.analysis_run_id}-{context.record_id}",
            "trace_version": TRACE_VERSION,
            "nodes": [],
            "edges": [],
            "conclusion_node_ids": [],
            "limitations": [f"reasoning_trace_build_failed: {exc}"],
            "build_status": "failed",
            "error": str(exc),
        }
