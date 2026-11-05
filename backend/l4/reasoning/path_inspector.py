"""确定性因果路径检验（服务端执行，不交给 LLM 自行判定证据状态）。"""

from __future__ import annotations

from typing import Any


def inspect_causal_path(assessment: dict[str, Any], *, causal_path_id: str | None = None) -> dict[str, Any]:
    paths = assessment.get("causal_paths") or []
    candidates = assessment.get("root_cause_candidates") or []
    cand_by_path = {c.get("causal_path_id"): c for c in candidates if c.get("causal_path_id")}

    selected = []
    for path in paths:
        if causal_path_id and path.get("causal_path_id") != causal_path_id:
            continue
        selected.append(path)
    if causal_path_id and not selected:
        return {"ok": False, "error": f"unknown_causal_path_id:{causal_path_id}"}

    reviews = []
    for path in selected:
        pid = path.get("causal_path_id")
        cand = cand_by_path.get(pid) or {}
        matching = path.get("evidence_matching") or {}
        same_layer = bool(
            matching.get("same_layer_as_resulting_fault") or cand.get("same_layer_as_resulting_fault")
        )
        cause_ids = list(cand.get("supporting_evidence_ids") or path.get("supporting_evidence_ids") or [])
        manif_ids = list(
            cand.get("fault_manifestation_evidence_ids") or path.get("fault_manifestation_evidence_ids") or []
        )
        contra_ids = list(
            cand.get("contradicting_evidence_ids") or path.get("contradicting_evidence_ids") or []
        )
        missing = list(cand.get("missing_evidence") or path.get("missing_evidence") or [])
        link = path.get("link_states") or {}

        def _step(name: str, *, knowledge: bool, observed_ids: list[str], state_hint: str) -> dict[str, Any]:
            if contra_ids and name in {"cause", "fault"} and not observed_ids and not cause_ids:
                status = "contradicted"
            elif observed_ids:
                status = "observed" if name != "cause" or (cause_ids and not same_layer) else "indirectly_supported"
            elif knowledge:
                status = "knowledge_backed"
            elif missing:
                status = "missing"
            else:
                status = "unresolved"
            if name == "cause" and same_layer:
                status = "knowledge_backed" if knowledge else "unresolved"
                note = "同层机理：不得将故障表现观测记为上游原因已观测"
            elif name == "cause" and cause_ids:
                status = "indirectly_supported"
                note = "存在 cause_specific 观测，但不足以单独确认根因（可被竞争解释）"
            else:
                note = ""
            return {
                "step": name,
                "status": status if status != "observed" or name != "mechanism" else "knowledge_backed",
                "has_knowledge_source": knowledge,
                "observation_evidence_ids": observed_ids,
                "contradicting_evidence_ids": contra_ids if name in {"cause", "fault"} else [],
                "missing": missing if name == "cause" else [],
                "state_hint": state_hint,
                "note": note,
            }

        has_docs = bool(path.get("source_document_locations") or path.get("source_knowledge_ids"))
        reviews.append(
            {
                "causal_path_id": pid,
                "causal_knowledge_id": path.get("causal_knowledge_id"),
                "cause": path.get("cause"),
                "mechanism": path.get("mechanism"),
                "resulting_fault": path.get("related_fault_type") or path.get("resulting_fault"),
                "same_layer_as_resulting_fault": same_layer,
                "steps": [
                    _step("cause", knowledge=has_docs, observed_ids=cause_ids, state_hint=str(link.get("cause") or "")),
                    _step(
                        "mechanism",
                        knowledge=has_docs,
                        observed_ids=[],
                        state_hint=str(link.get("mechanism") or ""),
                    ),
                    _step(
                        "fault",
                        knowledge=True,
                        observed_ids=manif_ids or cause_ids,
                        state_hint=str(link.get("fault_to_evidence") or ""),
                    ),
                    _step(
                        "observation",
                        knowledge=False,
                        observed_ids=manif_ids + cause_ids,
                        state_hint="observed" if (manif_ids or cause_ids) else "missing",
                    ),
                ],
                "applicability_status": cand.get("applicability_status"),
                "component_binding": cand.get("component_binding"),
                "notes": [
                    "CONNECTS_TO ≠ CAUSES；知识机理 ≠ 本机已发生。",
                    "cause_specific 不自动证明根因成立。",
                ],
            }
        )

    return {
        "ok": True,
        "tool": "inspect_causal_path",
        "path_count": len(reviews),
        "reviews": reviews,
    }
