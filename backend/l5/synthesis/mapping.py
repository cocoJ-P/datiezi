"""中英字段映射与设备类别解析。"""

from __future__ import annotations

from l5.prediction.contract import SHEET_TO_EQUIPMENT_TYPE, TARGET_COLUMNS
from l5.schema import BUSINESS_FIELD_LABELS_ZH, BUSINESS_FIELD_ORDER

ZH_TO_EN = {v: k for k, v in BUSINESS_FIELD_LABELS_ZH.items()}
EN_TO_ZH = dict(BUSINESS_FIELD_LABELS_ZH)

EQUIPMENT_TYPE_TO_CATEGORY = {
    "main_mill_motor": "主轧机电机",
    "gearbox": "主减速机/齿轮箱",
    "cooling_water_circulation_pump": "冷却水循环泵",
    "furnace_combustion_fan": "加热炉助燃风机",
    "high_pressure_descaling_pump": "高压除鳞泵",
    "caster_segment_drive_motor": "连铸机扇形段驱动电机",
}

CATEGORY_ALIASES = {
    "主减速机_齿轮箱": "主减速机/齿轮箱",
    "主减速机/齿轮箱": "主减速机/齿轮箱",
    "主轧机电机": "主轧机电机",
    "冷却水循环泵": "冷却水循环泵",
    "加热炉助燃风机": "加热炉助燃风机",
    "高压除鳞泵": "高压除鳞泵",
    "连铸机扇形段驱动电机": "连铸机扇形段驱动电机",
}


def normalize_equipment_category(raw: str | None, equipment_type: str | None = None) -> str | None:
    if raw:
        s = str(raw).strip()
        if s in CATEGORY_ALIASES:
            return CATEGORY_ALIASES[s]
        stem = s.replace("/", "_")
        if stem in SHEET_TO_EQUIPMENT_TYPE:
            et = SHEET_TO_EQUIPMENT_TYPE[stem]
            return EQUIPMENT_TYPE_TO_CATEGORY.get(et, s)
        if s in EQUIPMENT_TYPE_TO_CATEGORY.values():
            return s
    if equipment_type and equipment_type in EQUIPMENT_TYPE_TO_CATEGORY:
        return EQUIPMENT_TYPE_TO_CATEGORY[equipment_type]
    return None


def predictions_zh_to_en(preds: dict) -> dict:
    out = {}
    for zh, val in (preds or {}).items():
        en = ZH_TO_EN.get(zh)
        if en:
            out[en] = val
    return out


assert set(TARGET_COLUMNS) == set(BUSINESS_FIELD_LABELS_ZH.values())
assert len(BUSINESS_FIELD_ORDER) == 10
