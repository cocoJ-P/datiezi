"""测量语义注册：仅依据 Schema/已知元数据，不猜测 RMS。"""

from __future__ import annotations

from typing import Any

# 基于 L1 Schema definitions（dimension=velocity；X/Y/max 未声明 RMS；仅 vibration_rms 字段声明均方根）
FEATURE_SEMANTICS: dict[str, dict[str, Any]] = {
    "X向振动(mm/s)": {
        "canonical_feature_id": "X向振动(mm/s)",
        "schema_field": "vibration_x",
        "dimension": "velocity",
        "measurement_kind": "vibration_velocity",
        "statistic": "unresolved",
        "unit": "mm/s",
        "note": "L1 定义为 X向振动速度；未声明 RMS/峰值等统计量",
    },
    "Y向振动(mm/s)": {
        "canonical_feature_id": "Y向振动(mm/s)",
        "schema_field": "vibration_y",
        "dimension": "velocity",
        "measurement_kind": "vibration_velocity",
        "statistic": "unresolved",
        "unit": "mm/s",
        "note": "L1 定义为 Y向振动速度；未声明 RMS/峰值等统计量",
    },
    "最大振动(mm/s)": {
        "canonical_feature_id": "最大振动(mm/s)",
        "schema_field": "vibration_max",
        "dimension": "velocity",
        "measurement_kind": "vibration_velocity_max",
        "statistic": "max",
        "unit": "mm/s",
        "note": "与 X/Y 分量及 RMS 不是同一统计定义，不得等价",
    },
    "振动均方根": {
        "canonical_feature_id": "振动均方根",
        "schema_field": "vibration_rms",
        "dimension": "velocity",
        "measurement_kind": "vibration_velocity",
        "statistic": "rms",
        "unit": "mm/s",
        "note": "L1 单独字段；原列无单位时需复核",
    },
    "振动RMS": {
        "canonical_feature_id": "振动均方根",
        "schema_field": "vibration_rms",
        "dimension": "velocity",
        "measurement_kind": "vibration_velocity",
        "statistic": "rms",
        "unit": "mm/s",
        "note": "vibration_rms 别名",
    },
    "驱动端轴承温度(°C)": {
        "canonical_feature_id": "驱动端轴承温度(°C)",
        "schema_field": "bearing_temperature_drive_end",
        "dimension": "temperature",
        "measurement_kind": "temperature",
        "statistic": "point",
        "unit": "°C",
    },
    "非驱动端轴承温度(°C)": {
        "canonical_feature_id": "非驱动端轴承温度(°C)",
        "schema_field": "bearing_temperature_non_drive_end",
        "dimension": "temperature",
        "measurement_kind": "temperature",
        "statistic": "point",
        "unit": "°C",
    },
    "油温(°C)": {
        "canonical_feature_id": "油温(°C)",
        "measurement_kind": "temperature",
        "statistic": "point",
        "unit": "°C",
        "dimension": "temperature",
    },
    "齿轮箱油温(°C)": {
        "canonical_feature_id": "齿轮箱油温(°C)",
        "measurement_kind": "temperature",
        "statistic": "point",
        "unit": "°C",
        "dimension": "temperature",
    },
}


def semantics_for_feature(feature_name: str | None) -> dict[str, Any] | None:
    if not feature_name:
        return None
    return FEATURE_SEMANTICS.get(str(feature_name))


def measurement_kinds_compatible(rule_kind: str | None, obs_kind: str | None) -> tuple[str, str]:
    """返回 (status, reason) status in ok|unresolved|mismatch."""
    rk = str(rule_kind or "").strip()
    okind = str(obs_kind or "").strip()
    if not rk or not okind:
        return "unresolved", "measurement_kind_missing"
    if rk == okind:
        return "ok", "ok"
    # 明确不等价
    if {rk, okind} == {"vibration_velocity", "vibration_velocity_max"}:
        return "mismatch", "velocity_component_vs_max_not_equivalent"
    if "rms" in rk and "rms" not in okind:
        return "mismatch", "rule_requires_rms_observation_not_rms"
    return "mismatch", f"measurement_kind_mismatch:{okind}!={rk}"


def statistics_compatible(rule_stat: str | None, obs_stat: str | None) -> tuple[str, str]:
    rs = str(rule_stat or "unresolved").strip().lower()
    os_ = str(obs_stat or "unresolved").strip().lower()
    if rs in {"", "unresolved", "none", "null"} or os_ in {"", "unresolved", "none", "null"}:
        return "unresolved", "statistic_unresolved"
    if rs == os_:
        return "ok", "ok"
    if {rs, os_} & {"rms", "max", "peak", "p-p", "pk"} and rs != os_:
        return "mismatch", f"statistic_mismatch:{os_}!={rs}"
    return "mismatch", f"statistic_mismatch:{os_}!={rs}"
