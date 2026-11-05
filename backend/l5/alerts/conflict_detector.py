"""AlertRuleConflictDetector：检测同测点/同语义下的限值冲突。"""

from __future__ import annotations

from typing import Any

from l5.alerts.catalog import load_catalog


def _feature_keys(rule: dict[str, Any]) -> set[str]:
    return {str(x) for x in (rule.get("canonical_feature_ids") or []) if x}


def _norm_unit(unit: str | None) -> str:
    u = str(unit or "").strip().lower().replace("°", "")
    if u in {"c", "degc", "℃"}:
        return "c"
    if u in {"mm/s", "mm/s rms", "mm_s"}:
        return "mm/s"
    return u


def detect_conflicts(catalog: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    cat = catalog or load_catalog()
    rules = [r for r in (cat.get("rules") or []) if str(r.get("review_status")) != "deprecated"]
    conflicts: list[dict[str, Any]] = []

    # 显式：电机轴承温度 KB alarm/danger vs YAML-as-alarm
    kb_alarm = next((r for r in rules if r.get("rule_id") == "KB03-MM-BEARING-TEMP-ALARM"), None)
    kb_danger = next((r for r in rules if r.get("rule_id") == "KB03-MM-BEARING-TEMP-DANGER"), None)
    for yaml_id in ("YAML-MM-BEARING-TEMP-DE-AS-ALARM", "YAML-MM-BEARING-TEMP-NDE-AS-ALARM"):
        y = next((r for r in rules if r.get("rule_id") == yaml_id), None)
        if not y:
            continue
        conflicts.append(
            {
                "conflict_id": f"CONF-MM-BEARING-TEMP-{yaml_id}",
                "status": "confirmed",
                "equipment_type": "main_mill_motor",
                "measurement_kind": "temperature",
                "features": sorted(_feature_keys(y)),
                "rule_ids": [x for x in [kb_alarm and kb_alarm["rule_id"], kb_danger and kb_danger["rule_id"], y["rule_id"]] if x],
                "summary": (
                    "KB-03 将 75°C 定义为报警、85°C 定义为危险；"
                    f"legacy YAML {yaml_id} 将 85°C 标为 upper_alarm。相同测点语义下级别定义冲突。"
                ),
                "resolution": "blocked_auto_formal_alert",
                "note": "不得自动选择更大/更小阈值或更新文件作为权威版本",
            }
        )

    # 冷却泵 vs 除鳞泵：不同设备，记录为非等价（非冲突，防误用）
    conflicts.append(
        {
            "conflict_id": "NOTE-CWP-VS-DESCALING-NOT-EQUIVALENT",
            "status": "not_equivalent_equipment",
            "equipment_types": ["cooling_water_circulation_pump", "high_pressure_descaling_pump"],
            "summary": "KB-03 冷却水循环泵限值与 high_pressure_descaling_pump YAML 不属于同一设备类型，禁止互相借用。",
            "resolution": "keep_separate_scopes",
            "rule_ids": [
                "KB03-CWP-VIB-ALARM",
                "KB03-CWP-BEARING-TEMP-ALARM",
                "YAML-DP-VIB-X-ALARM",
                "YAML-DP-BEARING-TEMP-DE-ALARM",
            ],
        }
    )

    # 齿轮箱 vs 电机：不同设备
    conflicts.append(
        {
            "conflict_id": "NOTE-GEARBOX-VS-MOTOR-NOT-EQUIVALENT",
            "status": "not_equivalent_equipment",
            "equipment_types": ["gearbox", "main_mill_motor"],
            "summary": "齿轮箱振动/油温限值不得套用到主轧机电机本体测点。",
            "resolution": "keep_separate_scopes",
            "rule_ids": ["KB03-GB-VIB-ALARM", "KB03-GB-OIL-TEMP-ALARM", "KB03-MM-VIB-XY-ALARM"],
        }
    )

    # 通用扫描：同设备、同 feature、同 alert_level、同 operator、不同 threshold
    by_key: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for rule in rules:
        for feat in _feature_keys(rule):
            key = (
                rule.get("equipment_type"),
                feat,
                rule.get("alert_level"),
                rule.get("comparison_operator"),
                _norm_unit(rule.get("unit")),
                rule.get("measurement_kind"),
            )
            by_key.setdefault(key, []).append(rule)

    for key, group in by_key.items():
        if len(group) < 2:
            continue
        values = {float(r.get("threshold_value")) for r in group if r.get("threshold_value") is not None}
        if len(values) <= 1:
            continue
        # skip if already covered by motor bearing explicit conflict
        ids = [r.get("rule_id") for r in group]
        if any(str(i).startswith("YAML-MM-BEARING-TEMP") for i in ids) and any(
            str(i).startswith("KB03-MM-BEARING-TEMP") for i in ids
        ):
            continue
        # different source types with same level different values
        conflicts.append(
            {
                "conflict_id": f"CONF-SCAN-{key[0]}-{key[1]}-{key[2]}",
                "status": "confirmed" if key[0] else "unresolved",
                "equipment_type": key[0],
                "feature": key[1],
                "alert_level": key[2],
                "rule_ids": ids,
                "threshold_values": sorted(values),
                "summary": f"同设备同测点同级别出现不同限值：{sorted(values)}",
                "resolution": "blocked_auto_formal_alert",
            }
        )

    # YAML 将 danger 数值标为 alarm：级别缺失/错标
    if kb_danger and any(r.get("rule_id", "").startswith("YAML-MM-BEARING-TEMP") for r in rules):
        conflicts.append(
            {
                "conflict_id": "CONF-MM-BEARING-LEVEL-MISLABEL",
                "status": "confirmed",
                "summary": "legacy YAML 缺少独立 danger 级规则，并将 KB-03 危险值 85°C 错标为 alarm。",
                "rule_ids": ["KB03-MM-BEARING-TEMP-DANGER", "YAML-MM-BEARING-TEMP-DE-AS-ALARM"],
                "resolution": "blocked_auto_formal_alert",
            }
        )

    return conflicts


def conflicting_rule_ids(catalog: dict[str, Any] | None = None) -> set[str]:
    blocked: set[str] = set()
    for c in detect_conflicts(catalog):
        if c.get("status") == "confirmed" and c.get("resolution") == "blocked_auto_formal_alert":
            for rid in c.get("rule_ids") or []:
                blocked.add(str(rid))
    # also all review_status=conflict
    cat = catalog or load_catalog()
    for r in cat.get("rules") or []:
        if r.get("review_status") == "conflict":
            blocked.add(str(r.get("rule_id")))
    return blocked
