"""Causal Knowledge 设备适用性 v1.2：Unknown ≠ Absent；白名单不冒充 BOM。"""

from __future__ import annotations

from typing import Any

# 已知设备类型集合（出现在此集合外 → unresolved，不得因不在 scope 判 not_applicable）
KNOWN_EQUIPMENT_TYPES = frozenset(
    {
        "main_mill_motor",
        "high_pressure_descaling_pump",
    }
)

# 类型级参考结构：明确区分本体 / 关联传动，来源仅为人工类型知识，不是资产 BOM
EQUIPMENT_STRUCTURE_REF: dict[str, dict[str, Any]] = {
    "main_mill_motor": {
        "source": "type_reference_whitelist",
        "body_components": {"轴承", "转子", "联轴器", "润滑系统"},
        # 齿轮箱等为传动链关联，不代表电机本体已确认存在齿轮
        "associated_components": {"齿轮", "齿轮箱"},
        "explicitly_absent_body_components": {"机封"},
        "notes": "类型参考：电机本体不含机封；齿轮属关联传动，未确认 BOM。",
    },
    "high_pressure_descaling_pump": {
        "source": "type_reference_whitelist",
        "body_components": {"轴承", "转子", "机封", "叶轮", "润滑系统"},
        "associated_components": set(),
        "explicitly_absent_body_components": {"齿轮", "齿轮箱"},
        "notes": "类型参考：泵本体可含机封；无确认齿轮传动结构。",
    },
}

# 兼容旧名（测试可能引用）
EQUIPMENT_COMPONENTS: dict[str, set[str]] = {
    et: set(info["body_components"]) | set(info.get("associated_components") or [])
    for et, info in EQUIPMENT_STRUCTURE_REF.items()
}


def _structure_for(equipment_type: str | None) -> dict[str, Any] | None:
    et = (equipment_type or "").strip()
    if not et:
        return None
    return EQUIPMENT_STRUCTURE_REF.get(et)


def evaluate_applicability(
    entry: dict[str, Any],
    *,
    equipment_type: str | None,
    equipment_id: str | None = None,
) -> dict[str, Any]:
    et = (equipment_type or "").strip()
    scope = [str(x) for x in (entry.get("equipment_scope") or [])]
    required = [str(x) for x in (entry.get("required_components") or [])]
    notes: list[str] = []
    structure = _structure_for(et)

    # 显式不适用：仅当设备类型已知且条目声明
    nap = {str(x) for x in (entry.get("not_applicable_equipment_types") or [])}
    if et and et in nap:
        return {
            "applicability_status": "not_applicable",
            "applicability_level": "not_applicable",
            "applicability_notes": [f"条目声明设备类型 {et} 不适用"],
            "structure_source": (structure or {}).get("source"),
            "expand_as_active_candidate": False,
        }

    # 未知设备类型：Unknown ≠ Absent → unresolved
    if et and et not in KNOWN_EQUIPMENT_TYPES:
        return {
            "applicability_status": "unresolved",
            "applicability_level": "unresolved",
            "applicability_notes": [
                f"设备类型 {et} 不在已知类型集合，结构未知，不得判 not_applicable",
                "Unknown ≠ Absent",
            ],
            "structure_source": None,
            "expand_as_active_candidate": False,
        }

    if not et:
        return {
            "applicability_status": "unresolved",
            "applicability_level": "unresolved",
            "applicability_notes": ["缺少设备类型，无法确认适用性"],
            "structure_source": None,
            "expand_as_active_candidate": False,
        }

    body = set((structure or {}).get("body_components") or [])
    associated = set((structure or {}).get("associated_components") or [])
    absent = set((structure or {}).get("explicitly_absent_body_components") or [])

    # 必要部件与显式缺席冲突 → not_applicable
    if any(c in absent for c in required):
        hit = [c for c in required if c in absent]
        return {
            "applicability_status": "not_applicable",
            "applicability_level": "not_applicable",
            "applicability_notes": [
                f"类型知识声明本体明确不含部件 {hit}",
                (structure or {}).get("notes") or "",
            ],
            "structure_source": (structure or {}).get("source"),
            "expand_as_active_candidate": False,
        }

    star_only = (not scope) or scope == ["*"] or set(scope) == {"*"}
    has_star = "*" in scope
    in_explicit_scope = et in scope and "*" not in scope

    # 已知类型但不在条目 scope，且无 * ：若类型已知 → not_applicable；否则已在上方 unresolved
    if et not in scope and not has_star and not star_only:
        return {
            "applicability_status": "not_applicable",
            "applicability_level": "not_applicable",
            "applicability_notes": [f"已知设备类型 {et} 不在 equipment_scope={scope}"],
            "structure_source": (structure or {}).get("source"),
            "expand_as_active_candidate": False,
        }

    if star_only:
        return {
            "applicability_status": "reference_only",
            "applicability_level": "reference_only",
            "applicability_notes": ["equipment_scope=* 仅作通用机理参考，不自动 applicable"],
            "structure_source": (structure or {}).get("source"),
            "expand_as_active_candidate": False,
        }

    # 部件确认：本体齐全 / 仅关联 / 未知
    missing_body = [c for c in required if c not in body and c not in associated]
    only_associated = [c for c in required if c in associated and c not in body]
    body_ok = all(c in body for c in required)

    if missing_body:
        # 类型表未列出 ≠ 证明不存在 → unresolved
        return {
            "applicability_status": "unresolved",
            "applicability_level": "unresolved",
            "applicability_notes": [
                f"必要部件 {missing_body} 在类型结构参考中未确认（非证明缺席）",
                "不得因查不到部件就认定不适用",
            ],
            "structure_source": (structure or {}).get("source"),
            "expand_as_active_candidate": False,
        }

    case_eq = entry.get("source_case_equipment_types") or []
    if case_eq and et not in case_eq and entry.get("force_reference_outside_case_equipment"):
        return {
            "applicability_status": "reference_only",
            "applicability_level": "reference_only",
            "applicability_notes": [
                f"知识来自案例设备 {case_eq}，当前为 {et}，仅参考",
            ],
            "structure_source": (structure or {}).get("source"),
            "expand_as_active_candidate": False,
        }

    if only_associated and not body_ok:
        notes = [
            f"必要部件 {only_associated} 属于关联传动/外部部件，非已确认设备本体",
            "保留为关联未确认候选，不宣称本体结构已验证",
            (structure or {}).get("notes") or "",
        ]
        if entry.get("notes"):
            notes.append(str(entry["notes"]))
        # 允许展开候选，但适用性不是 exact body applicable
        return {
            "applicability_status": "associated_unconfirmed",
            "applicability_level": "associated_component_type_ref",
            "applicability_notes": [n for n in notes if n],
            "structure_source": (structure or {}).get("source"),
            "component_binding": "associated_drive_train",
            "expand_as_active_candidate": True,
        }

    if body_ok and (in_explicit_scope or et in scope):
        notes.append("匹配设备类型；部件来自类型参考白名单，不是资产 BOM")
        notes.append(f"structure_source={(structure or {}).get('source')}")
        if entry.get("notes"):
            notes.append(str(entry["notes"]))
        return {
            "applicability_status": "applicable",
            "applicability_level": "equipment_type",
            "applicability_notes": notes,
            "structure_source": (structure or {}).get("source"),
            "component_binding": "body_type_reference",
            "expand_as_active_candidate": True,
        }

    return {
        "applicability_status": "unresolved",
        "applicability_level": "unresolved",
        "applicability_notes": ["无法确认适用性"],
        "structure_source": (structure or {}).get("source"),
        "expand_as_active_candidate": False,
    }
