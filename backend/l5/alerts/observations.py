"""从 L3 EvidenceBundle / analyzer_results 提取实测值，并附着 Schema 语义。"""

from __future__ import annotations

from typing import Any

from l5.alerts.semantics import semantics_for_feature


def extract_observations(evidence_bundle: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """feature_name -> {value, unit, evidence_ids, sources, semantics}"""
    bundle = evidence_bundle or {}
    out: dict[str, dict[str, Any]] = {}

    def _put(name: str, value: Any, unit: str | None, evidence_id: str | None, source: str) -> None:
        if not name or value is None:
            return
        try:
            num = float(value)
        except (TypeError, ValueError):
            return
        sem = semantics_for_feature(name)
        slot = out.setdefault(
            name,
            {
                "feature_name": name,
                "value": num,
                "unit": unit or (sem or {}).get("unit"),
                "evidence_ids": [],
                "sources": [],
                "semantics": sem,
            },
        )
        if evidence_id and evidence_id not in slot["evidence_ids"]:
            slot["evidence_ids"].append(str(evidence_id))
        if source not in slot["sources"]:
            slot["sources"].append(source)
        if slot.get("unit") in (None, "") and unit:
            slot["unit"] = unit
        if not slot.get("semantics") and sem:
            slot["semantics"] = sem

    for ar in bundle.get("analyzer_results") or []:
        if not isinstance(ar, dict):
            continue
        aid = ar.get("analyzer_id")
        for obs in ar.get("observations") or []:
            if not isinstance(obs, dict):
                continue
            param = obs.get("parameter") or obs.get("feature_name")
            _put(
                str(param),
                obs.get("observed_value"),
                obs.get("unit"),
                f"thr-obs:{obs.get('rule_id')}" if obs.get("rule_id") else None,
                f"analyzer:{aid}",
            )

    for item in bundle.get("evidence_items") or []:
        if not isinstance(item, dict):
            continue
        _put(
            str(item.get("feature_name") or ""),
            item.get("observed_value"),
            item.get("unit"),
            item.get("evidence_id"),
            "evidence_item",
        )

    return out
