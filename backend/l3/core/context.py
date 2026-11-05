"""单条记录分析上下文。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class HistoryPoint:
    record_id: str
    collection_time: str | None
    features: dict[str, float]


@dataclass
class AnalysisContext:
    analysis_run_id: str
    record_id: str
    equipment_type: str
    equipment_id: str | None
    prepared_dataset_id: str
    dataset_revision_id: str
    diagnosis_run_id: str
    model_bundle_id: str | None
    features: dict[str, float]
    feature_names: list[str]
    collection_time: str | None
    l2_decision: dict[str, Any]
    l2_binary: dict[str, Any]
    l2_multiclass: dict[str, Any]
    l2_consistency: dict[str, Any]
    l2_spectral_reference: dict[str, Any] | None
    trigger_reason: str
    routing_policy_version: str
    history_before: list[HistoryPoint] = field(default_factory=list)
    history_note: str = "historical_observation_reference"
    schema_version: str | None = None
    analyzer_config_version: str = "analyzers_v1"
    threshold_rules: list[dict[str, Any]] = field(default_factory=list)

    @property
    def screening_result(self) -> str | None:
        return (self.l2_decision or {}).get("screening_result")

    @property
    def preliminary_fault(self) -> str | None:
        return (self.l2_decision or {}).get("preliminary_fault")

    @property
    def candidate_faults(self) -> list[str]:
        fault = self.preliminary_fault
        ranked = (self.l2_multiclass or {}).get("ranked_classes") or []
        labels: list[str] = []
        if fault and fault not in {"正常", "None", "null"}:
            labels.append(str(fault))
        for item in ranked[:3]:
            if isinstance(item, dict):
                label = str(item.get("label") or "")
            else:
                label = str(item)
            if label and label != "正常" and label not in labels:
                labels.append(label)
        return labels

    def to_l2_slice(self) -> dict[str, Any]:
        return {
            "screening_result": self.screening_result,
            "preliminary_fault": self.preliminary_fault,
            "route": (self.l2_decision or {}).get("route"),
            "reason_codes": list((self.l2_decision or {}).get("reason_codes") or []),
            "binary_probabilities": (self.l2_binary or {}).get("probabilities"),
            "multiclass_probabilities": (self.l2_multiclass or {}).get("probabilities"),
            "original_decision_route": (self.l2_decision or {}).get("route"),
            "spectral_reference": self.l2_spectral_reference,
        }
