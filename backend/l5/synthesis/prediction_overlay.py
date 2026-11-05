"""将 L5P 监督预测写入 FinalDiagnosisAssessment 十字段主值（保留上游视图于 extras）。"""

from __future__ import annotations

from typing import Any

from l5.schema import BUSINESS_FIELD_ORDER, field_slot
from l5.synthesis.mapping import EN_TO_ZH, normalize_equipment_category, predictions_zh_to_en


def apply_supervised_predictions(
    assessment: dict[str, Any],
    prediction: dict[str, Any] | None,
    *,
    prefer_prediction: bool = True,
) -> dict[str, Any]:
    """
    十字段主 value 来自监督预测；原聚合结果放入 extras.upstream_aggregation。
    不修改 root_cause_confirmation（保持 unconfirmed）。
    """
    if not prefer_prediction or not prediction:
        assessment.setdefault("limitations", []).append("supervised_prediction_not_applied")
        return assessment

    preds_zh = prediction.get("predictions") or {}
    preds_en = predictions_zh_to_en(preds_zh)
    model_id = (prediction.get("model_versions") or {}).get("model_id")
    statuses = prediction.get("prediction_status") or {}
    sources = prediction.get("prediction_sources") or {}

    assessment["supervised_prediction"] = {
        "schema": prediction.get("schema"),
        "model_id": model_id,
        "model_versions": prediction.get("model_versions"),
        "model_input_fingerprint": prediction.get("model_input_fingerprint"),
        "prediction_status": statuses,
        "prediction_sources": sources,
        "equipment_category": prediction.get("equipment_category"),
        "limitations": prediction.get("limitations") or [],
        "field_semantics": prediction.get("field_semantics") or {},
    }
    assessment["source_versions"] = dict(assessment.get("source_versions") or {})
    assessment["source_versions"]["l5_prediction_model_id"] = model_id

    for en in BUSINESS_FIELD_ORDER:
        zh = EN_TO_ZH[en]
        prev = dict(assessment.get("fields", {}).get(en) or {})
        if zh not in preds_zh:
            assessment.setdefault("limitations", []).append(f"prediction_missing_field:{zh}")
            continue
        val = preds_zh[zh]
        extras = {
            "upstream_aggregation": {
                "value": prev.get("value"),
                "display": prev.get("display"),
                "source_layer": prev.get("source_layer"),
                "uncertainty": prev.get("uncertainty"),
                "null_reason": prev.get("null_reason"),
            },
            "prediction_value": val,
            "source_model_id": model_id,
            "prediction_status": statuses.get(zh) or statuses.get(en) or "ok",
            "prediction_source": sources.get(zh) or sources.get(en),
            "equipment_type": prediction.get("equipment_type") or assessment.get("equipment_type"),
            "equipment_category": prediction.get("equipment_category"),
            "input_fingerprint": prediction.get("model_input_fingerprint"),
            "dataset_field_zh": zh,
        }
        if en == "diagnosis_confidence":
            extras["confidence_semantics"] = "dataset_supervised_regression_not_calibrated_probability"
        if en == "root_cause_analysis":
            extras["naming"] = "dataset_predicted_root_cause"
        if en == "priority":
            extras["label_space"] = "dataset_P0_P3"
        assessment["fields"][en] = field_slot(
            val,
            display=str(val),
            source_layer="l5_prediction",
            source_run_id=model_id,
            evidence_references=list(prev.get("evidence_references") or []),
            knowledge_references=list(prev.get("knowledge_references") or []),
            uncertainty="supervised_prediction_not_physical_confirmation",
            extras=extras,
        )

    # 完整性：预测齐全则 complete-ish
    missing = [en for en in BUSINESS_FIELD_ORDER if (assessment.get("fields") or {}).get(en, {}).get("value") is None]
    if not missing:
        assessment["completeness"] = "complete"
        assessment["outcome"] = "succeeded"
        assessment["review_status"] = "draft"
    assessment["root_cause_confirmation"] = "unconfirmed"
    assessment.setdefault("limitations", [])
    assessment["limitations"] = list(
        dict.fromkeys(
            list(assessment.get("limitations") or [])
            + [
                "cross_segment_primary_values_from_l5_supervised_prediction",
                "l3_l4_views_preserved_in_field_extras_upstream_aggregation",
                "root_cause_confirmation_remains_unconfirmed",
            ]
        )
    )
    return assessment


def try_predict_for_context(context: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """尝试对 L5 上下文做监督预测；失败返回 (None, reason)。"""
    from l5.prediction.service import L5PredictionError, L5PredictionService
    from l5.synthesis.features import extract_feature_map

    features, gaps = extract_feature_map(context)
    if features is None:
        return None, f"missing_features:{','.join(gaps[:8])}"
    cat = normalize_equipment_category(
        context.get("_resolved_equipment_category")
        or (context.get("evidence_bundle") or {}).get("equipment_category")
        or context.get("equipment_category"),
        context.get("equipment_type"),
    )
    if not cat:
        return None, "equipment_category_unresolved"
    try:
        svc = L5PredictionService()
        pred = svc.predict_record(
            record_id=str(context.get("record_id")),
            equipment_category=cat,
            equipment_type=context.get("equipment_type"),
            equipment_id=context.get("equipment_id"),
            features=features,
        )
        return pred, None
    except L5PredictionError as exc:
        return None, f"{exc.code}:{exc.message}"
    except Exception as exc:  # noqa: BLE001
        return None, f"prediction_failed:{exc}"
