"""审计累积与最终 outcome 收口。"""

from __future__ import annotations

from typing import Any

from l4.reasoning.validator import validate_causal_reasoning_assessment


def empty_audit() -> dict[str, Any]:
    return {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "llm_call_count": 0,
        "tool_call_count": 0,
        "execution_duration": 0.0,
        "finish_reason": None,
        "error_type": None,
    }


def accumulate_usage(audit: dict[str, Any], response: Any) -> None:
    usage = getattr(response, "usage", None)
    if not usage:
        return
    audit["prompt_tokens"] = int(audit.get("prompt_tokens") or 0) + int(getattr(usage, "prompt_tokens", 0) or 0)
    audit["completion_tokens"] = int(audit.get("completion_tokens") or 0) + int(
        getattr(usage, "completion_tokens", 0) or 0
    )
    audit["total_tokens"] = int(audit.get("total_tokens") or 0) + int(getattr(usage, "total_tokens", 0) or 0)
    audit["llm_call_count"] = int(audit.get("llm_call_count") or 0) + 1


def finalize_assessment(
    assessment: dict[str, Any],
    *,
    reasoning_input: dict[str, Any],
    base_mode: str,
    audit: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    assessment = dict(assessment)
    assessment["root_cause_confirmation"] = "unconfirmed"
    assessment["audit"] = dict(audit)
    impact = assessment.get("impact_interpretation")
    if not isinstance(impact, dict):
        impact = {}
    else:
        impact = dict(impact)
    impact.setdefault("propagation_status", "unknown")
    impact["impact_degree"] = None
    impact["impact_severity_level"] = None
    assessment["impact_interpretation"] = impact

    validation = validate_causal_reasoning_assessment(assessment, reasoning_input=reasoning_input)
    assessment["validation"] = validation
    if not validation.get("ok"):
        assessment["status"] = "validation_failed"
        assessment["outcome"] = "validation_failed"
        assessment["limitations"] = list(assessment.get("limitations") or []) + [
            f"validation_error:{e}" for e in validation.get("errors") or []
        ]
        return assessment, "validation_failed"

    assessment["status"] = "succeeded"
    if base_mode in {
        "live_llm_success",
        "deterministic_fallback",
        "dry_run",
        "insufficient_evidence",
        "llm_failed_fallback",
        "llm_invalid_json_fallback",
    }:
        assessment["outcome"] = base_mode
    else:
        assessment["outcome"] = "deterministic_fallback"
    return assessment, assessment["outcome"]
