"""L4 只读 EvidenceMetadataAdapter：从 L3 EvidenceItem 提取可验证结构化元数据。

不修改 L3 Schema。禁止用宽泛 token 子串推断物理含义。
"""

from __future__ import annotations

import re
from typing import Any

# 精确 feature_name → canonical_feature_id（仅登记稳定观测名）
CANONICAL_FEATURE_MAP: dict[str, str] = {
    "BPFO幅值(g)": "bpfo_amp",
    "BPFI幅值(g)": "bpfi_amp",
    "BSF幅值(g)": "bsf_amp",
    "包络谱峰值(g)": "envelope_peak",
    "1X转频幅值(mm/s)": "amp_1x",
    "2X转频幅值(mm/s)": "amp_2x",
    "2X幅值(mm/s)": "amp_2x",
    "啮合频率幅值(g)": "gear_mesh_amp",
    "驱动端轴承温度(°C)": "bearing_temp_de",
    "非驱动端轴承温度(°C)": "bearing_temp_nde",
    "X向振动(mm/s)": "vib_x",
    "Y向振动(mm/s)": "vib_y",
    "油品含水率(%)": "oil_water_content",
    "含水率(%)": "oil_water_content",
}

# mech 第三段：仅允许登记的 feature_group（精确枚举，不是子串）
MECH_FEATURE_GROUPS = frozenset(
    {
        "bearing",
        "lubrication",
        "gear_mesh",
        "unbalance_1x",
        "misalignment",
        "seal",
        "oil",
    }
)

KNOWN_ANALYZER_IDS = frozenset({"mechanical_feature", "statistical", "threshold"})

# evidence_id 格式
_MECH_RE = re.compile(r"^mech:(?P<record>[^:]+):(?P<group>[^:]+):(?P<feature>.+)$")
_STAT_RE = re.compile(r"^stat:(?P<record>[^:]+):(?P<feature>.+)$")
_THR_RE = re.compile(r"^thr:(?P<record>[^:]+):(?P<rule>[^:]+)$")


def parse_evidence_id(evidence_id: str | None) -> dict[str, Any]:
    eid = str(evidence_id or "").strip()
    if not eid:
        return {"parse_ok": False, "scheme": None, "reason": "missing_evidence_id"}
    m = _MECH_RE.match(eid)
    if m:
        group = m.group("group")
        return {
            "parse_ok": group in MECH_FEATURE_GROUPS,
            "scheme": "mech",
            "record_id": m.group("record"),
            "feature_group": group if group in MECH_FEATURE_GROUPS else None,
            "feature_name_from_id": m.group("feature"),
            "reason": None if group in MECH_FEATURE_GROUPS else f"unknown_mech_feature_group:{group}",
        }
    m = _STAT_RE.match(eid)
    if m:
        return {
            "parse_ok": True,
            "scheme": "stat",
            "record_id": m.group("record"),
            "feature_group": "statistical",
            "feature_name_from_id": m.group("feature"),
            "reason": None,
        }
    m = _THR_RE.match(eid)
    if m:
        return {
            "parse_ok": True,
            "scheme": "thr",
            "record_id": m.group("record"),
            "feature_group": "threshold",
            "rule_id": m.group("rule"),
            "feature_name_from_id": None,
            "reason": None,
        }
    return {"parse_ok": False, "scheme": "unknown", "reason": "unrecognized_evidence_id_format"}


def resolve_canonical_feature(feature_name: str | None, parsed: dict[str, Any]) -> str | None:
    name = str(feature_name or "").strip()
    if name and name in CANONICAL_FEATURE_MAP:
        return CANONICAL_FEATURE_MAP[name]
    # 兼容：ID 内嵌 feature 段与 feature_name 一致时
    from_id = str(parsed.get("feature_name_from_id") or "").strip()
    if from_id and from_id in CANONICAL_FEATURE_MAP:
        return CANONICAL_FEATURE_MAP[from_id]
    return None


def adapt_evidence_item(item: dict[str, Any]) -> dict[str, Any]:
    """提取结构化元数据；无法验证时标记 unresolved，不得用于 cause_specific。"""
    eid = str(item.get("evidence_id") or "")
    analyzer_id = str(item.get("analyzer_id") or "").strip()
    feature_name = str(item.get("feature_name") or "").strip() or None
    parsed = parse_evidence_id(eid)
    canonical = resolve_canonical_feature(feature_name, parsed)

    analyzer_ok = analyzer_id in KNOWN_ANALYZER_IDS
    structured_ok = bool(parsed.get("parse_ok")) and analyzer_ok and canonical is not None

    # 观测是否真实存在：有 observed_value 字段（可为 0）
    has_observation = "observed_value" in item

    meta = {
        "evidence_id": eid or None,
        "analyzer_id": analyzer_id or None,
        "feature_name": feature_name,
        "canonical_feature_id": canonical,
        "feature_group": parsed.get("feature_group"),
        "evidence_id_scheme": parsed.get("scheme"),
        "assessment": str(item.get("assessment") or "").upper(),
        "related_fault_mode": item.get("related_fault_mode"),
        "observed_value": item.get("observed_value"),
        "unit": item.get("unit"),
        "has_observation": has_observation,
        "metadata_status": "resolved" if structured_ok else "unresolved",
        "metadata_notes": [],
        "raw_item_ref": True,
    }
    if not analyzer_id:
        meta["metadata_notes"].append("missing_analyzer_id")
    elif not analyzer_ok:
        meta["metadata_notes"].append(f"unknown_analyzer_id:{analyzer_id}")
    if not parsed.get("parse_ok"):
        meta["metadata_notes"].append(parsed.get("reason") or "evidence_id_parse_failed")
    if canonical is None:
        meta["metadata_notes"].append("canonical_feature_unmapped")
    if not has_observation:
        meta["metadata_notes"].append("missing_observed_value")
    return meta


def adapt_evidence_list(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        meta = adapt_evidence_item(item)
        eid = meta.get("evidence_id") or ""
        if eid and eid in seen:
            continue
        if eid:
            seen.add(eid)
        out.append(meta)
    return out
