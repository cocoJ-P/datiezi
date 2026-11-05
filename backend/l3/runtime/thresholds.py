"""设备限值规则加载（按 equipment_type 隔离）。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

RULES_DIR = Path(__file__).resolve().parents[1] / "config" / "thresholds"


@lru_cache(maxsize=32)
def load_threshold_rules(equipment_type: str) -> tuple[dict[str, Any], ...]:
    path = RULES_DIR / f"{equipment_type}.yaml"
    if not path.is_file():
        return tuple()
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if str(payload.get("equipment_type") or "") != equipment_type:
        return tuple()
    rules = payload.get("rules") or []
    out = []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        out.append(dict(rule))
    return tuple(out)


def clear_threshold_cache() -> None:
    load_threshold_rules.cache_clear()
