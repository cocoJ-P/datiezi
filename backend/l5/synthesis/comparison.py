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
    contradict_modes = set()
    support_modes = set()
    for row in l3_reviews or []:
        if not isinstance(row, dict) or not row.get("fault_mode"):
            continue
        mode = str(row["fault_mode"])
        candidates.add(mode)
        verdict = str(row.get("verdict") or "").upper()
        if verdict == "CONTRADICT":
            contradict_modes.add(mode)
        if verdict in {"SUPPORT", "SUPPORTED", "LIKELY"}:
            support_modes.add(mode)
    if not candidates:
        return {
            "field": "故障类型",
            "consistency": "unavailable",
            "detail": "no_l2_l3_fault_candidates",
            "l5": pred,
            "candidates": [],
        }
    pred_s = str(pred)
    if pred_s in contradict_modes and pred_s not in support_modes:
        return {
            "field": "故障类型",
            "consistency": "conflicting",
            "detail": "l3_explicit_contradict",
            "l5": pred,
            "candidates": sorted(candidates),
            "note": "保留双方结果，不自动覆盖",
        }
    if pred_s in candidates:
        return {"field": "故障类型", "consistency": "consistent", "l5": pred, "candidates": sorted(candidates)}
    # 候选集合不含预测且无明确 CONTRADICT → 证据不足而非必然冲突
    return {
        "field": "故障类型",
        "consistency": "insufficient_evidence",
        "detail": "l5_not_in_l2_l3_candidates_no_explicit_contradict",
        "l5": pred,
        "candidates": sorted(candidates),
        "note": "保留双方结果，不自动覆盖",
    }


def _l4_candidate_rows(l4_assessment: dict[str, Any]) -> list[dict[str, Any]]:
    rows = (
        l4_assessment.get("ranked_candidates")
        or l4_assessment.get("candidates")
        or l4_assessment.get("candidate_reviews")
        or []
    )
    out = []
    for row in rows:
        if isinstance(row, dict):
            out.append(row)
        elif row:
            out.append({"label": str(row)})
    return out


def _candidate_texts(rows: list[dict[str, Any]]) -> list[str]:
    cands = []
    for row in rows:
        for key in ("label", "root_cause", "hypothesis", "cause_name", "name", "candidate_id"):
            if row.get(key):
                cands.append(str(row.get(key)))
        if row.get("summary"):
            cands.append(str(row.get("summary"))[:120])
        if row.get("review_summary"):
            cands.append(str(row.get("review_summary"))[:120])
        if row.get("causal_mechanism_summary"):
            cands.append(str(row.get("causal_mechanism_summary"))[:120])
    return [c for c in cands if c]


def _has_explicit_contradiction(rows: list[dict[str, Any]], l4_assessment: dict[str, Any]) -> bool:
    if l4_assessment.get("excluded_review_recommendations"):
        # 排除建议不等于否定 L5 模板，除非文本指向同一根因；保守：仅当有 contradicting_evidence
        pass
    for row in rows:
        ce = row.get("contradicting_evidence") or []
        if isinstance(ce, list) and len(ce) > 0:
            return True
        vs = str(row.get("validity_status") or "").lower()
        if vs in {"rejected", "refuted", "excluded", "contradicted"}:
            return True
    conf = str(l4_assessment.get("root_cause_confirmation") or "").lower()
    if conf in {"rejected", "refuted", "excluded"}:
        return True
    return False


