"""程序化校验 CausalReasoningAssessment，失败则不得标推理成功。"""

from __future__ import annotations

from typing import Any

from l4.reasoning.schema import FORBIDDEN_FIELDS, VALID_OUTCOMES, VALID_VALIDITY


def validate_causal_reasoning_assessment(
    assessment: dict[str, Any],
    *,
    reasoning_input: dict[str, Any],
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []

    core = reasoning_input.get("root_cause_assessment") or {}
    allowed_cand = set(reasoning_input.get("allowed_candidate_ids") or [])
    allowed_ev = set(reasoning_input.get("allowed_evidence_ids") or [])
    allowed_ck = set(reasoning_input.get("allowed_causal_knowledge_ids") or [])
    excluded_faults = {str(x) for x in (reasoning_input.get("excluded_fault_types") or [])}
    core_appl = {
        c.get("candidate_id"): c.get("applicability_status")
        for c in (core.get("root_cause_candidates") or [])
        if c.get("candidate_id")
    }

    # 强制确认态
    if assessment.get("root_cause_confirmation") not in {None, "unconfirmed"}:
        errors.append("root_cause_confirmation_must_remain_unconfirmed")
    assessment["root_cause_confirmation"] = "unconfirmed"

    # 禁止 L5 / 确认类字段
    blob_keys = set(assessment.keys())
    for bad in FORBIDDEN_FIELDS:
        if bad in blob_keys and bad != "root_cause_confirmation":
            if assessment.get(bad) not in (None, "", [], {}, "unconfirmed"):
                errors.append(f"forbidden_field:{bad}")

    impact = assessment.get("impact_interpretation") or {}
    if impact.get("impact_degree") not in (None,):
        errors.append("impact_degree_must_be_null")
    if str(impact.get("propagation_status") or "unknown") not in {"unknown", "not_assessed"}:
        # 允许复述 Core 的 unknown；禁止 confirmed/observed_propagated
        if str(impact.get("propagation_status")).lower() in {
            "confirmed",
            "propagated",
            "observed",
            "high",
            "medium",
            "low",
        }:
            errors.append("invalid_propagation_claim")
    for note in impact.get("notes") or []:
        text = str(note)
        # 禁止肯定性传播表述；允许“不宣称已传播”等否定边界说明
        if any(
            x in text
            for x in (
                "已经传播到",
                "已传播至",
                "已影响下游设备",
                "confirmed propagation",
                "故障已跨设备传播",
            )
        ):
            errors.append("natural_language_propagation_claim")
        if ("已传播" in text or "已经传播" in text) and not any(
            neg in text for neg in ("不宣称", "并非", "不是", "未", "不得", "禁止")
        ):
            errors.append("natural_language_propagation_claim")

    for rev in assessment.get("candidate_reviews") or []:
        if not isinstance(rev, dict):
            errors.append("candidate_review_not_object")
            continue
        cid = rev.get("candidate_id")
        if cid not in allowed_cand:
            # review_recommended on excluded is separate list
            errors.append(f"unknown_candidate_id:{cid}")
        vs = rev.get("validity_status")
        if vs and vs not in VALID_VALIDITY:
            errors.append(f"invalid_validity_status:{vs}")
        if vs == "not_applicable_kept_excluded":
            errors.append("excluded_restored_via_validity_status")
        for eid in (rev.get("upstream_cause_evidence") or []) + (rev.get("fault_manifestation_evidence") or []) + (
            rev.get("contradicting_evidence") or []
        ):
            if eid and str(eid) not in allowed_ev:
                errors.append(f"unknown_evidence_id:{eid}")
        for ck in rev.get("source_causal_knowledge_ids") or []:
            if ck and ck not in allowed_ck:
                errors.append(f"unknown_causal_knowledge_id:{ck}")
        # 不得升级适用性
        if cid in core_appl and rev.get("applicability_status"):
            if rev.get("applicability_status") == "applicable" and core_appl.get(cid) not in {
                "applicable",
                "associated_unconfirmed",
            }:
                errors.append(f"applicability_upgrade_forbidden:{cid}")
        # 表现证据不能写成已确认原因
        summary = str(rev.get("review_summary") or "") + str(rev.get("causal_mechanism_summary") or "")
        if any(x in summary for x in ("根因已确认", "已证实根因", "confirmed root cause", "确诊根因")):
            errors.append(f"claims_confirmed_root_cause:{cid}")
        if rev.get("same_layer_as_resulting_fault") and rev.get("validity_status") not in {
            "mechanism_only",
            "insufficient_upstream_evidence",
            "indistinguishable",
            "consistent_with_observations",
            None,
            "",
        }:
            # consistent_with_observations OK for manifestation alignment; but not as deeper root
            pass
        if rev.get("same_layer_as_resulting_fault") and vs not in {
            None,
            "",
            "mechanism_only",
            "insufficient_upstream_evidence",
            "indistinguishable",
            "consistent_with_observations",
            "contradicted",
        }:
            warnings.append(f"same_layer_unexpected_status:{cid}:{vs}")

    for path_rev in assessment.get("causal_path_reviews") or []:
        if not isinstance(path_rev, dict):
            continue
        ck = path_rev.get("causal_knowledge_id")
        if ck and ck not in allowed_ck:
            errors.append(f"unknown_path_causal_knowledge_id:{ck}")
        for eid in path_rev.get("observation_evidence_ids") or []:
            if eid and str(eid) not in allowed_ev:
                errors.append(f"unknown_path_evidence_id:{eid}")

    # 排除恢复检查
    for rec in assessment.get("excluded_review_recommendations") or []:
        if not isinstance(rec, dict):
            continue
        if rec.get("action") not in {None, "review_recommended"}:
            errors.append("excluded_auto_restore_forbidden")
        ft = rec.get("fault_type")
        if ft and ft not in excluded_faults:
            warnings.append(f"review_for_non_excluded_fault:{ft}")
        # 不得出现在 candidate_reviews 作为有效恢复
        if any(r.get("related_fault_type") == ft and r.get("validity_status") not in {"not_applicable_kept_excluded"} for r in assessment.get("candidate_reviews") or []):
            # candidate_reviews 只允许 active candidates
            pass

    restored = []
    for rev in assessment.get("candidate_reviews") or []:
        # if cause targets excluded fault type as if active — check cause related via core cand
        cid = rev.get("candidate_id")
        core_cand = next((c for c in (core.get("root_cause_candidates") or []) if c.get("candidate_id") == cid), None)
        if core_cand and core_cand.get("related_fault_type") in excluded_faults:
            restored.append(cid)
    if restored:
        errors.append(f"excluded_fault_candidates_present:{restored}")

    for eid in assessment.get("supporting_evidence_ids") or []:
        if eid and str(eid) not in allowed_ev:
            errors.append(f"unknown_global_support_id:{eid}")
    for eid in assessment.get("contradicting_evidence_ids") or []:
        if eid and str(eid) not in allowed_ev:
            errors.append(f"unknown_global_contradict_id:{eid}")

    outcome = assessment.get("outcome")
    if outcome and outcome not in VALID_OUTCOMES:
        errors.append(f"invalid_outcome:{outcome}")

    ok = len(errors) == 0
    return {"ok": ok, "errors": errors, "warnings": warnings}
