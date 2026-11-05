"""Reasoning Selection Policy：Core 全量，LLM 按需。"""

from __future__ import annotations

import os
from typing import Any

from l3.reasoning import REASONING_POLICY_VERSION


def policy_config() -> dict[str, Any]:
    def _int(name: str, default: int) -> int:
        raw = os.getenv(name, "").strip()
        if not raw:
            return default
        try:
            return max(0, int(raw))
        except ValueError:
            return default

    return {
        "policy_version": REASONING_POLICY_VERSION,
        "max_auto_records_per_run": _int("L3_REASONING_MAX_AUTO_RECORDS", 8),
        "max_tool_calls_per_record": _int("L3_REASONING_MAX_TOOL_CALLS", 8),
        "max_llm_rounds": _int("L3_REASONING_MAX_LLM_ROUNDS", 3),
        "timeout_seconds": _int("L3_REASONING_TIMEOUT_SECONDS", 90),
        "retry_limit": _int("L3_REASONING_RETRY_LIMIT", 1),
        # 4096 为当前验收可用默认；仍可能截断，可用环境变量覆盖
        "max_output_tokens": _int("L3_REASONING_MAX_OUTPUT_TOKENS", 4096),
    }


def select_reason(
    *,
    screening_result: str | None,
    evidence: dict[str, Any],
    manual_request: bool = False,
) -> tuple[bool, str]:
    """返回 (should_auto_select, reason)。manual_request 始终可选。"""
    if manual_request:
        return True, "manual_request"

    screening = str(screening_result or "").upper()
    sufficiency = str(evidence.get("evidence_sufficiency") or "")
    reviews = evidence.get("fault_type_review") or []
    support = evidence.get("support_evidence") or []
    contradict = evidence.get("contradict_evidence") or []

    if screening == "CONFLICT":
        return True, "l2_conflict"
    if screening == "UNCERTAIN":
        return True, "l2_uncertain"
    if sufficiency in {"insufficient", "inconclusive"} and reviews:
        return True, "core_inconclusive_with_candidates"
    if support and contradict:
        return True, "core_evidence_conflict"
    # 多个故障模式同时 SUPPORT
    supported_modes = [r for r in reviews if str(r.get("verdict") or "").upper() == "SUPPORT"]
    if len(supported_modes) >= 2:
        return True, "multiple_competing_faults"

    if screening == "ABNORMAL":
        # 证据充分且一致：不强制 LLM
        if sufficiency == "sufficient" and supported_modes and not contradict:
            return False, "abnormal_core_sufficient_skip_auto"
        if supported_modes and not contradict:
            return False, "abnormal_consistent_optional"
        return True, "abnormal_needs_deeper_review"

    return False, "not_selected"


def apply_selection(
    rows: list[dict[str, Any]],
    *,
    manual_record_ids: list[str] | None = None,
    automatic_selection: bool = True,
    max_auto: int | None = None,
) -> dict[str, Any]:
    cfg = policy_config()
    budget = cfg["max_auto_records_per_run"] if max_auto is None else max_auto
    wanted = set(manual_record_ids or [])
    selected: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for row in rows:
        rid = row["record_id"]
        evidence = row.get("evidence") or {}
        screening = row.get("screening_result") or (evidence.get("l2_context") or {}).get("screening_result")
        manual = rid in wanted
        ok, reason = select_reason(screening_result=screening, evidence=evidence, manual_request=manual)
        entry = {"record_id": rid, "screening_result": screening, "selection_reason": reason, "manual": manual}
        if manual or (automatic_selection and ok):
            selected.append(entry)
        elif ok and not automatic_selection:
            deferred.append({**entry, "note": "eligible_but_automatic_selection_disabled"})
        else:
            skipped.append(entry)

    # 预算：manual 优先保留，再截断 auto
    if budget >= 0 and len(selected) > budget:
        manuals = [e for e in selected if e["manual"]]
        autos = [e for e in selected if not e["manual"]]
        keep_auto = autos[: max(0, budget - len(manuals))]
        overflow = autos[len(keep_auto) :]
        selected = manuals + keep_auto
        for e in overflow:
            deferred.append({**e, "note": "budget_exceeded_pending"})

    return {
        "policy_version": cfg["policy_version"],
        "budget": budget,
        "selected": selected,
        "deferred": deferred,
        "skipped": skipped,
        "config": cfg,
    }
