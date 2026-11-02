"""受控单位换算。只在物理维度兼容且来源、目标单位都已确认时换算。"""

from __future__ import annotations

from decimal import Decimal

STANDARD_GRAVITY = Decimal("9.80665")

_CANONICAL = {
    "rpm": "rpm",
    "r/min": "rpm",
    "hz": "Hz",
    "khz": "kHz",
    "m/s": "m/s",
    "mm/s": "mm/s",
    "um/s": "um/s",
    "μm/s": "um/s",
    "m": "m",
    "mm": "mm",
    "um": "um",
    "μm": "um",
    "a": "A",
    "ma": "mA",
    "°c": "°C",
    "℃": "°C",
    "c": "°C",
    "k": "K",
    "g": "g",
    "m/s2": "m/s²",
    "m/s²": "m/s²",
}

# 换到该维度的基准单位：value_base = value * scale + offset
_TO_BASE: dict[str, dict[str, tuple[Decimal, Decimal]]] = {
    "frequency": {
        "Hz": (Decimal(1), Decimal(0)),
        "kHz": (Decimal(1000), Decimal(0)),
        "rpm": (Decimal(1) / Decimal(60), Decimal(0)),
    },
    "rotational_speed": {
        "rpm": (Decimal(1), Decimal(0)),
    },
    "velocity": {
        "m/s": (Decimal(1), Decimal(0)),
        "mm/s": (Decimal("0.001"), Decimal(0)),
        "um/s": (Decimal("0.000001"), Decimal(0)),
    },
    "length": {
        "m": (Decimal(1), Decimal(0)),
        "mm": (Decimal("0.001"), Decimal(0)),
        "um": (Decimal("0.000001"), Decimal(0)),
    },
    "current": {
        "A": (Decimal(1), Decimal(0)),
        "mA": (Decimal("0.001"), Decimal(0)),
    },
    "temperature": {
        "K": (Decimal(1), Decimal(0)),
        "°C": (Decimal(1), Decimal("273.15")),
    },
    "acceleration": {
        "m/s²": (Decimal(1), Decimal(0)),
        "g": (STANDARD_GRAVITY, Decimal(0)),
    },
}


def canonical_unit(unit: str) -> str:
    key = "".join(unit.strip().split()).replace("℃", "°C").casefold()
    if key in {"°c"}:
        return "°C"
    return _CANONICAL.get(key, "")


def convert_unit(value: Decimal, source: str, target: str, dimension: str) -> tuple[Decimal | None, str, str]:
    src = canonical_unit(source)
    dst = canonical_unit(target)
    if not source.strip() and not target.strip():
        return value, "success", "UNIT_NONE"
    if not source.strip():
        return None, "unit_unconfirmed", "UNIT_UNCONFIRMED"
    if not src or not dst:
        return None, "unit_incompatible", "UNIT_INCOMPATIBLE"
    if src == dst:
        return value, "success", "UNIT_IDENTITY"
    table = _TO_BASE.get(dimension, {})
    if src not in table or dst not in table:
        return None, "unit_incompatible", "UNIT_INCOMPATIBLE"
    src_scale, src_offset = table[src]
    dst_scale, dst_offset = table[dst]
    if dst_scale == 0:
        return None, "unit_incompatible", "UNIT_INCOMPATIBLE"
    base = value * src_scale + src_offset
    converted = (base - dst_offset) / dst_scale
    return converted, "success", "UNIT_CONVERT"
