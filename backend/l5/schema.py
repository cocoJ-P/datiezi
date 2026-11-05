"""FinalDiagnosisAssessment 十字段 Schema（仅十项业务字段）。"""

from __future__ import annotations

from typing import Any

BUSINESS_FIELD_ORDER = [
    "equipment_status",
    "fault_type",
    "fault_severity",
    "fault_location",
    "diagnosis_confidence",
    "spectral_description",
    "root_cause_analysis",
    "recommended_actions",
    "priority",
    "impact_degree",
]

BUSINESS_FIELD_LABELS_ZH = {
    "equipment_status": "设备状态",
    "fault_type": "故障类型",
    "fault_severity": "故障严重度",
    "fault_location": "故障位置",
    "diagnosis_confidence": "诊断置信度",
    "spectral_description": "频谱描述",
    "root_cause_analysis": "根因分析",
    "recommended_actions": "建议措施",
    "priority": "优先级",
    "impact_degree": "影响程度",
}

FORBIDDEN_FABRICATION = {
    "risk_level",
    "priority_p0_p3",
    "confirmed_root_cause",
    "repair_order",
    "shutdown_order",
}


def field_slot(
    value: Any,
    *,
    source_layer: str | None = None,
    source_run_id: str | None = None,
    evidence_references: list[str] | None = None,
    knowledge_references: list[str] | None = None,
    uncertainty: str | None = None,
    null_reason: str | None = None,
    display: str | None = None,
    extras: dict[str, Any] | None = None,
) -> dict[str, Any]:
    slot: dict[str, Any] = {
        "value": value,
        "display": display if display is not None else (None if value is None else value),
        "source_layer": source_layer,
        "source_run_id": source_run_id,
        "evidence_references": list(evidence_references or []),
        "knowledge_references": list(knowledge_references or []),
        "uncertainty": uncertainty,
        "null_reason": null_reason,
    }
    if extras:
        slot["extras"] = extras
    return slot


def empty_assessment(
    *,
    l5_run_id: str,
    record_id: str,
    source_run_ids: dict[str, Any],
    source_versions: dict[str, Any],
    input_fingerprint: str,
) -> dict[str, Any]:
    fields = {k: field_slot(None, null_reason="not_aggregated") for k in BUSINESS_FIELD_ORDER}
    return {
        "schema": "FinalDiagnosisAssessment",
        "schema_version": "final_diagnosis_assessment_v1",
        "l5_run_id": l5_run_id,
        "record_id": record_id,
        "equipment_id": None,
        "equipment_type": None,
        "source_run_ids": source_run_ids,
        "source_versions": source_versions,
        "source_availability": {},
        "input_fingerprint": input_fingerprint,
        "aggregation_schema_version": "final_diagnosis_assessment_v1",
        "recommendation_rule_version": "l5a_verification_v1",
        "fields": fields,
        "field_labels_zh": dict(BUSINESS_FIELD_LABELS_ZH),
        "verification_recommendations": [],
        "provenance_index": {},
        "limitations": [],
        "review_status": "draft",
        "completeness": "incomplete",
        "root_cause_confirmation": "unconfirmed",
        "outcome": "partial",
        "status": "succeeded",
    }
