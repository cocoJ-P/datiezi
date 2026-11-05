"""Canonical Knowledge Model 与设备适用范围。"""

from __future__ import annotations

from typing import Any, Literal

KnowledgeType = Literal[
    "equipment_fact",
    "component_relation",
    "fault_mode",
    "diagnostic_rule",
    "expert_case",
    "equipment_link",
    "retrieval_chunk",
    "symptom",
    "measurement",
]

ValidationStatus = Literal[
    "verified_source",
    "extracted_pending_review",
    "conflicting",
    "unsupported",
]

# L2/L3 equipment_type → 知识库中文别名（仅人工确认过的映射，禁止模糊联想）
EQUIPMENT_SCOPE_ALIASES: dict[str, tuple[str, ...]] = {
    "main_mill_motor": (
        "主轧机电机",
        "棒线主轧机电机",
        "主电机",
        "YKK 800-4",
        "YKK800-4",
    ),
    "high_pressure_descaling_pump": (
        "高压除鳞泵",
        "除鳞泵",
    ),
    "cooling_water_pump": (
        "冷却水循环泵",
        "棒线冷却水循环泵",
        "冷却泵",
    ),
    "main_mill_gearbox": (
        "主减速机",
        "棒线主减速机",
        "齿轮箱",
        "棒线齿轮箱",
    ),
}

# 明确不自动映射到当前 PED 设备的相似设备（冷却泵 ≠ 除鳞泵）
EXCLUDED_CROSS_EQUIPMENT = (
    "冷却水循环泵",
    "棒线冷却水循环泵",
    "主减速机",
    "齿轮箱",
    "棒线主轧机电机",
    "主轧机电机",
    "高压除鳞泵",
)


def scopes_for_equipment_type(equipment_type: str) -> list[str]:
    return list(EQUIPMENT_SCOPE_ALIASES.get(equipment_type, ()))


def equipment_type_matches_text(equipment_type: str, text: str) -> bool | None:
    """True=适用，False=明确不适用，None=无法判断（保守保留）。"""
    blob = text or ""
    aliases = scopes_for_equipment_type(equipment_type)
    if aliases and any(a in blob for a in aliases):
        return True

    # 文本明确指向其他已登记设备族时，禁止跨设备套用
    for other_type, other_aliases in EQUIPMENT_SCOPE_ALIASES.items():
        if other_type == equipment_type:
            continue
        if any(a in blob for a in other_aliases):
            return False

    for banned in EXCLUDED_CROSS_EQUIPMENT:
        if banned in blob and equipment_type == "high_pressure_descaling_pump":
            # 冷却泵/电机/齿轮箱知识不得套用到除鳞泵
            if "除鳞" not in blob and "高压" not in blob:
                return False
        if banned in blob and equipment_type == "main_mill_motor":
            if "主轧机电机" not in blob and "主电机" not in blob and "YKK" not in blob:
                if "齿轮箱" in blob or "冷却水" in blob or "除鳞" in blob:
                    return False
    return None


def make_knowledge_item(
    *,
    knowledge_id: str,
    knowledge_type: str,
    canonical_name: str,
    content: str,
    source_document_id: str,
    source_location: dict[str, Any],
    source_hash: str,
    knowledge_version: str,
    equipment_scope: list[str] | None = None,
    component_scope: list[str] | None = None,
    fault_mode_scope: list[str] | None = None,
    applicability: str = "scoped",
    validation_status: ValidationStatus = "verified_source",
    canonical_of: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = {
        "knowledge_id": knowledge_id,
        "knowledge_type": knowledge_type,
        "canonical_name": canonical_name,
        "equipment_scope": equipment_scope or [],
        "component_scope": component_scope or [],
        "fault_mode_scope": fault_mode_scope or [],
        "content": content,
        "source_document_id": source_document_id,
        "source_location": source_location,
        "source_hash": source_hash,
        "knowledge_version": knowledge_version,
        "applicability": applicability,
        "validation_status": validation_status,
        "canonical_of": canonical_of,
    }
    if extra:
        body["extra"] = extra
    return body
