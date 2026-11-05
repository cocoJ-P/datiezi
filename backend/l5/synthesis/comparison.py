"""确定性对照引擎（不用真实答案裁决）。"""

from __future__ import annotations

from typing import Any


def _status_l2_semantic(screening: str | None) -> str:
    s = str(screening or "").upper()
    if s == "NORMAL":
        return "aligns_with_正常_candidate"
    if s == "ABNORMAL":
        return "aligns_with_预警报警危险_bucket_not_1to1"
    if s in {"CONFLICT", "UNCERTAIN"}:
        return "l2_decision_state_not_dataset_four_class"
    return "unavailable"


def compare_status(pred_status: Any, l2_screening: str | None) -> dict[str, Any]:
    mapping = _status_l2_semantic(l2_screening)
    if not l2_screening:
        return {"field": "设备状态", "consistency": "unavailable", "detail": "l2_screening_missing", "l5": pred_status, "l2": None}
    if mapping == "l2_decision_state_not_dataset_four_class":
        return {
            "field": "设备状态",
            "consistency": "insufficient_evidence",
            "detail": "L2 CONFLICT/UNCERTAIN 不是数据集四级状态，仅语义提示",
            "l5": pred_status,
            "l2": l2_screening,
            "mapping_note": mapping,
        }
    # 粗语义：正常↔NORMAL；非正常↔ABNORMAL
    pred = str(pred_status)
    if l2_screening.upper() == "NORMAL" and pred == "正常":
        cons = "consistent"
    elif l2_screening.upper() == "ABNORMAL" and pred in {"预警", "报警", "危险"}:
        cons = "partially_consistent"
    elif l2_screening.upper() == "NORMAL" and pred != "正常":
        cons = "conflicting"
    elif l2_screening.upper() == "ABNORMAL" and pred == "正常":
        cons = "conflicting"
    else:
        cons = "partially_consistent"
    return {
        "field": "设备状态",
        "consistency": cons,
        "detail": mapping,
        "l5": pred_status,
        "l2": l2_screening,
        "note": "非一一映射；不得用 L2 覆盖 L5 四级预测",
    }


def compare_fault_type(pred: Any, l2_prelim: Any, l3_reviews: list[dict] | None) -> dict[str, Any]:
    candidates = set()
    if l2_prelim:
        candidates.add(str(l2_prelim))
    for row in l3_reviews or []:
        if isinstance(row, dict) and row.get("fault_mode"):
            candidates.add(str(row["fault_mode"]))
    if not candidates:
        return {
            "field": "故障类型",
            "consistency": "unavailable",
            "detail": "no_l2_l3_fault_candidates",
            "l5": pred,
            "candidates": [],
        }
    if str(pred) in candidates:
        return {"field": "故障类型", "consistency": "consistent", "l5": pred, "candidates": sorted(candidates)}
    return {
        "field": "故障类型",
        "consistency": "conflicting",
        "detail": "l5_not_in_l2_l3_candidates",
        "l5": pred,
        "candidates": sorted(candidates),
        "note": "保留双方结果，不自动覆盖",
    }


def compare_root_cause(pred_text: Any, l4_assessment: dict[str, Any] | None) -> dict[str, Any]:
    if not l4_assessment:
        return {
            "field": "根因分析",
            "consistency": "unavailable",
            "detail": "l4_reasoning_absent",
            "dataset_predicted_root_cause": pred_text,
            "l4_causal_assessment": None,
        }
    conf = l4_assessment.get("root_cause_confirmation") or l4_assessment.get("confirmation") or "unconfirmed"
    # 提取候选文本（含 L4 Reasoning 的 candidate_reviews）
    cands = []
    for key in ("primary_root_cause", "selected_root_cause", "root_cause"):
        if l4_assessment.get(key):
            cands.append(str(l4_assessment.get(key)))
    for row in (
        l4_assessment.get("ranked_candidates")
        or l4_assessment.get("candidates")
        or l4_assessment.get("candidate_reviews")
        or []
    ):
        if isinstance(row, dict):
            cands.append(
                str(
                    row.get("label")
                    or row.get("root_cause")
                    or row.get("hypothesis")
                    or row.get("candidate_id")
                    or row.get("name")
                    or ""
                )
            )
            if row.get("summary"):
                cands.append(str(row.get("summary"))[:120])
        elif row:
            cands.append(str(row))
    cands = [c for c in cands if c]
    pred_s = str(pred_text or "")
    if not cands:
        cons = "insufficient_evidence"
        detail = "l4_present_but_no_comparable_candidate_text"
    elif any(pred_s and (pred_s in c or c in pred_s) for c in cands):
        cons = "partially_consistent"
        detail = "text_overlap_with_l4_candidate"
    else:
        cons = "conflicting"
        detail = "no_text_overlap"
    return {
        "field": "根因分析",
        "consistency": cons,
        "detail": detail,
        "dataset_predicted_root_cause": pred_text,
        "l4_candidates": cands[:8],
        "l4_root_cause_confirmation": conf,
        "note": "不得因模板预测将 L4 confirmation 改为 confirmed",
    }


