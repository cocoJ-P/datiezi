"""统一 outcome_class 与审计字段组装。"""

from __future__ import annotations

from typing import Any

from l3.reasoning.validator import apply_semantic_guards, sanitize_origins, validate_assessment


def empty_audit() -> dict[str, Any]:
    return {
        "prompt_tokens": None,
        "completion_tokens": None,
        "total_tokens": None,
        "llm_call_count": 0,
        "tool_call_count": 0,
        "execution_duration": None,
        "finish_reason": None,
        "error_type": None,
        "estimated_cost": None,
    }


def accumulate_usage(audit: dict[str, Any], response: Any) -> None:
    audit["llm_call_count"] = int(audit.get("llm_call_count") or 0) + 1
    usage = getattr(response, "usage", None)
    if usage is None:
        return
    pt = getattr(usage, "prompt_tokens", None)
    ct = getattr(usage, "completion_tokens", None)
    tt = getattr(usage, "total_tokens", None)
    if pt is not None:
        audit["prompt_tokens"] = int(audit["prompt_tokens"] or 0) + int(pt)
    if ct is not None:
        audit["completion_tokens"] = int(audit["completion_tokens"] or 0) + int(ct)
    if tt is not None:
        audit["total_tokens"] = int(audit["total_tokens"] or 0) + int(tt)
    # 若从未拿到 usage，保持 None；部分调用有 usage 则累计


def finalize_assessment(
    assessment: dict[str, Any],
    *,
    reasoning_input: dict[str, Any],
    base_mode: str,
    audit: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str]:
    """校验 + 语义守卫；返回 (assessment, outcome_class)。未通过不得标 live_llm_success。"""
    sanitize_origins(assessment)
    demotions = apply_semantic_guards(assessment, reasoning_input=reasoning_input)
    validation = validate_assessment(assessment, reasoning_input=reasoning_input, apply_guards=False)
    if not validation["ok"]:
        # 受控清理非法引用后再验
        allowed_ev = set(reasoning_input.get("allowed_evidence_ids") or [])
        allowed_kn = set(reasoning_input.get("allowed_knowledge_ids") or [])
        for hyp in assessment.get("candidate_hypotheses") or []:
            if not isinstance(hyp, dict):
                continue
            hyp["supporting_evidence_ids"] = [e for e in (hyp.get("supporting_evidence_ids") or []) if str(e) in allowed_ev]
            hyp["contradicting_evidence_ids"] = [
                e for e in (hyp.get("contradicting_evidence_ids") or []) if str(e) in allowed_ev
            ]
            hyp["knowledge_reference_ids"] = [
                k for k in (hyp.get("knowledge_reference_ids") or []) if (not allowed_kn) or str(k) in allowed_kn
            ]
            # origin 再规范化
        for key in list(assessment.keys()):
            if key in {
                "final_root_cause",
                "root_cause",
                "repair_action",
                "recommended_actions",
                "business_priority",
                "priority",
                "impact_assessment",
                "impact",
            }:
                assessment.pop(key, None)
        apply_semantic_guards(assessment, reasoning_input=reasoning_input)
        sanitize_origins(assessment)
        validation = validate_assessment(assessment, reasoning_input=reasoning_input, apply_guards=False)

    assessment["validation"] = validation
    if audit is not None:
        assessment["audit"] = audit

    if base_mode in {"llm_invalid_json_fallback", "llm_failed_fallback", "dry_run"}:
        outcome = base_mode
        assessment["status"] = base_mode
        assessment["outcome_class"] = outcome
        return assessment, outcome

    if not validation["ok"]:
        outcome = "validation_failed"
        assessment["status"] = "validation_failed"
        assessment["outcome_class"] = outcome
        assessment["limitations"] = list(assessment.get("limitations") or []) + ["validation_failed_keep_core"]
        return assessment, outcome

    # 在线成功路径：语义降级或 Core 证据不足 → insufficient_evidence
    sufficiency = str(assessment.get("evidence_sufficiency") or "")
    if demotions or sufficiency in {"insufficient", "limited"}:
        outcome = "insufficient_evidence"
        assessment["status"] = "insufficient_evidence"
        assessment["outcome_class"] = outcome
        if demotions:
            assessment["limitations"] = list(assessment.get("limitations") or []) + ["semantic_guard_applied"]
        return assessment, outcome

    outcome = "live_llm_success"
    assessment["status"] = "live_llm_success"
    assessment["outcome_class"] = outcome
    return assessment, outcome
