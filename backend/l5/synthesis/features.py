"""从 L5 上下文 / EvidenceBundle / 数据集观测表提取 19 维特征。

禁止通过 record_id 回查训练答案（目标列永不进入本模块缓存）。
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from l5.prediction.contract import FEATURE_WHITELIST, TARGET_COLUMNS
from l5.synthesis.mapping import normalize_equipment_category


def _coerce_feature_row(row: dict[str, Any]) -> dict[str, Any] | None:
    out: dict[str, Any] = {}
    for name in FEATURE_WHITELIST:
        if name not in row or row[name] is None:
            return None
        out[name] = row[name]
    return out


@lru_cache(maxsize=1)
def _observation_index() -> dict[str, dict[str, Any]]:
    """记录编号 → 仅观测特征 + 设备类别（不含任何目标列）。"""
    from l5.prediction.dataset import load_test_tables, load_train_tables

    index: dict[str, dict[str, Any]] = {}
    for loader in (load_train_tables, load_test_tables):
        try:
            loaded = loader()
        except Exception:  # noqa: BLE001
            continue
        for _stem, df in (loaded.get("tables") or {}).items():
            # 显式丢弃目标列，防止泄漏
            drop = [c for c in TARGET_COLUMNS if c in df.columns]
            view = df.drop(columns=drop, errors="ignore")
            for _, row in view.iterrows():
                rid = str(row.get("记录编号") or "")
                if not rid or rid in index:
                    continue
                feats = {c: row[c] for c in FEATURE_WHITELIST if c in view.columns}
                if len(feats) != len(FEATURE_WHITELIST):
                    continue
                cat = str(row.get("设备类别") or "")
                index[rid] = {
                    "features": feats,
                    "设备类别": cat,
                    "equipment_category": normalize_equipment_category(cat) or cat,
                    "source": "dataset_observation_index_no_targets",
                }
    return index


def lookup_observation_features(record_id: str) -> dict[str, Any] | None:
    return _observation_index().get(str(record_id))


def extract_feature_map(context: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    """返回 (features, gaps)。features 齐全才非 None。"""
    found: dict[str, Any] = {}
    gaps: list[str] = []

    bundle = context.get("evidence_bundle") or {}
    for item in bundle.get("evidence_items") or []:
        if not isinstance(item, dict):
            continue
        name = item.get("feature_name") or item.get("parameter")
        if name in FEATURE_WHITELIST and name not in found:
            val = item.get("observed_value")
            if val is not None:
                found[str(name)] = val
    for ar in bundle.get("analyzer_results") or []:
        if not isinstance(ar, dict):
            continue
        for obs in ar.get("observations") or []:
            if not isinstance(obs, dict):
                continue
            name = obs.get("parameter") or obs.get("feature_name")
            if name in FEATURE_WHITELIST and name not in found and obs.get("observed_value") is not None:
                found[str(name)] = obs.get("observed_value")

    flat = context.get("observation_features") or context.get("features") or {}
    if isinstance(flat, dict):
        for k, v in flat.items():
            if k in FEATURE_WHITELIST and k not in found and v is not None:
                found[str(k)] = v

    # 补齐：从竞赛观测表按 record_id 取缺失列（永不取目标）
    if any(name not in found for name in FEATURE_WHITELIST):
        hit = lookup_observation_features(str(context.get("record_id") or ""))
        if hit:
            for k, v in (hit.get("features") or {}).items():
                if k in FEATURE_WHITELIST and k not in found and v is not None:
                    found[str(k)] = v
            # 透传类别供预测服务
            if hit.get("equipment_category") and not context.get("_resolved_equipment_category"):
                context["_resolved_equipment_category"] = hit.get("equipment_category")
                context.setdefault("equipment_category", hit.get("equipment_category"))

    for name in FEATURE_WHITELIST:
        if name not in found:
            gaps.append(name)
    if gaps:
        return None, gaps
    return found, []
