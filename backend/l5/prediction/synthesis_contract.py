"""L5-P3 预留：与 L2/L3/L4 对照的轻量聚合契约（本轮仅定义，不实现协调器）。"""

from __future__ import annotations

from typing import Any


DIAGNOSIS_SYNTHESIS_INPUT_SCHEMA = "DiagnosisSynthesisInput_v1"


def empty_diagnosis_synthesis_input(*, record_id: str, equipment_type: str | None = None) -> dict[str, Any]:
    return {
        "schema": DIAGNOSIS_SYNTHESIS_INPUT_SCHEMA,
        "record_id": record_id,
        "equipment_type": equipment_type,
        "l5_prediction_assessment": None,
        "l2_diagnosis_result": None,
        "l3_evidence_bundle": None,
        "l3_reasoning_assessment": None,
        "l4_root_cause_assessment": None,
        "l4_causal_reasoning_assessment": None,
        "notes": [
            "L5 dataset_predicted_* 与 L3/L4 评估分源；不得混成已确认根因。",
            "协调器（L5-P3）仅对照一致/矛盾/证据不足，不在本轮实现。",
            "禁止用验证答案选择或调参 L3/L4/L5 输出。",
        ],
    }


def attach_l5_prediction(synthesis: dict[str, Any], assessment: dict[str, Any]) -> dict[str, Any]:
    out = dict(synthesis)
    out["l5_prediction_assessment"] = assessment
    # 显式分名字段，便于对照
    preds = (assessment or {}).get("predictions") or {}
    out["dataset_predicted_root_cause"] = preds.get("根因分析")
    out["dataset_predicted_recommendation"] = preds.get("建议措施")
    out["dataset_predicted_spectrum"] = preds.get("频谱描述")
    out["dataset_predicted_impact"] = preds.get("影响程度")
    out["l4_causal_assessment_slot"] = "populate_from_l4_separately"
    out["l4_propagation_status_slot"] = "populate_from_l4_separately"
    return out
