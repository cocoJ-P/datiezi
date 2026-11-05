"""将 L5InputContext 聚合为 FinalDiagnosisAssessment（确定性，不填造等级）。"""

from __future__ import annotations

from typing import Any

from l5.recommendations import build_verification_recommendations
from l5.schema import BUSINESS_FIELD_ORDER, empty_assessment, field_slot


def _screening_display(screening: str | None) -> tuple[Any, str | None, str | None]:
    s = str(screening or "").upper()
    if s == "NORMAL":
        return "NORMAL", "L2 筛查：NORMAL", None
    if s == "ABNORMAL":
        return "ABNORMAL", "L2 筛查：ABNORMAL", None
    if s == "CONFLICT":
        return "CONFLICT", "L2 筛查：CONFLICT（待确认，不自动转为 ABNORMAL/NORMAL）", "screening_conflict"
    if s == "UNCERTAIN":
        return "UNCERTAIN", "L2 筛查：UNCERTAIN（不确定，不自动转为 ABNORMAL/NORMAL）", "screening_uncertain"
    if s:
        return s, f"L2 筛查：{s}", "screening_nonstandard"
    return None, None, "l2_screening_missing"


def _evidence_ids_from_bundle(bundle: dict[str, Any], limit: int = 12) -> list[str]:
    out: list[str] = []
    for item in bundle.get("evidence_items") or []:
        if isinstance(item, dict) and item.get("evidence_id"):
            out.append(str(item["evidence_id"]))
        if len(out) >= limit:
            break
    return out


