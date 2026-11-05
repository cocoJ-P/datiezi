"""从 L1 PD 生成 EquipmentSpec 候选草案。"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from typing import Any

from l2.equipment.registry import list_equipment_types
from l2.prepared.l1_source import get_l1_prepared_meta, iter_l1_prepared_rows
from l2.prepared.mapping import resolve_equipment_type

LEAKAGE_IDS = {
    "fault_type",
    "fault_severity",
    "fault_location",
    "equipment_status",
    "diagnosis_confidence",
    "spectrum_description",
    "root_cause",
    "recommended_action",
    "priority",
    "impact_level",
    "record_id",
    "equipment_id",
    "collection_time",
    "timestamp",
    "dataset_split",
    "equipment_category",
}

LEAKAGE_NAME_HINTS = {
    "故障类型",
    "设备状态",
    "故障严重度",
    "故障位置",
    "根因分析",
    "诊断置信度",
    "建议措施",
    "优先级",
    "影响程度",
    "频谱描述",
    "记录编号",
    "设备编号",
    "采集时间",
    "数据集划分",
}

CONTROLLED_NORMAL_LABELS = {"正常", "normal", "Normal", "NORMAL", "OK", "ok"}


def content_key_for(
    *,
    pd_revision_id: str,
    equipment_category: str | None,
    equipment_group_id: str | None,
) -> str:
    raw = f"{pd_revision_id}|{equipment_category or ''}|{equipment_group_id or ''}|onboarding-v1"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def suggest_equipment_type(category: str | None, filename: str | None = None) -> tuple[str, str, list[str]]:
    """返回 (equipment_type_candidate, display_name, warnings)。"""
    warnings: list[str] = []
    display = (category or "").strip() or _filename_hint(filename) or "未命名设备"
    # 已注册 display_name 命中
    for spec in list_equipment_types():
        if display in {spec.display_name, spec.equipment_type}:
            return spec.equipment_type, spec.display_name, warnings
    slug = _slugify(display)
    if not slug or slug.startswith("eq_"):
        warnings.append("设备类别标识由系统建议，请确认后保存。")
    if not category:
        warnings.append("L1 未提供明确设备类别名称，请确认 display_name 与 equipment_type。")
    return slug, display, warnings


def build_draft_from_pd(
    *,
    pd_revision_id: str,
    equipment_category: str | None = None,
    equipment_group_id: str | None = None,
) -> dict[str, Any]:
    meta = get_l1_prepared_meta(pd_revision_id)
    category = equipment_category
    feature_stats: dict[str, dict[str, Any]] = {}
    label_counter: Counter[str] = Counter()
    record_count = 0
    field_headers: dict[str, str] = {}
    field_units: dict[str, str | None] = {}

    # 尝试从 P4 compiled 取 header/unit（失败则仅用 feature_id）
    try:
        field_headers, field_units = _load_field_meta(meta.prepare_run_id, meta.p4_run_id)
    except Exception:  # noqa: BLE001
        field_headers, field_units = {}, {}

    for row in iter_l1_prepared_rows(pd_revision_id):
        if category is None:
            category = row.get("equipment_category")
        if equipment_category is not None:
            row_cat = (row.get("equipment_category") or "").strip()
            if row_cat != str(equipment_category).strip():
                continue
        record_count += 1
        for fid, cell in (row.get("input_features") or {}).items():
            if fid in LEAKAGE_IDS:
                continue
            number = _scalar(cell)
            stats = feature_stats.setdefault(
                str(fid),
                {
                    "feature_id": str(fid),
                    "name": field_headers.get(str(fid)) or str(fid),
                    "dtype": "float",
                    "unit": field_units.get(str(fid)),
                    "source_field_id": str(fid),
                    "selected": True,
                    "numeric_count": 0,
                    "missing_count": 0,
                    "leakage": False,
                },
            )
            if number is None:
                stats["missing_count"] += 1
            else:
                stats["numeric_count"] += 1
        fault = _label_value((row.get("labels") or {}).get("fault_type"))
        if fault:
            label_counter[fault] += 1

    mapping = resolve_equipment_type(
        dataset_id=meta.dataset_id,
        original_filename=meta.original_filename,
        equipment_category=category,
        equipment_group_id=equipment_group_id,
    )
    if mapping.mapping_status == "confirmed" and mapping.equipment_type:
        equipment_type = mapping.equipment_type
        display_name = mapping.display_name or mapping.equipment_type
        type_warnings: list[str] = []
        already_registered = True
    else:
        equipment_type, display_name, type_warnings = suggest_equipment_type(
            category, meta.original_filename
        )
        already_registered = False

    features = []
    warnings = list(type_warnings)
    for fid, stats in sorted(feature_stats.items(), key=lambda x: x[0]):
        name = str(stats["name"])
        if name in LEAKAGE_NAME_HINTS or fid in LEAKAGE_IDS:
            stats["selected"] = False
            stats["leakage"] = True
            continue
        # 无任何数值样本则默认不选
        if stats["numeric_count"] <= 0:
            stats["selected"] = False
            warnings.append(f"特征 {name} 无有效数值，默认不纳入训练。")
        features.append(
            {
                "feature_id": fid,
                "name": name,
                "dtype": "float",
                "unit": stats.get("unit"),
                "required": True,
                "selected": bool(stats["selected"]),
                "source_field_id": fid,
                "l1_mapping_ref": fid,
                "numeric_count": stats["numeric_count"],
                "missing_count": stats["missing_count"],
                "leakage": False,
            }
        )

    selected_features = [f for f in features if f["selected"]]
    class_labels = sorted(label_counter.keys())
    normal_candidate = None
    normal_confirmed = False
    for label in class_labels:
        if label in CONTROLLED_NORMAL_LABELS or label == "正常":
            normal_candidate = "正常" if label == "正常" else label
            normal_confirmed = True
            break
    if normal_candidate is None and "正常" in class_labels:
        normal_candidate = "正常"
        normal_confirmed = True
    if normal_candidate is None:
        warnings.append("未能自动确定正常类别，请人工确认。")

    labeled_total = sum(label_counter.values())
    has_abnormal = any(lbl != (normal_candidate or "") for lbl in class_labels)
    training_ready = (
        bool(selected_features)
        and normal_confirmed
        and normal_candidate is not None
        and has_abnormal
        and labeled_total >= 20
        and len(class_labels) >= 2
    )
    if not has_abnormal:
        warnings.append("当前数据缺少非正常故障类别，无法训练监督分类模型。")
    if labeled_total == 0:
        warnings.append("当前数据缺少有效故障标签，无法训练监督分类模型。")
        training_ready = False
    if labeled_total and labeled_total < 20:
        warnings.append("带标签样本偏少，可能无法满足训练/校准/测试划分。")

    schema_version = f"{equipment_type}_features_v1"
    return {
        "equipment_type_candidate": equipment_type,
        "display_name_candidate": display_name,
        "schema_version_candidate": schema_version,
        "already_registered": already_registered,
        "mapping_status": mapping.mapping_status,
        "source_pd_revision": pd_revision_id,
        "dataset_id": meta.dataset_id,
        "original_filename": meta.original_filename,
        "equipment_category": category,
        "equipment_group_id": equipment_group_id,
        "record_count": record_count,
        "feature_candidates": features,
        "feature_count": len(selected_features),
        "target_candidate": "故障类型",
        "target_field_id": "fault_type",
        "normal_label_candidate": normal_candidate,
        "normal_label_confirmed": normal_confirmed,
        "class_labels": class_labels,
        "class_counts": dict(label_counter),
        "class_count": len(class_labels),
        "labeled_record_count": labeled_total,
        "training_readiness": {
            "ready": training_ready,
            "has_features": bool(selected_features),
            "has_normal_label": normal_confirmed,
            "has_abnormal_label": has_abnormal,
            "has_labels": labeled_total > 0,
            "min_samples_ok": labeled_total >= 20,
        },
        "warnings": warnings,
        "leakage_columns": sorted(LEAKAGE_NAME_HINTS),
    }


def _slugify(text: str) -> str:
    ascii_part = re.sub(r"[^a-zA-Z0-9]+", "_", text.strip().lower()).strip("_")
    if len(ascii_part) >= 3:
        return ascii_part[:64]
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:10]
    return f"eq_{digest}"


def _filename_hint(filename: str | None) -> str | None:
    if not filename:
        return None
    stem = PathStem(filename)
    return stem or None


def PathStem(filename: str) -> str:
    name = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if "." in name:
        name = name.rsplit(".", 1)[0]
    return name.strip()


def _scalar(cell: Any) -> float | None:
    if isinstance(cell, (int, float)) and not isinstance(cell, bool):
        number = float(cell)
        if number != number or number in {float("inf"), float("-inf")}:
            return None
        return number
    if not isinstance(cell, dict):
        return None
    value = cell.get("value")
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in {float("inf"), float("-inf")}:
        return None
    return number


def _label_value(cell: Any) -> str | None:
    if cell is None:
        return None
    if isinstance(cell, dict):
        value = cell.get("value")
        if value is not None and str(value).strip():
            return str(value).strip()
        text = cell.get("text")
        if text is not None and str(text).strip():
            return str(text).strip()
        return None
    text = str(cell).strip()
    return text or None


def _load_field_meta(prepare_run_id: str, p4_run_id: str) -> tuple[dict[str, str], dict[str, str | None]]:
    import sqlite3
    from pathlib import Path

    from app.services.l1_prepare.context import load_compiled
    from app.services.l1_store import data_dir

    connection = sqlite3.connect(str(Path(data_dir()) / "l1.sqlite3"), timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        p4 = connection.execute("SELECT * FROM p4_runs WHERE run_id = ?", (p4_run_id,)).fetchone()
        if p4 is None:
            return {}, {}
        compiled = load_compiled(connection, p4)
        headers: dict[str, str] = {}
        units: dict[str, str | None] = {}
        for fld in compiled.get("fields") or []:
            sid = fld.get("standard_field_id")
            if not sid:
                continue
            headers[str(sid)] = str(fld.get("header") or sid)
            unit = fld.get("target_unit") or fld.get("source_unit")
            units[str(sid)] = str(unit) if unit else None
        return headers, units
    finally:
        connection.close()
