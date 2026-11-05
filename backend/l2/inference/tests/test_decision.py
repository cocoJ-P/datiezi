from __future__ import annotations

from l2.inference.decision import decide


def _probs(**kwargs: float) -> dict[str, float]:
    base = {
        "正常": 0.0,
        "轴承磨损": 0.0,
        "转子不平衡": 0.0,
        "润滑故障": 0.0,
        "齿轮异常": 0.0,
    }
    base.update(kwargs)
    return base


def test_clear_normal():
    result = decide(
        p_a_abnormal=0.05,
        p_b_abnormal=0.08,
        multiclass_proba=_probs(**{"正常": 0.92, "轴承磨损": 0.02, "转子不平衡": 0.02, "润滑故障": 0.02, "齿轮异常": 0.02}),
    )
    assert result["screening_result"] == "NORMAL"
    assert result["route"] == "l2_complete"
    assert "CLEAR_NORMAL" in result["reason_codes"]


def test_clear_abnormal():
    result = decide(
        p_a_abnormal=0.91,
        p_b_abnormal=0.88,
        multiclass_proba=_probs(**{"正常": 0.12, "轴承磨损": 0.78, "转子不平衡": 0.04, "润滑故障": 0.03, "齿轮异常": 0.03}),
    )
    assert result["screening_result"] == "ABNORMAL"
    assert result["preliminary_fault"] == "轴承磨损"
    assert result["route"] == "l3_review_recommended"
    assert result["status"] == "l3_review_recommended"
    assert "CLEAR_ABNORMAL" in result["reason_codes"]


def test_models_conflict():
    result = decide(
        p_a_abnormal=0.85,
        p_b_abnormal=0.15,
        multiclass_proba=_probs(**{"正常": 0.85, "轴承磨损": 0.05, "转子不平衡": 0.04, "润滑故障": 0.03, "齿轮异常": 0.03}),
    )
    assert result["screening_result"] == "CONFLICT"
    assert result["route"] == "l3_review_recommended"
    assert "MODELS_DISAGREE" in result["reason_codes"]


def test_high_gap_uncertain():
    # 同向非正常，但 gap>0.20 且 top1 不够明确 → UNCERTAIN
    result = decide(
        p_a_abnormal=0.92,
        p_b_abnormal=0.55,
        multiclass_proba=_probs(**{"正常": 0.45, "轴承磨损": 0.30, "转子不平衡": 0.10, "润滑故障": 0.08, "齿轮异常": 0.07}),
    )
    assert result["screening_result"] == "UNCERTAIN"
    assert "HIGH_PROBABILITY_GAP" in result["reason_codes"]


def test_low_top1_uncertain():
    result = decide(
        p_a_abnormal=0.82,
        p_b_abnormal=0.81,
        multiclass_proba=_probs(**{"正常": 0.19, "轴承磨损": 0.40, "转子不平衡": 0.21, "润滑故障": 0.10, "齿轮异常": 0.10}),
    )
    assert result["screening_result"] == "UNCERTAIN"
    assert "LOW_CLASS_PROBABILITY" in result["reason_codes"]


def test_multiclass_ambiguity():
    # top1 正常，但故障合计 > 0.5
    result = decide(
        p_a_abnormal=0.55,
        p_b_abnormal=0.55,
        multiclass_proba=_probs(**{"正常": 0.45, "轴承磨损": 0.20, "转子不平衡": 0.15, "润滑故障": 0.10, "齿轮异常": 0.10}),
    )
    assert result["screening_result"] == "UNCERTAIN"
    assert "MULTICLASS_AMBIGUITY" in result["reason_codes"]


def test_boundary_equal_half_is_uncertain_direction():
    result = decide(
        p_a_abnormal=0.5,
        p_b_abnormal=0.5,
        multiclass_proba=_probs(**{"正常": 0.5, "轴承磨损": 0.2, "转子不平衡": 0.1, "润滑故障": 0.1, "齿轮异常": 0.1}),
    )
    assert result["screening_result"] == "UNCERTAIN"
    assert result["binary_direction"] == "uncertain"
