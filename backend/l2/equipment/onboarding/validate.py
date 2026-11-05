"""Onboarding 草案校验。"""

from __future__ import annotations

from typing import Any

from l2.equipment.onboarding.draft import LEAKAGE_IDS, LEAKAGE_NAME_HINTS
from l2.equipment.registry import get_equipment_spec, has_equipment


def validate_draft(draft: dict[str, Any], *, user_overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    merged = apply_overrides(draft, user_overrides or {})
    errors: list[dict[str, str]] = []
    warnings: list[str] = list(merged.get("warnings") or [])

    equipment_type = str(merged.get("equipment_type_candidate") or "").strip()
    display_name = str(merged.get("display_name_candidate") or "").strip()
    if not equipment_type:
        errors.append({"code": "EQUIPMENT_TYPE_REQUIRED", "message": "必须提供 equipment_type。"})
    if not display_name:
        errors.append({"code": "DISPLAY_NAME_REQUIRED", "message": "必须提供设备显示名称。"})
    if equipment_type and ("/" in equipment_type or "\\" in equipment_type or ".." in equipment_type):
        errors.append({"code": "EQUIPMENT_TYPE_INVALID", "message": "equipment_type 非法。"})

    features = [f for f in (merged.get("feature_candidates") or []) if f.get("selected")]
    if not features:
        errors.append({"code": "FEATURES_REQUIRED", "message": "至少选择一个训练特征。"})
    for item in features:
        fid = str(item.get("feature_id") or "")
        name = str(item.get("name") or "")
        if item.get("leakage") or fid in LEAKAGE_IDS or name in LEAKAGE_NAME_HINTS:
            errors.append(
                {
                    "code": "LEAKAGE_FEATURE",
                    "message": f"字段 {name or fid} 属于泄漏字段，不能作为训练特征。",
                }
            )
        if int(item.get("numeric_count") or 0) <= 0:
            errors.append(
                {
                    "code": "FEATURE_NO_VALUES",
                    "message": f"特征 {name or fid} 没有有效数值。",
                }
            )

    normal = merged.get("normal_label_candidate")
    class_labels = [str(x) for x in (merged.get("class_labels") or [])]
    if not normal:
        errors.append({"code": "NORMAL_LABEL_REQUIRED", "message": "必须确认正常类别标签。"})
    elif class_labels and str(normal) not in class_labels:
        errors.append(
            {
                "code": "NORMAL_LABEL_NOT_IN_CLASSES",
                "message": f"正常标签 {normal} 不在已有故障类别中。",
            }
        )
    if class_labels and len(set(class_labels)) < 2:
        errors.append({"code": "CLASS_LABELS_INSUFFICIENT", "message": "至少需要正常与一种故障类别。"})
    if not merged.get("labeled_record_count"):
        warnings.append("当前数据缺少有效故障标签，无法训练监督分类模型。")

    schema_conflict = None
    if equipment_type and has_equipment(equipment_type):
        existing = get_equipment_spec(equipment_type)
        selected_names = [str(f.get("name") or f.get("feature_id")) for f in features]
        existing_names = list(existing.feature_names)
        if sorted(selected_names) != sorted(existing_names):
            schema_conflict = {
                "existing_schema_version": existing.schema_version,
                "existing_feature_count": existing.feature_count,
                "requested_feature_count": len(selected_names),
                "message": "已有设备 Schema 与本次特征集合不一致，将生成新 Schema 版本。",
            }
            warnings.append(schema_conflict["message"])

    readiness = dict(merged.get("training_readiness") or {})
    readiness["ready"] = (
        not errors
        and bool(features)
        and bool(normal)
        and len(class_labels) >= 2
        and int(merged.get("labeled_record_count") or 0) > 0
        and any(lbl != str(normal) for lbl in class_labels)
    )
    if int(merged.get("labeled_record_count") or 0) > 0 and int(merged.get("labeled_record_count") or 0) < 20:
        warnings.append("带标签样本偏少，训练可能失败。")

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "merged_draft": merged,
        "schema_conflict": schema_conflict,
        "training_readiness": readiness,
    }


def apply_overrides(draft: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged = dict(draft)
    if "equipment_type" in overrides or "equipment_type_candidate" in overrides:
        merged["equipment_type_candidate"] = str(
            overrides.get("equipment_type") or overrides.get("equipment_type_candidate") or ""
        ).strip()
    if "display_name" in overrides or "display_name_candidate" in overrides:
        merged["display_name_candidate"] = str(
            overrides.get("display_name") or overrides.get("display_name_candidate") or ""
        ).strip()
    if "normal_label" in overrides or "normal_label_candidate" in overrides:
        merged["normal_label_candidate"] = overrides.get("normal_label") or overrides.get(
            "normal_label_candidate"
        )
        merged["normal_label_confirmed"] = True
    if "target_field" in overrides or "target_candidate" in overrides:
        merged["target_candidate"] = str(
            overrides.get("target_field") or overrides.get("target_candidate") or merged.get("target_candidate")
        )
    if "selected_feature_ids" in overrides and isinstance(overrides["selected_feature_ids"], list):
        selected = {str(x) for x in overrides["selected_feature_ids"]}
        features = []
        for item in merged.get("feature_candidates") or []:
            copy = dict(item)
            copy["selected"] = str(copy.get("feature_id")) in selected and not copy.get("leakage")
            features.append(copy)
        merged["feature_candidates"] = features
        merged["feature_count"] = sum(1 for f in features if f.get("selected"))
    elif "feature_candidates" in overrides and isinstance(overrides["feature_candidates"], list):
        merged["feature_candidates"] = overrides["feature_candidates"]
        merged["feature_count"] = sum(1 for f in merged["feature_candidates"] if f.get("selected"))
    return merged
