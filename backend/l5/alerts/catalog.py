"""AlertRuleCatalog 加载（版本化，不覆盖历史文件）。"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from l5.alerts import CATALOG_VERSION

CATALOG_DIR = Path(__file__).resolve().parent / "catalog"
CATALOG_PATH = CATALOG_DIR / "alert_rules_v1_1.json"
# 历史版本保留：alert_rules_v1.json（不覆盖）


@lru_cache(maxsize=4)
def load_catalog(path: str | None = None) -> dict[str, Any]:
    target = Path(path) if path else CATALOG_PATH
    payload = json.loads(target.read_text(encoding="utf-8"))
    rules = []
    for rule in payload.get("rules") or []:
        if not isinstance(rule, dict):
            continue
        r = dict(rule)
        r.setdefault("catalog_version", payload.get("catalog_version") or CATALOG_VERSION)
        rules.append(r)
    return {
        "schema": payload.get("schema") or "AlertRuleCatalog",
        "catalog_version": payload.get("catalog_version") or CATALOG_VERSION,
        "notes": list(payload.get("notes") or []),
        "rules": rules,
        "source_path": str(target),
    }


def list_rules(
    *,
    equipment_type: str | None = None,
    review_status: str | None = None,
    catalog: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    cat = catalog or load_catalog()
    out = []
    for rule in cat.get("rules") or []:
        if equipment_type and str(rule.get("equipment_type")) != str(equipment_type):
            continue
        if review_status and str(rule.get("review_status")) != str(review_status):
            continue
        out.append(rule)
    return out


def get_rule(rule_id: str, *, catalog: dict[str, Any] | None = None) -> dict[str, Any] | None:
    for rule in list_rules(catalog=catalog):
        if rule.get("rule_id") == rule_id:
            return rule
    return None


def clear_catalog_cache() -> None:
    load_catalog.cache_clear()
