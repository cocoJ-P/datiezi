"""L4 Reasoning 运行策略（按需、限量）。"""

from __future__ import annotations

import os
from typing import Any


def policy_config() -> dict[str, Any]:
    return {
        "max_records": int(os.getenv("L4_REASONING_MAX_RECORDS", "5")),
        "max_llm_rounds": int(os.getenv("L4_REASONING_MAX_LLM_ROUNDS", "5")),
        "max_tool_calls_per_record": int(os.getenv("L4_REASONING_MAX_TOOL_CALLS", "12")),
        "max_output_tokens": int(os.getenv("L4_REASONING_MAX_OUTPUT_TOKENS", "4096")),
        "max_final_output_tokens": int(os.getenv("L4_REASONING_MAX_FINAL_OUTPUT_TOKENS", "6144")),
        "json_repair_limit": int(os.getenv("L4_REASONING_JSON_REPAIR_LIMIT", "1")),
        "timeout_seconds": float(os.getenv("L4_REASONING_TIMEOUT", "120")),
        "retry_limit": int(os.getenv("L4_REASONING_RETRY_LIMIT", "1")),
        "auto_select_enabled": os.getenv("L4_REASONING_AUTO_SELECT", "0") == "1",
    }


def select_records(l4_run: dict[str, Any], items: list[dict[str, Any]], *, max_records: int) -> list[str]:
    """受控自动选择：多候选 / 证据冲突 / 缺特异支持优先。可关闭。"""
    cfg = policy_config()
    if not cfg["auto_select_enabled"] and not items:
        return []
    scored: list[tuple[int, str]] = []
    for item in items:
        a = item.get("assessment") or {}
        cands = a.get("root_cause_candidates") or []
        score = 0
        if len(cands) >= 2:
            score += 3
        if any(not (c.get("supporting_evidence_ids") or []) for c in cands):
            score += 2
        if a.get("excluded_faults"):
            score += 1
        if any(c.get("contradicting_evidence_ids") for c in cands):
            score += 2
        scored.append((score, str(item.get("record_id"))))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [rid for _, rid in scored[: max(1, max_records)] if rid]
