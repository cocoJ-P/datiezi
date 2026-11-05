"""AlertApplicabilityValidator（含测量语义）。"""

from __future__ import annotations

from typing import Any

from l5.alerts.semantics import (
    measurement_kinds_compatible,
    semantics_for_feature,
    statistics_compatible,
)


def _norm_unit(unit: str | None) -> str | None:
    if unit is None or str(unit).strip() == "":
        return None
    u = str(unit).strip().lower().replace("°", "")
    if u in {"c", "degc", "℃", "celsius"}:
        return "c"
    if "mm/s" in u.replace(" ", ""):
        return "mm/s"
    return u


def units_compatible(rule_unit: str | None, obs_unit: str | None) -> tuple[bool, str]:
    ru = _norm_unit(rule_unit)
    ou = _norm_unit(obs_unit)
    if ru is None or ou is None:
        return False, "unit_missing"
    if ru == ou:
        return True, "ok"
    return False, f"unit_mismatch:{ou}!={ru}"


def assess_rule_applicability(
    rule: dict[str, Any],
    *,
    equipment_type: str | None,
    equipment_id: str | None = None,
    feature_name: str | None = None,
    observed_unit: str | None = None,
    observation_semantics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """返回 applicable / not_applicable / unresolved。"""
    reasons: list[str] = []
    status = "applicable"
    sem_notes: list[str] = []

    rule_et = rule.get("equipment_type")
    if not equipment_type:
        return {
            "status": "unresolved",
            "reasons": ["equipment_type_unknown"],
            "note": "未知设备类型 ≠ 不适用；安全降级为 unresolved",
        }
    if rule_et and str(rule_et) != str(equipment_type):
        return {
            "status": "not_applicable",
            "reasons": [f"equipment_type_mismatch:{equipment_type}!={rule_et}"],
            "note": "禁止跨设备类型套用限值",
        }

    if rule_et == "cooling_water_circulation_pump" and equipment_type == "high_pressure_descaling_pump":
        return {
            "status": "not_applicable",
            "reasons": ["cooling_pump_rule_forbidden_on_descaling_pump"],
            "note": "KB-03 冷却水泵 ≠ 高压除鳞泵",
        }
    if rule_et == "gearbox" and equipment_type == "main_mill_motor":
        return {
            "status": "not_applicable",
            "reasons": ["gearbox_rule_forbidden_on_motor"],
            "note": "齿轮箱限值不得套用到电机本体",
        }

    if rule.get("equipment_id") and equipment_id and str(rule.get("equipment_id")) != str(equipment_id):
        return {
            "status": "not_applicable",
            "reasons": ["equipment_id_mismatch"],
        }

    feats = [str(x) for x in (rule.get("canonical_feature_ids") or [])]
    if feature_name and feats and feature_name not in feats:
        return {
            "status": "not_applicable",
            "reasons": [f"feature_not_in_rule:{feature_name}"],
        }

    ok, why = units_compatible(rule.get("unit"), observed_unit)
    if feature_name and observed_unit is not None:
        if not ok:
            if why == "unit_missing":
                status = "unresolved"
                reasons.append(why)
            else:
                return {"status": "not_applicable", "reasons": [why]}

    # 测量语义
    obs_sem = observation_semantics or semantics_for_feature(feature_name) or {}
    mk_status, mk_reason = measurement_kinds_compatible(
        rule.get("measurement_kind"), obs_sem.get("measurement_kind")
    )
    if mk_status == "mismatch":
        return {"status": "not_applicable", "reasons": [mk_reason], "semantics": obs_sem}
    if mk_status == "unresolved":
        status = "unresolved"
        reasons.append(mk_reason)
        sem_notes.append(mk_reason)

    st_status, st_reason = statistics_compatible(rule.get("statistic"), obs_sem.get("statistic"))
    if st_status == "mismatch":
        return {"status": "not_applicable", "reasons": [st_reason], "semantics": obs_sem}
    if st_status == "unresolved":
        status = "unresolved"
        reasons.append(st_reason)
        sem_notes.append(
            "统计量未确认：不得将 X/Y 振动字段名猜测为 RMS；KB-03 仅给出 mm/s 未声明 RMS"
        )

    if rule.get("review_status") == "deprecated":
        return {"status": "not_applicable", "reasons": ["rule_deprecated"]}

    return {
        "status": status,
        "reasons": reasons or ["ok"],
        "note": "; ".join(sem_notes) if sem_notes else None,
        "semantics": obs_sem or None,
    }
