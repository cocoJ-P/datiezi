"""特征与设备类型校验。"""

from __future__ import annotations

from typing import Any

from l2.equipment.registry import load_all_specs
from l2.training.schema import LEAKAGE_COLUMNS


def _all_leakage_columns() -> frozenset[str]:
    blocked = set(LEAKAGE_COLUMNS)
    try:
        for spec in load_all_specs().values():
            blocked.update(spec.leakage_columns)
            blocked.add(spec.record_id_field)
            blocked.add(spec.target_field)
            blocked.add(spec.status_field)
    except Exception:  # noqa: BLE001 - 校验兜底使用默认泄漏集合
        pass
    return frozenset(blocked)


class InferenceValidationError(Exception):
    def __init__(self, error_code: str, message: str, details: dict[str, Any] | None = None, status_code: int = 422):
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.details = details or {}
        self.status_code = status_code


def validate_equipment_type(requested: str, expected: str) -> None:
    if requested != expected:
        raise InferenceValidationError(
            "UNSUPPORTED_EQUIPMENT_TYPE",
            f"请求设备类型 {requested} 与已加载模型 {expected} 不一致。",
            {"equipment_type": requested, "loaded": expected},
            status_code=400,
        )


def validate_features(features: dict[str, Any], feature_columns: list[str], *, record_index: int | None = None) -> dict[str, float]:
    prefix = f"records[{record_index}]." if record_index is not None else ""
    if not isinstance(features, dict):
        raise InferenceValidationError("INVALID_FEATURES", f"{prefix}features 必须是对象。")

    leakage = [name for name in features if name in _all_leakage_columns()]
    if leakage:
        raise InferenceValidationError(
            "LEAKAGE_FIELDS_FORBIDDEN",
            f"{prefix}不允许提交标签或诊断泄漏字段。",
            {"fields": leakage},
        )

    unknown = [name for name in features if name not in feature_columns]
    if unknown:
        raise InferenceValidationError(
            "UNKNOWN_FEATURES",
            f"{prefix}存在未知特征字段。",
            {"fields": unknown},
        )

    missing = [name for name in feature_columns if name not in features]
    if missing:
        raise InferenceValidationError(
            "MISSING_FEATURES",
            f"{prefix}缺少必要特征。",
            {"fields": missing},
        )

    def details(**extra: object) -> dict[str, object]:
        payload: dict[str, object] = dict(extra)
        if record_index is not None:
            payload["index"] = record_index
        return payload

    cleaned: dict[str, float] = {}
    for name in feature_columns:
        value = features[name]
        if value is None:
            raise InferenceValidationError("NULL_FEATURE", f"{prefix}特征 {name} 不能为 null。", details(field=name))
        if isinstance(value, bool):
            raise InferenceValidationError("INVALID_FEATURE_TYPE", f"{prefix}特征 {name} 不能是布尔值。", details(field=name))
        if isinstance(value, str):
            raise InferenceValidationError("INVALID_FEATURE_TYPE", f"{prefix}特征 {name} 必须是数值。", details(field=name))
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise InferenceValidationError(
                "INVALID_FEATURE_TYPE",
                f"{prefix}特征 {name} 无法转换为数值。",
                details(field=name, value=value),
            ) from exc
        if number != number or number in (float("inf"), float("-inf")):
            raise InferenceValidationError(
                "NON_FINITE_FEATURE",
                f"{prefix}特征 {name} 必须是有限数值。",
                details(field=name, value=value),
            )
        cleaned[name] = number
    return cleaned
