"""典型频谱模式参考说明（模板匹配，不参与模型预测）。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from l2.equipment.registry import get_equipment_spec

PATTERNS_DIR = Path(__file__).resolve().parents[1] / "equipment" / "config" / "spectral_patterns"


@lru_cache(maxsize=32)
def _load_template(equipment_type: str) -> dict[str, Any] | None:
    path = PATTERNS_DIR / f"{equipment_type}.yaml"
    if not path.is_file():
        return None
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        return None
    if str(payload.get("equipment_type") or "") != equipment_type:
        return None
    return payload


def clear_spectral_template_cache() -> None:
    _load_template.cache_clear()


def resolve_spectral_reference(
    *,
    equipment_type: str,
    screening_result: str | None,
    preliminary_fault: str | None,
) -> dict[str, Any] | None:
    """仅在 ABNORMAL 且故障类别明确、存在模板时返回参考说明。"""
    if str(screening_result or "").upper() != "ABNORMAL":
        return None
    fault = str(preliminary_fault or "").strip()
    if not fault or fault in {"正常", "None", "null"}:
        return None

    template = _load_template(equipment_type)
    if not template:
        return None
    patterns = template.get("patterns") or {}
    if not isinstance(patterns, dict) or fault not in patterns:
        return None

    body = patterns[fault] or {}
    description = str(body.get("description") or "").strip()
    if not description:
        return None

    related = [str(x) for x in (body.get("related_features") or []) if str(x).strip()]
    try:
        spec = get_equipment_spec(equipment_type)
        allowed = set(spec.feature_names)
        related = [name for name in related if name in allowed]
    except Exception:  # noqa: BLE001 - 模板仍可返回描述，特征列表降级
        pass

    return {
        "fault_class": fault,
        "description": description,
        "related_features": related,
        "source": "equipment_template",
        "template_version": str(template.get("template_version") or "unknown"),
        "verification_status": "reference_only",
    }


def attach_spectral_reference(result: dict[str, Any]) -> dict[str, Any]:
    """向诊断结果附加 spectral_reference；不改动既有分类字段。"""
    decision = result.get("decision") or {}
    ref = resolve_spectral_reference(
        equipment_type=str(result.get("equipment_type") or ""),
        screening_result=decision.get("screening_result"),
        preliminary_fault=decision.get("preliminary_fault"),
    )
    out = dict(result)
    out["spectral_reference"] = ref
    return out
