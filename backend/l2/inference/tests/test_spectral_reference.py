"""典型频谱模式参考说明：后置适配，不改动分类结果。"""

from __future__ import annotations

from copy import deepcopy

from l2.inference.spectral_reference import (
    attach_spectral_reference,
    clear_spectral_template_cache,
    resolve_spectral_reference,
)
from l2.handoff.l3_contract import (
    TRIGGER_AUTO_REVIEW,
    TRIGGER_MANUAL_REQUEST,
    build_l3_handoff_payload,
    infer_trigger_reason,
)


def setup_function() -> None:
    clear_spectral_template_cache()


def test_abnormal_with_template_returns_reference() -> None:
    ref = resolve_spectral_reference(
        equipment_type="main_mill_motor",
        screening_result="ABNORMAL",
        preliminary_fault="轴承磨损",
    )
    assert ref is not None
    assert ref["fault_class"] == "轴承磨损"
    assert ref["source"] == "equipment_template"
    assert ref["template_version"] == "spectral_patterns_v1"
    assert ref["verification_status"] == "reference_only"
    assert "BPFO幅值(g)" in ref["related_features"]
    assert "典型轴承故障" in ref["description"]


def test_normal_does_not_fabricate_fault_pattern() -> None:
    assert (
        resolve_spectral_reference(
            equipment_type="main_mill_motor",
            screening_result="NORMAL",
            preliminary_fault="正常",
        )
        is None
    )


def test_conflict_and_uncertain_no_single_fault_pattern() -> None:
    assert (
        resolve_spectral_reference(
            equipment_type="main_mill_motor",
            screening_result="CONFLICT",
            preliminary_fault="轴承磨损",
        )
        is None
    )
    assert (
        resolve_spectral_reference(
            equipment_type="main_mill_motor",
            screening_result="UNCERTAIN",
            preliminary_fault="转子不平衡",
        )
        is None
    )


def test_unknown_equipment_or_fault_degrades() -> None:
    assert (
        resolve_spectral_reference(
            equipment_type="unknown_device",
            screening_result="ABNORMAL",
            preliminary_fault="轴承磨损",
        )
        is None
    )
    assert (
        resolve_spectral_reference(
            equipment_type="main_mill_motor",
            screening_result="ABNORMAL",
            preliminary_fault="未知故障XYZ",
        )
        is None
    )


def test_template_missing_does_not_alter_diagnosis_fields() -> None:
    base = {
        "equipment_type": "unknown_device",
        "model_bundle_id": "MB-1",
        "binary_classifier": {"p_abnormal": 0.9},
        "multiclass_classifier": {"predicted_label": "轴承磨损"},
        "consistency": {"probability_gap": 0.01},
        "decision": {
            "screening_result": "ABNORMAL",
            "preliminary_fault": "轴承磨损",
            "status": "l2_complete",
            "route": "l2_complete",
            "reason_codes": ["CLEAR_ABNORMAL"],
        },
        "calibration_status": {"binary": "applied", "multiclass": "applied"},
    }
    before = deepcopy(base)
    out = attach_spectral_reference(base)
    assert out["spectral_reference"] is None
    for key in (
        "binary_classifier",
        "multiclass_classifier",
        "consistency",
        "decision",
        "model_bundle_id",
        "calibration_status",
    ):
        assert out[key] == before[key]


def test_template_does_not_change_probabilities() -> None:
    base = {
        "equipment_type": "main_mill_motor",
        "model_bundle_id": "MB-mill",
        "binary_classifier": {
            "predicted_label": "非正常",
            "probabilities": {"正常": 0.05, "非正常": 0.95},
            "p_abnormal": 0.95,
        },
        "multiclass_classifier": {
            "predicted_label": "轴承磨损",
            "probabilities": {"轴承磨损": 0.88, "正常": 0.02},
        },
        "consistency": {"p_a_abnormal": 0.95, "p_b_abnormal": 0.98, "probability_gap": 0.03},
        "decision": {
            "screening_result": "ABNORMAL",
            "preliminary_fault": "轴承磨损",
            "status": "l2_complete",
            "route": "l2_complete",
            "reason_codes": ["CLEAR_ABNORMAL"],
        },
        "calibration_status": {"binary": "applied"},
    }
    before = deepcopy(base)
    out = attach_spectral_reference(base)
    assert out["spectral_reference"] is not None
    assert out["binary_classifier"] == before["binary_classifier"]
    assert out["multiclass_classifier"] == before["multiclass_classifier"]
    assert out["consistency"] == before["consistency"]
    assert out["decision"] == before["decision"]
    assert out["model_bundle_id"] == before["model_bundle_id"]
    assert out["calibration_status"] == before["calibration_status"]


def test_equipment_templates_are_isolated() -> None:
    mill = resolve_spectral_reference(
        equipment_type="main_mill_motor",
        screening_result="ABNORMAL",
        preliminary_fault="齿轮异常",
    )
    pump = resolve_spectral_reference(
        equipment_type="high_pressure_descaling_pump",
        screening_result="ABNORMAL",
        preliminary_fault="齿轮异常",
    )
    assert mill is not None
    assert pump is None  # 泵模板未配置齿轮异常


def test_l3_handoff_contract_compatible() -> None:
    assert infer_trigger_reason(screening_result="CONFLICT", route="l3_review_recommended") == TRIGGER_AUTO_REVIEW
    assert infer_trigger_reason(screening_result="ABNORMAL", route="l3_review_recommended") == TRIGGER_AUTO_REVIEW
    assert infer_trigger_reason(screening_result="NORMAL", route="l2_complete") == TRIGGER_MANUAL_REQUEST
    payload = build_l3_handoff_payload(
        equipment_type="main_mill_motor",
        record_ids=["R1"],
        l2_results=[
            {
                "record_id": "R1",
                "decision": {
                    "screening_result": "UNCERTAIN",
                    "preliminary_fault": None,
                    "route": "l3_review_recommended",
                    "reason_codes": ["LOW_CLASS_PROBABILITY"],
                },
                "binary_classifier": {},
                "multiclass_classifier": {},
                "consistency": {},
            }
        ],
        trigger_reason=TRIGGER_AUTO_REVIEW,
        prepared_dataset_id="PED-1",
        dataset_revision_id="rev1",
        diagnosis_run_id="DR-1",
        model_bundle_id="MB-1",
    )
    assert payload["contract_version"] == "l2_l3_handoff_v1"
    assert payload["trigger_reason"] == "auto_review"
    assert payload["l2_results"][0]["screening_result"] == "UNCERTAIN"
    assert payload["l2_results"][0]["route"] == "l3_review_recommended"
