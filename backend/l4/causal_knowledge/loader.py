"""加载版本化因果知识目录（v1.2）。"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from l4 import CAUSAL_KNOWLEDGE_VERSION, RULES_VERSION

_CATALOG_PATH = Path(__file__).resolve().parent / "catalog_v1_2.json"
_LEGACY_PATHS = (
    Path(__file__).resolve().parent / "catalog_v1_1.json",
    Path(__file__).resolve().parent / "catalog_v1.json",
)


@lru_cache(maxsize=1)
def load_causal_knowledge() -> dict[str, Any]:
    return json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))


def get_catalog() -> dict[str, Any]:
    return load_causal_knowledge()


def list_entries_for_fault(fault_type: str | None, *, equipment_type: str | None = None) -> list[dict[str, Any]]:
    fault = (fault_type or "").strip()
    if not fault:
        return []
    catalog = load_causal_knowledge()
    out: list[dict[str, Any]] = []
    for entry in catalog.get("entries") or []:
        aliases = entry.get("related_l3_fault_aliases") or [entry.get("resulting_fault")]
        if fault not in aliases and fault != entry.get("resulting_fault"):
            continue
        out.append(entry)
    return out


def catalog_version() -> str:
    return str(load_causal_knowledge().get("causal_knowledge_version") or CAUSAL_KNOWLEDGE_VERSION)


def rules_version() -> str:
    return str(load_causal_knowledge().get("rules_version") or RULES_VERSION)


def legacy_catalog_paths() -> tuple[Path, ...]:
    return _LEGACY_PATHS
