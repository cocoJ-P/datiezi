"""L4 结构化结果契约（确定性，不含 LLM 字段）。"""

from __future__ import annotations

from typing import Any

from l4 import CAUSAL_TRACE_VERSION, L4_SCHEMA_VERSION

# 任务执行 outcome（与根因确认状态分离）
VALID_OUTCOMES = {
    "candidates_generated",
    "insufficient_evidence",
    "no_applicable_causal_knowledge",
    "mapping_unresolved",
    "analysis_failed",
}

# 根因确认状态：本阶段全部 unconfirmed
ROOT_CAUSE_CONFIRMATION = "unconfirmed"

# 因果路径环节证据状态（非业务诊断字段）
PATH_EVIDENCE_STATES = {
    "observed",
    "knowledge_supported",
    "hypothesized",
    "contradicted",
    "unknown",
}

# 一般异常证据标记：不得单独支撑具体根因
ANOMALY_ONLY_MARKERS = (
    "alarm",
    "thr:",
    "vib_x",
    "vib_y",
    "vib_max",
    "threshold",
    "越限",
    "vibration",
)


def empty_assessment_skeleton(
    *,
    l4_run_id: str,
    analysis_run_id: str,
    record_id: str,
    input_fingerprint: str,
    causal_knowledge_version: str,
) -> dict[str, Any]:
    return {
        "schema": "RootCauseAssessment",
        "l4_schema_version": L4_SCHEMA_VERSION,
        "l4_run_id": l4_run_id,
        "analysis_run_id": analysis_run_id,
        "record_id": record_id,
        "input_fingerprint": input_fingerprint,
        "causal_knowledge_version": causal_knowledge_version,
        "root_cause_confirmation": ROOT_CAUSE_CONFIRMATION,
        "root_cause_candidates": [],
        "causal_paths": [],
        "impact_assessment": None,
        "evidence_references": [],
        "missing_evidence": [],
        "limitations": [],
        "causal_trace": {
            "trace_version": CAUSAL_TRACE_VERSION,
            "nodes": [],
            "edges": [],
        },
        "outcome": "no_applicable_causal_knowledge",
        "notes": [],
    }


def is_anomaly_only_evidence(evidence_id: str | None, feature_name: str | None = None) -> bool:
    text = f"{evidence_id or ''} {feature_name or ''}".lower()
    return any(marker in text for marker in ANOMALY_ONLY_MARKERS)