def build_final_diagnosis(
    context: dict[str, Any],
    *,
    l5_run_id: str,
) -> dict[str, Any]:
    record_id = str(context.get("record_id"))
    source_run_ids = context.get("source_run_ids") or {}
    source_versions = context.get("source_versions") or {}
    availability = context.get("source_availability") or {}
    assessment = empty_assessment(
        l5_run_id=l5_run_id,
        record_id=record_id,
        source_run_ids=source_run_ids,
        source_versions=source_versions,
        input_fingerprint=str(context.get("input_fingerprint") or ""),
    )
    assessment["equipment_id"] = context.get("equipment_id")
    assessment["equipment_type"] = context.get("equipment_type")
    assessment["source_availability"] = availability
    limitations = list(context.get("limitations") or [])

    bundle = context.get("evidence_bundle") or {}
    l2_result = ((context.get("l2_record") or {}).get("result") or {}) if context.get("l2_record") else {}
    decision = l2_result.get("decision") or {}
    screening = decision.get("screening_result") or (context.get("l2_record") or {}).get("screening_result")
    diag_id = source_run_ids.get("diagnosis_run_id")
    analysis_id = source_run_ids.get("analysis_run_id")
    l4_id = source_run_ids.get("l4_run_id")

    # 1 设备状态
    value, display, unc = _screening_display(screening)
    if value is None:
        assessment["fields"]["equipment_status"] = field_slot(
            None, source_layer="l2", source_run_id=diag_id, null_reason=unc or "l2_unavailable"
        )
    else:
        assessment["fields"]["equipment_status"] = field_slot(
            value,
            display=display,
            source_layer="l2",
            source_run_id=diag_id,
            uncertainty=unc,
            extras={
                "preliminary_fault": decision.get("preliminary_fault"),
                "route": decision.get("route"),
            },
        )

    # 2 故障类型
    fault_rows = []
    for row in bundle.get("fault_type_review") or []:
        if isinstance(row, dict) and row.get("fault_mode"):
            fault_rows.append(
                {
                    "fault_mode": row.get("fault_mode"),
                    "verdict": row.get("verdict"),
                    "support_count": row.get("support_count"),
                    "contradict_count": row.get("contradict_count"),
                    "source": "l3_fault_type_review",
                }
            )
    prelim = decision.get("preliminary_fault")
    if prelim:
        fault_rows.append({"fault_mode": prelim, "verdict": None, "source": "l2_preliminary_fault"})
    l3r = context.get("l3_reasoning_assessment") or {}
    ai_review_done = bool(l3r)
    sufficiency = bundle.get("evidence_sufficiency")
    if fault_rows:
        assessment["fields"]["fault_type"] = field_slot(
            fault_rows,
            display="；".join(
                f"{r.get('fault_mode')}({r.get('verdict') or r.get('source')})" for r in fault_rows[:6]
            ),
            source_layer="l3" if availability.get("l3_core") else "l2",
            source_run_id=analysis_id or diag_id,
            evidence_references=_evidence_ids_from_bundle(bundle),
            uncertainty="multi_candidate_not_confirmed",
            extras={
                "l2_preliminary_fault": prelim,
                "l3_reasoning_executed": ai_review_done,
                "evidence_sufficiency": sufficiency,
            },
        )
    else:
        assessment["fields"]["fault_type"] = field_slot(
            None,
            source_layer="l2",
            source_run_id=diag_id,
            null_reason="no_fault_candidate",
            extras={"l3_reasoning_executed": ai_review_done, "evidence_sufficiency": sufficiency},
        )

    # 3 故障严重度 — 禁止编造等级
    sev = bundle.get("severity_assessment") or {}
    sev_level = sev.get("severity_level")
    assessment["fields"]["fault_severity"] = field_slot(
        None if sev_level in (None, "", []) else sev_level,
        display=None,
        source_layer="l3",
        source_run_id=analysis_id,
        evidence_references=list(sev.get("supporting_evidence_ids") or [])[:12]
        if isinstance(sev.get("supporting_evidence_ids"), list)
        else _evidence_ids_from_bundle(bundle, 8),
        null_reason=None if sev_level not in (None, "", []) else "no_validated_severity_scale",
        uncertainty="threshold_exceedance_is_symptom_not_grade",
        extras={
            "symptom_notes": sev.get("notes") or sev.get("signals") or sev.get("summary"),
            "raw_severity_assessment_keys": sorted([k for k in sev.keys()])[:20] if isinstance(sev, dict) else [],
        },
    )

    # 4 故障位置
    loc = bundle.get("location_candidates") or {}
    candidates = loc.get("candidates") if isinstance(loc, dict) else None
    if candidates:
        assessment["fields"]["fault_location"] = field_slot(
            candidates,
            display="；".join(str(c.get("component") or c) for c in candidates[:5] if isinstance(c, (dict, str))),
            source_layer="l3",
            source_run_id=analysis_id,
            uncertainty="location_candidate_not_confirmed",
            extras={"note": "候选部件 ≠ 已确认故障位置"},
        )
    else:
        assessment["fields"]["fault_location"] = field_slot(
            None,
            source_layer="l3",
            source_run_id=analysis_id,
            null_reason="location_candidates_insufficient",
            extras={"limitations": (loc.get("limitations") if isinstance(loc, dict) else None)},
        )

    # 5 诊断置信度 — 禁止伪造综合百分比
    binary = l2_result.get("binary_classifier") or {}
    multi = l2_result.get("multiclass_classifier") or {}
    consistency = l2_result.get("consistency") or {}
    assessment["fields"]["diagnosis_confidence"] = field_slot(
        None,
        source_layer="l2+l3+l4",
        source_run_id=diag_id or analysis_id,
        null_reason="no_unified_confidence_definition",
        extras={
            "l2_binary_probabilities": binary.get("probabilities"),
            "l2_multiclass_top1": multi.get("top1") or multi.get("predicted_label"),
            "l2_multiclass_probabilities": multi.get("probabilities"),
            "l2_consistency": consistency,
            "l3_evidence_sufficiency": sufficiency,
            "l4_root_cause_confirmation": (context.get("root_cause_assessment") or {}).get(
                "root_cause_confirmation", "unconfirmed"
            ),
            "note": "以上为分项分数/充分性，不是统一诊断置信度百分比",
        },
    )

    # 6 频谱描述
    spectral = bundle.get("spectral_feature_assessment") or {}
    if spectral:
        assessment["fields"]["spectral_description"] = field_slot(
            {
                "observed_scalar_features": spectral.get("observed") or spectral.get("features") or spectral.get("summary"),
                "unverified": spectral.get("unverified") or [],
                "limitations": spectral.get("limitations") or [],
            },
            display=str(spectral.get("summary") or spectral.get("note") or "见 extras 中实测标量与未验证项"),
            source_layer="l3",
            source_run_id=analysis_id,
            evidence_references=_evidence_ids_from_bundle(bundle),
            uncertainty="scalar_features_only" if spectral.get("unverified") else None,
            extras={"knowledge_vs_measurement": "不得将知识参考当作本机完整频谱结论"},
        )
    else:
        assessment["fields"]["spectral_description"] = field_slot(
            None,
            source_layer="l3",
            source_run_id=analysis_id,
            null_reason="spectral_assessment_unavailable",
        )

    # 7 根因分析
    rca = context.get("root_cause_assessment") or {}
    cra = context.get("causal_reasoning_assessment") or {}
    cands = rca.get("root_cause_candidates") or []
    if cands:
        slim = []
        for c in cands:
            if not isinstance(c, dict):
                continue
            slim.append(
                {
                    "candidate_id": c.get("candidate_id"),
                    "cause_name": c.get("cause_name"),
                    "related_fault_type": c.get("related_fault_type"),
                    "causal_knowledge_id": c.get("causal_knowledge_id"),
                    "causal_path_id": c.get("causal_path_id"),
                    "applicability_status": c.get("applicability_status"),
                    "same_layer_as_resulting_fault": c.get("same_layer_as_resulting_fault"),
                    "supporting_evidence_ids": c.get("supporting_evidence_ids") or [],
                    "fault_manifestation_evidence_ids": c.get("fault_manifestation_evidence_ids") or [],
                    "root_cause_confirmation": "unconfirmed",
                }
            )
        reviews = []
        for r in cra.get("candidate_reviews") or []:
            if isinstance(r, dict):
                reviews.append(
                    {
                        "candidate_id": r.get("candidate_id"),
                        "validity_status": r.get("validity_status"),
                        "review_summary": r.get("review_summary"),
                    }
                )
        assessment["fields"]["root_cause_analysis"] = field_slot(
            {"candidates": slim, "reasoning_reviews": reviews or None},
            display=f"{len(slim)} 个未确认根因候选"
            + (f"；含 L4 Reasoning 比较" if reviews else "（未绑定 L4 Reasoning）"),
            source_layer="l4",
            source_run_id=l4_id,
            evidence_references=[
                eid
                for c in slim
                for eid in (c.get("supporting_evidence_ids") or []) + (c.get("fault_manifestation_evidence_ids") or [])
            ][:20],
            knowledge_references=[c.get("causal_knowledge_id") for c in slim if c.get("causal_knowledge_id")],
            uncertainty="root_cause_unconfirmed",
            extras={
                "root_cause_confirmation": "unconfirmed",
                "l4_reasoning_bound": bool(availability.get("l4_reasoning")),
                "impact_propagation_status": (rca.get("impact_assessment") or {}).get("propagation_status"),
            },
        )
    else:
        assessment["fields"]["root_cause_analysis"] = field_slot(
            None,
            source_layer="l4",
            source_run_id=l4_id,
            null_reason="l4_core_unavailable_or_no_candidates",
            extras={"root_cause_confirmation": "unconfirmed"},
        )
    assessment["root_cause_confirmation"] = "unconfirmed"

    # 8 建议措施 — 仅验证建议
    verifications = build_verification_recommendations(context)
    assessment["verification_recommendations"] = verifications
    assessment["fields"]["recommended_actions"] = field_slot(
        [{"recommendation_id": v["recommendation_id"], "type": "verification", "description": v["description"]} for v in verifications],
        display=f"{len(verifications)} 条验证建议（非维修指令）" if verifications else "暂无自动验证建议",
        source_layer="l3+l4",
        source_run_id=analysis_id or l4_id,
        null_reason=None if verifications else "no_missing_evidence_signals",
        extras={
            "auto_issued_types": ["verification"],
            "repair_actions_auto_issued": False,
            "safety_orders_auto_issued": False,
            "note": "维修建议与正式安全处置不在 L5a 自动签发",
        },
    )

    # 9 优先级 — 强制 null
    assessment["fields"]["priority"] = field_slot(
        None,
        source_layer="l5a",
        source_run_id=l5_run_id,
        null_reason="no_validated_p0_p3_rules",
        extras={"note": "禁止使用 L1 流水线 P0–P3 或答案列代替维修优先级"},
    )

    # 10 影响程度 — 展示观察/潜在，等级 null
    impact = rca.get("impact_assessment") or {}
    if impact:
        assessment["fields"]["impact_degree"] = field_slot(
            None,
            source_layer="l4",
            source_run_id=l4_id,
            null_reason="no_validated_impact_degree_scale",
            extras={
                "observed_impact": impact.get("observed_impact"),
                "potential_impact": impact.get("potential_impact"),
                "unknown_impact": impact.get("unknown_impact"),
                "propagation_status": impact.get("propagation_status") or "unknown",
                "mapping_level": (impact.get("equipment_mapping") or {}).get("mapping_level"),
                "upstream_count": len(impact.get("upstream_dependencies") or []),
                "downstream_count": len(impact.get("downstream_connections") or []),
                "note": "类型级拓扑仅为潜在关联；不等于已发生传播或影响程度等级",
            },
        )
    else:
        assessment["fields"]["impact_degree"] = field_slot(
            None,
            source_layer="l4",
            source_run_id=l4_id,
            null_reason="l4_impact_unavailable",
        )

    # completeness / review / outcome
    null_business = [
        k
        for k in BUSINESS_FIELD_ORDER
        if assessment["fields"][k].get("value") is None
        and k not in {"fault_severity", "diagnosis_confidence", "priority", "impact_degree"}
    ]
    # 严重度/置信度/优先级/影响程度按设计常为 null，不算失败
    missing_core_layers = not availability.get("l2")
    if missing_core_layers:
        assessment["outcome"] = "failed"
        assessment["status"] = "failed"
        assessment["completeness"] = "incomplete"
        assessment["review_status"] = "incomplete"
    elif not availability.get("l3_core") or not availability.get("l4_core") or null_business:
        assessment["outcome"] = "partial"
        assessment["completeness"] = "partial"
        assessment["review_status"] = "draft"
    else:
        assessment["outcome"] = "completed"
        assessment["completeness"] = "partial"  # 因多项等级字段故意 null
        assessment["review_status"] = "draft"

    limitations.extend(
        [
            "L5a 不生成正式风险等级 / P0–P3 / 维修指令",
            "根因保持 unconfirmed",
            "fault_severity / priority / impact_degree / diagnosis_confidence 在无规则时保持 null",
        ]
    )
    if not availability.get("l3_reasoning"):
        limitations.append("未绑定 L3 Reasoning；故障类型未含 AI 复核结论")
    if not availability.get("l4_reasoning"):
        limitations.append("未绑定 L4 Reasoning；根因区仅 Core 候选")
    assessment["limitations"] = list(dict.fromkeys(limitations))

    # provenance index（轻量引用，不复制全量）
    assessment["provenance_index"] = {
        "equipment_status": {"layer": "l2", "run_id": diag_id, "path": "decision.screening_result"},
        "fault_type": {"layer": "l3|l2", "run_id": analysis_id or diag_id, "path": "fault_type_review"},
        "fault_severity": {"layer": "l3", "run_id": analysis_id, "path": "severity_assessment.severity_level=null"},
        "fault_location": {"layer": "l3", "run_id": analysis_id, "path": "location_candidates"},
        "diagnosis_confidence": {"layer": "multi", "run_id": diag_id, "path": "probabilities+sufficiency"},
        "spectral_description": {"layer": "l3", "run_id": analysis_id, "path": "spectral_feature_assessment"},
        "root_cause_analysis": {"layer": "l4", "run_id": l4_id, "path": "root_cause_candidates"},
        "recommended_actions": {"layer": "l5a", "run_id": l5_run_id, "path": "verification_recommendations"},
        "priority": {"layer": "l5a", "null_reason": "no_validated_p0_p3_rules"},
        "impact_degree": {"layer": "l4", "run_id": l4_id, "path": "impact_assessment", "grade": None},
    }
    return assessment