def compare_impact(pred_text: Any, l4_assessment: dict[str, Any] | None) -> dict[str, Any]:
    if not l4_assessment:
        return {
            "field": "影响程度",
            "consistency": "unavailable",
            "dataset_predicted_impact": pred_text,
            "l4_propagation_status": None,
        }
    prop = (
        l4_assessment.get("propagation_status")
        or (l4_assessment.get("impact_assessment") or {}).get("propagation_status")
        or (l4_assessment.get("impact") or {}).get("propagation_status")
    )
    return {
        "field": "影响程度",
        "consistency": "insufficient_evidence" if prop is None else "partially_consistent",
        "detail": "templates_and_propagation_are_separate_channels",
        "dataset_predicted_impact": pred_text,
        "l4_propagation_status": prop,
        "note": "禁止将模板反向写入 L4 ImpactAssessment",
    }


def compare_location(pred: Any, l3r: dict | None, l4a: dict | None) -> dict[str, Any]:
    clues = []
    for src in (l3r, l4a):
        if not isinstance(src, dict):
            continue
        for key in ("location_interpretation", "fault_location", "component_hints"):
            if src.get(key):
                clues.append(str(src.get(key))[:200])
    if not clues:
        return {
            "field": "故障位置",
            "consistency": "insufficient_evidence",
            "detail": "no_location_evidence_from_l3_l4",
            "l5": pred,
            "note": "无定位证据时不直接否定预测",
        }
    pred_s = str(pred)
    if any(pred_s and pred_s in c for c in clues):
        return {"field": "故障位置", "consistency": "partially_consistent", "l5": pred, "clues": clues[:5]}
    return {
        "field": "故障位置",
        "consistency": "insufficient_evidence",
        "detail": "clues_present_but_no_string_match",
        "l5": pred,
        "clues": clues[:5],
    }


def compare_spectrum(pred: Any, bundle: dict | None) -> dict[str, Any]:
    if not bundle:
        return {"field": "频谱描述", "consistency": "unavailable", "l5": pred}
    # 仅标量特征存在性
    has_amp = False
    for item in bundle.get("evidence_items") or []:
        if isinstance(item, dict) and item.get("feature_name") in {
            "包络谱峰值(g)",
            "BPFO幅值(g)",
            "1X转频幅值(mm/s)",
        }:
            has_amp = True
            break
    return {
        "field": "频谱描述",
        "consistency": "partially_consistent" if has_amp else "insufficient_evidence",
        "detail": "template_vs_scalar_features_only_no_full_fft",
        "l5": pred,
        "note": "不得把缺失 FFT/相位/2X 当作已测量",
    }


def run_deterministic_comparison(
    *,
    prediction: dict[str, Any] | None,
    l2_record: dict[str, Any] | None = None,
    evidence_bundle: dict[str, Any] | None = None,
    l3_reasoning: dict[str, Any] | None = None,
    l4_reasoning: dict[str, Any] | None = None,
) -> dict[str, Any]:
    preds = (prediction or {}).get("predictions") or {}
    l2_result = ((l2_record or {}).get("result") or {}) if l2_record else {}
    decision = l2_result.get("decision") or {}
    screening = decision.get("screening_result") or (l2_record or {}).get("screening_result")
    prelim = decision.get("preliminary_fault")
    reviews = (evidence_bundle or {}).get("fault_type_review") or (l3_reasoning or {}).get("fault_type_review")

    field_comparisons = [
        compare_status(preds.get("设备状态"), screening),
        compare_fault_type(preds.get("故障类型"), prelim, reviews if isinstance(reviews, list) else None),
        compare_location(preds.get("故障位置"), l3_reasoning, l4_reasoning),
        compare_spectrum(preds.get("频谱描述"), evidence_bundle),
        compare_root_cause(preds.get("根因分析"), l4_reasoning),
        {
            "field": "建议措施",
            "consistency": "partially_consistent" if preds.get("建议措施") else "unavailable",
            "detail": "dataset_recommendation_template_not_work_order",
            "l5": preds.get("建议措施"),
        },
        compare_impact(preds.get("影响程度"), l4_reasoning),
        {
            "field": "故障严重度",
            "consistency": "unavailable" if not preds.get("故障严重度") else "insufficient_evidence",
            "detail": "no_independent_severity_source_required",
            "l5": preds.get("故障严重度"),
        },
        {
            "field": "优先级",
            "consistency": "unavailable" if not preds.get("优先级") else "insufficient_evidence",
            "detail": "dataset_P0_P3_independent_of_enterprise_sla",
            "l5": preds.get("优先级"),
        },
        {
            "field": "诊断置信度",
            "consistency": "unavailable" if preds.get("诊断置信度") is None else "insufficient_evidence",
            "detail": "supervised_regression_target_not_calibrated_probability",
            "l5": preds.get("诊断置信度"),
        },
    ]

    counts: dict[str, int] = {}
    for row in field_comparisons:
        counts[row["consistency"]] = counts.get(row["consistency"], 0) + 1
    unresolved = [r for r in field_comparisons if r["consistency"] in {"conflicting", "insufficient_evidence"}]

    return {
        "field_comparisons": field_comparisons,
        "consistency_summary": counts,
        "unresolved_conflicts": unresolved,
        "engine": "deterministic_comparison_v1",
        "used_ground_truth_labels": False,
    }