def compare_root_cause(pred_text: Any, l4_assessment: dict[str, Any] | None) -> dict[str, Any]:
    """
    L5 根因模板 + L4 unconfirmed ≠ conflicting。
    仅当存在明确反证 / 拒绝，或已确认且与模板无相容文本时标 conflicting。
    """
    if not l4_assessment:
        return {
            "field": "根因分析",
            "consistency": "unavailable",
            "detail": "l4_reasoning_absent",
            "dataset_predicted_root_cause": pred_text,
            "l4_causal_assessment": None,
        }
    conf = l4_assessment.get("root_cause_confirmation") or l4_assessment.get("confirmation") or "unconfirmed"
    rows = _l4_candidate_rows(l4_assessment)
    for key in ("primary_root_cause", "selected_root_cause", "root_cause"):
        if l4_assessment.get(key):
            rows.append({"label": str(l4_assessment.get(key))})
    cands = _candidate_texts(rows)
    pred_s = str(pred_text or "")
    overlap = any(pred_s and (pred_s in c or c in pred_s) for c in cands)
    # 弱关键词相容（轴承/润滑等）仅作部分一致提示，不升格为 confirmed
    weak_tokens = ("轴承", "磨损", "润滑", "疲劳", "保持架", "转子", "齿轮", "不平衡", "正常")
    weak = bool(pred_s) and any(
        t in pred_s and any(t in c for c in cands) for t in weak_tokens
    )
    explicit_contra = _has_explicit_contradiction(rows, l4_assessment)
    conf_l = str(conf).lower()

    if overlap:
        cons, detail = "partially_consistent", "text_overlap_with_l4_candidate"
    elif explicit_contra:
        cons, detail = "conflicting", "l4_explicit_contradicting_evidence_or_rejection"
    elif conf_l in {"confirmed", "accepted"} and cands and not overlap and not weak:
        cons, detail = "conflicting", "l4_confirmed_different_root_cause"
    elif conf_l in {"unconfirmed", "insufficient", "unknown", ""} or not conf_l:
        # 未确认且无反证：证据不足（含无候选文本）
        cons, detail = "insufficient_evidence", "l5_template_with_l4_unconfirmed_no_refutation"
    elif weak:
        cons, detail = "partially_consistent", "weak_keyword_overlap_unconfirmed"
    else:
        cons, detail = "insufficient_evidence", "no_decisive_agreement_or_refutation"

    return {
        "field": "根因分析",
        "consistency": cons,
        "detail": detail,
        "dataset_predicted_root_cause": pred_text,
        "l4_candidates": cands[:8],
        "l4_root_cause_confirmation": conf,
        "note": "不得因模板预测将 L4 confirmation 改为 confirmed；unconfirmed≠conflicting",
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


def check_prediction_internal_consistency(preds: dict[str, Any]) -> dict[str, Any]:
    """检查十字段内部相容性：只标记，不修改预测。"""
    issues: list[dict[str, Any]] = []
    fault = str(preds.get("故障类型") or "")
    loc = str(preds.get("故障位置") or "")
    root = str(preds.get("根因分析") or "")
    status = str(preds.get("设备状态") or "")

    if status == "正常" and fault not in {"", "正常", "-"} and fault != "正常":
        issues.append(
            {
                "code": "status_normal_but_fault_nonzero",
                "consistency": "conflicting",
                "detail": f"设备状态=正常 但故障类型={fault}",
            }
        )
    if fault in {"正常", "-"} and loc not in {"", "-", "无", "正常"}:
        issues.append(
            {
                "code": "normal_fault_with_location",
                "consistency": "partially_consistent",
                "detail": f"故障类型={fault} 但给出位置={loc}",
            }
        )
    # 位置与根因部件词
    loc_tokens = ("驱动端", "非驱动端", "轴承", "转子", "齿轮", "电机", "风机", "泵")
    if loc and loc not in {"-", "无"} and root:
        # 若位置含部件词，根因完全不含该词且含其他部件词 → 标记
        loc_hit = [t for t in loc_tokens if t in loc]
        root_hit = [t for t in loc_tokens if t in root]
        if loc_hit and root_hit and not set(loc_hit) & set(root_hit):
            issues.append(
                {
                    "code": "location_root_component_mismatch",
                    "consistency": "conflicting",
                    "detail": f"故障位置部件线索 {loc_hit} 与根因模板 {root_hit} 不相交",
                    "fault_location": loc,
                    "root_cause_analysis": root,
                }
            )
    if fault and fault not in {"正常", "-"} and root and fault not in root:
        # 故障类型关键词未出现在根因：仅提示
        if not any(tok in root for tok in (fault[:2],) if len(fault) >= 2):
            issues.append(
                {
                    "code": "fault_type_not_mentioned_in_root_template",
                    "consistency": "insufficient_evidence",
                    "detail": "故障类型字符串未出现在根因模板中（可能仍语义相关）",
                    "fault_type": fault,
                }
            )

    worst = "consistent"
    order = {"consistent": 0, "partially_consistent": 1, "insufficient_evidence": 2, "conflicting": 3, "unavailable": 4}
    for iss in issues:
        if order.get(iss["consistency"], 0) > order.get(worst, 0):
            worst = iss["consistency"]
    return {
        "field": "十字段内部相容性",
        "consistency": worst if issues else "consistent",
        "detail": "prediction_internal_check_mark_only_no_overwrite",
        "issues": issues,
        "note": "发现矛盾只标记，不用答案或 LLM 改写十字段",
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
        check_prediction_internal_consistency(preds),
    ]

    counts: dict[str, int] = {}
    for row in field_comparisons:
        counts[row["consistency"]] = counts.get(row["consistency"], 0) + 1
    unresolved = [r for r in field_comparisons if r["consistency"] in {"conflicting", "insufficient_evidence"}]

    return {
        "field_comparisons": field_comparisons,
        "consistency_summary": counts,
        "unresolved_conflicts": unresolved,
        "engine": "deterministic_comparison_v1_1",
        "used_ground_truth_labels": False,
        "semantics": {
            "consistent": "compatible_conclusion_and_evidence",
            "partially_consistent": "partial_agreement",
            "conflicting": "substantive_contradiction",
            "insufficient_evidence": "cannot_confirm_or_refute",
            "unavailable": "upstream_reasoning_absent",
            "l5_root_plus_l4_unconfirmed": "insufficient_evidence_not_conflicting",
        },
    }
