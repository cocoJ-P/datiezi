"""双模型一致性判断与分流（确定性规则，无 LLM）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DecisionThresholds:
    clear_probability_threshold: float = 0.80
    fault_top1_threshold: float = 0.70
    max_probability_gap: float = 0.20
    clear_normal_ceiling: float = 0.20


def _direction(prob_abnormal: float) -> str:
    if prob_abnormal > 0.5:
        return "abnormal"
    if prob_abnormal < 0.5:
        return "normal"
    return "uncertain"


def decide(
    *,
    p_a_abnormal: float,
    p_b_abnormal: float,
    multiclass_proba: dict[str, float],
    thresholds: DecisionThresholds | dict[str, float] | None = None,
    normal_label: str = "正常",
) -> dict[str, Any]:
    cfg = thresholds if isinstance(thresholds, DecisionThresholds) else DecisionThresholds(**(thresholds or {}))
    ranked = sorted(multiclass_proba.items(), key=lambda item: item[1], reverse=True)
    top1_label, top1_probability = ranked[0]
    top2_probability = ranked[1][1] if len(ranked) > 1 else 0.0
    top1_top2_margin = float(top1_probability - top2_probability)
    probability_gap = abs(float(p_a_abnormal) - float(p_b_abnormal))
    dir_a = _direction(float(p_a_abnormal))
    dir_b = _direction(float(p_b_abnormal))
    top1_is_normal = top1_label == normal_label
    aggregate_vs_top1_mismatch = (dir_b == "abnormal" and top1_is_normal) or (dir_b == "normal" and not top1_is_normal)

    reason_codes: list[str] = []
    if dir_a == dir_b and dir_a != "uncertain":
        reason_codes.append("MODELS_AGREE")
    if dir_a != dir_b and "uncertain" not in {dir_a, dir_b}:
        reason_codes.append("MODELS_DISAGREE")
    if probability_gap > cfg.max_probability_gap:
        reason_codes.append("HIGH_PROBABILITY_GAP")
    if top1_probability < cfg.fault_top1_threshold and not top1_is_normal:
        reason_codes.append("LOW_CLASS_PROBABILITY")
    if aggregate_vs_top1_mismatch:
        reason_codes.append("MULTICLASS_AMBIGUITY")

    # A. NORMAL
    if (
        p_a_abnormal <= cfg.clear_normal_ceiling
        and p_b_abnormal <= cfg.clear_normal_ceiling
        and probability_gap <= cfg.max_probability_gap
        and top1_is_normal
    ):
        reason_codes.append("CLEAR_NORMAL")
        return _result(
            screening_result="NORMAL",
            preliminary_fault="正常",
            status="l2_complete",
            route="l2_complete",
            reason_codes=reason_codes,
            p_a=p_a_abnormal,
            p_b=p_b_abnormal,
            gap=probability_gap,
            top1_label=top1_label,
            top1_probability=top1_probability,
            top2_probability=top2_probability,
            top1_top2_margin=top1_top2_margin,
            dir_a=dir_a,
            dir_b=dir_b,
            ranked=ranked,
        )

    # B. ABNORMAL
    if (
        p_a_abnormal >= cfg.clear_probability_threshold
        and p_b_abnormal >= cfg.clear_probability_threshold
        and probability_gap <= cfg.max_probability_gap
        and not top1_is_normal
        and top1_probability >= cfg.fault_top1_threshold
    ):
        reason_codes.append("CLEAR_ABNORMAL")
        return _result(
            screening_result="ABNORMAL",
            preliminary_fault=top1_label,
            status="l2_complete",
            route="l2_complete",
            reason_codes=reason_codes,
            p_a=p_a_abnormal,
            p_b=p_b_abnormal,
            gap=probability_gap,
            top1_label=top1_label,
            top1_probability=top1_probability,
            top2_probability=top2_probability,
            top1_top2_margin=top1_top2_margin,
            dir_a=dir_a,
            dir_b=dir_b,
            ranked=ranked,
        )

    # C. CONFLICT
    if dir_a != dir_b and "uncertain" not in {dir_a, dir_b}:
        return _result(
            screening_result="CONFLICT",
            preliminary_fault=None,
            status="l3_review_recommended",
            route="l3_review_recommended",
            reason_codes=reason_codes or ["MODELS_DISAGREE"],
            p_a=p_a_abnormal,
            p_b=p_b_abnormal,
            gap=probability_gap,
            top1_label=top1_label,
            top1_probability=top1_probability,
            top2_probability=top2_probability,
            top1_top2_margin=top1_top2_margin,
            dir_a=dir_a,
            dir_b=dir_b,
            ranked=ranked,
        )

    # D. UNCERTAIN
    if not reason_codes:
        reason_codes.append("LOW_CLASS_PROBABILITY")
    return _result(
        screening_result="UNCERTAIN",
        preliminary_fault=None if aggregate_vs_top1_mismatch else (None if top1_probability < cfg.fault_top1_threshold else top1_label),
        status="l3_review_recommended",
        route="l3_review_recommended",
        reason_codes=reason_codes,
        p_a=p_a_abnormal,
        p_b=p_b_abnormal,
        gap=probability_gap,
        top1_label=top1_label,
        top1_probability=top1_probability,
        top2_probability=top2_probability,
        top1_top2_margin=top1_top2_margin,
        dir_a=dir_a,
        dir_b=dir_b,
        ranked=ranked,
    )


def _result(
    *,
    screening_result: str,
    preliminary_fault: str | None,
    status: str,
    route: str,
    reason_codes: list[str],
    p_a: float,
    p_b: float,
    gap: float,
    top1_label: str,
    top1_probability: float,
    top2_probability: float,
    top1_top2_margin: float,
    dir_a: str,
    dir_b: str,
    ranked: list[tuple[str, float]],
) -> dict[str, Any]:
    # 去重并保持顺序
    codes: list[str] = []
    for code in reason_codes:
        if code not in codes:
            codes.append(code)
    return {
        "screening_result": screening_result,
        "preliminary_fault": preliminary_fault,
        "status": status,
        "route": route,
        "reason_codes": codes,
        "p_a_abnormal": float(p_a),
        "p_b_abnormal": float(p_b),
        "probability_gap": float(gap),
        "binary_direction": dir_a,
        "multiclass_aggregate_direction": dir_b,
        "multiclass_top1": top1_label,
        "top1_probability": float(top1_probability),
        "top2_probability": float(top2_probability),
        "top1_top2_margin": float(top1_top2_margin),
        "ranked_classes": [{"label": label, "probability": float(prob)} for label, prob in ranked],
    }
