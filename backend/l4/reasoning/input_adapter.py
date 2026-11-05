"""构建 L4ReasoningInput：只读绑定 L4 Core + L3 证据，不重跑 Core。"""

from __future__ import annotations

from typing import Any

from l4 import APPLICABILITY_VERSION, CAUSAL_KNOWLEDGE_VERSION, RULES_VERSION
from l4.store import L4_STORE, L4Store
from l3.runtime.store import ANALYSIS_STORE


class L4ReasoningInputError(Exception):
    def __init__(self, message: str, *, code: str = "l4_reasoning_input_error"):
        super().__init__(message)
        self.message = message
        self.code = code


def build_l4_reasoning_input(
    *,
    l4_run_id: str,
    record_id: str,
    store: L4Store | None = None,
    analysis_store=None,
) -> dict[str, Any]:
    lstore = store or L4_STORE
    astore = analysis_store or ANALYSIS_STORE
    try:
        run = lstore.get(l4_run_id)
    except KeyError as exc:
        raise L4ReasoningInputError(f"L4Run 不存在：{l4_run_id}", code="l4_run_not_found") from exc
    try:
        l4_rec = lstore.get_record(l4_run_id, record_id)
    except KeyError as exc:
        raise L4ReasoningInputError(f"L4 记录不存在：{record_id}", code="l4_record_not_found") from exc

    assessment = l4_rec.get("assessment") or {}
    if not isinstance(assessment, dict) or assessment.get("schema") != "RootCauseAssessment":
        raise L4ReasoningInputError("缺少有效 RootCauseAssessment", code="missing_root_cause_assessment")

    analysis_run_id = run.get("source_analysis_run_id") or assessment.get("analysis_run_id")
    if not analysis_run_id:
        raise L4ReasoningInputError("无法绑定 AnalysisRun", code="missing_analysis_run")

    try:
        arec = astore.get_record(analysis_run_id, record_id)
    except KeyError as exc:
        raise L4ReasoningInputError(f"Analysis 记录不存在：{record_id}", code="analysis_record_not_found") from exc

    bundle = arec.get("evidence") or {}
    if not isinstance(bundle, dict):
        bundle = {}

    allowed_evidence = []
    for row in bundle.get("evidence_items") or []:
        if isinstance(row, dict) and row.get("evidence_id"):
            allowed_evidence.append(str(row["evidence_id"]))
    for key in ("support_evidence", "contradict_evidence"):
        for row in bundle.get(key) or []:
            if isinstance(row, dict) and row.get("evidence_id"):
                eid = str(row["evidence_id"])
                if eid not in allowed_evidence:
                    allowed_evidence.append(eid)

    allowed_ck = []
    for c in (assessment.get("root_cause_candidates") or []) + (assessment.get("reference_candidates") or []):
        ck = c.get("causal_knowledge_id")
        if ck and ck not in allowed_ck:
            allowed_ck.append(ck)
    for p in assessment.get("causal_paths") or []:
        ck = p.get("causal_knowledge_id")
        if ck and ck not in allowed_ck:
            allowed_ck.append(ck)

    active_ids = {c.get("candidate_id") for c in assessment.get("root_cause_candidates") or []}
    excluded_faults = assessment.get("excluded_faults") or []

    return {
        "schema": "L4ReasoningInput",
        "schema_version": "l4_reasoning_input_v1",
        "source_l4_run_id": l4_run_id,
        "source_analysis_run_id": analysis_run_id,
        "source_reasoning_run_id": run.get("source_reasoning_run_id"),
        "source_enrichment_run_id": run.get("source_enrichment_run_id"),
        "record_id": record_id,
        "equipment_id": assessment.get("equipment_id") or bundle.get("equipment_id"),
        "equipment_type": assessment.get("equipment_type") or bundle.get("equipment_type"),
        "input_fingerprint": assessment.get("input_fingerprint") or l4_rec.get("input_fingerprint"),
        "causal_knowledge_version": assessment.get("causal_knowledge_version") or CAUSAL_KNOWLEDGE_VERSION,
        "applicability_version": assessment.get("applicability_version") or APPLICABILITY_VERSION,
        "evidence_match_version": assessment.get("rules_version") or RULES_VERSION,
        "knowledge_snapshot_id": assessment.get("knowledge_snapshot_id"),
        "root_cause_assessment": assessment,
        "evidence_bundle": bundle,
        "l2_context": bundle.get("l2_context") or {},
        "allowed_evidence_ids": allowed_evidence,
        "allowed_causal_knowledge_ids": allowed_ck,
        "allowed_candidate_ids": sorted(x for x in active_ids if x),
        "excluded_fault_types": [e.get("fault_type") for e in excluded_faults if e.get("fault_type")],
        "excluded_faults": excluded_faults,
        "l3_reasoning_assessment": None,  # 可选：由 jobs 注入
        "knowledge_context": None,
        "constraints": {
            "root_cause_confirmation_fixed": "unconfirmed",
            "may_not_restore_excluded": True,
            "may_not_upgrade_applicability": True,
            "may_not_claim_propagation": True,
            "cause_specific_not_proof": True,
        },
    }
