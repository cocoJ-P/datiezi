"""从 L5 上下文提取 19 维观测特征（唯一物理输入）。

规则（L5-P4）：
- record_id / 文件名 / 行号不得作为模型特征。
- 观测索引只暴露 FEATURE_WHITELIST + 设备/时间/版本元数据，永不含十字段答案。
- 当前输入已有的特征不得被历史值静默覆盖；共同特征数值冲突则拒绝合并。
- 缺特征且无合法数据源时明确失败，不得偷偷借用无关训练记录。
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from l5.prediction.contract import FEATURE_WHITELIST, TARGET_COLUMNS
from l5.synthesis.mapping import normalize_equipment_category

# 相对误差 / 绝对误差：用于判断「同一物理量」是否冲突
_REL_TOL = 1e-4
_ABS_TOL = 1e-6


def _values_conflict(a: Any, b: Any) -> bool:
    try:
        fa, fb = float(a), float(b)
        return abs(fa - fb) > max(_ABS_TOL, _REL_TOL * max(abs(fa), abs(fb), 1.0))
    except (TypeError, ValueError):
        return str(a).strip() != str(b).strip()


@lru_cache(maxsize=1)
def _observation_index() -> dict[str, dict[str, Any]]:
    """记录编号 → 观测特征 + 溯源元数据（不含任何目标列）。"""
    from l5.prediction.dataset import dataset_fingerprint, load_test_tables, load_train_tables
    from l5.prediction.paths import TEST_DIR, TRAIN_DIR
    from l5.prediction.contract import EQUIPMENT_FILE_STEMS

    index: dict[str, dict[str, Any]] = {}
    # 指纹绑定版本
    train_paths = [TRAIN_DIR / f"{s}.xlsx" for s in EQUIPMENT_FILE_STEMS]
    test_paths = [TEST_DIR / f"{s}.xlsx" for s in EQUIPMENT_FILE_STEMS]
    try:
        train_fp = dataset_fingerprint([p for p in train_paths if p.is_file()])
    except Exception:  # noqa: BLE001
        train_fp = None
    try:
        test_fp = dataset_fingerprint([p for p in test_paths if p.is_file()])
    except Exception:  # noqa: BLE001
        test_fp = None

    loaders = (
        ("train_competition_xlsx", load_train_tables, train_fp, TRAIN_DIR),
        ("test_competition_xlsx", load_test_tables, test_fp, TEST_DIR),
    )
    for channel, loader, fp, root in loaders:
        try:
            loaded = loader()
        except Exception:  # noqa: BLE001
            continue
        for stem, df in (loaded.get("tables") or {}).items():
            drop = [c for c in TARGET_COLUMNS if c in df.columns]
            view = df.drop(columns=drop, errors="ignore")
            source_file = str((root / f"{stem}.xlsx").resolve())
            for _, row in view.iterrows():
                rid = str(row.get("记录编号") or "")
                if not rid or rid in index:
                    continue
                feats = {c: row[c] for c in FEATURE_WHITELIST if c in view.columns}
                if len(feats) != len(FEATURE_WHITELIST):
                    continue
                # 二次断言：答案列不得进入
                for t in TARGET_COLUMNS:
                    feats.pop(t, None)
                cat = str(row.get("设备类别") or "")
                index[rid] = {
                    "features": feats,
                    "设备类别": cat,
                    "equipment_category": normalize_equipment_category(cat) or cat,
                    "equipment_id": str(row.get("设备编号") or "") or None,
                    "collection_time": str(row.get("采集时间") or "") or None,
                    "provenance": {
                        "channel": channel,
                        "source_file": source_file,
                        "sheet_or_stem": stem,
                        "dataset_fingerprint": fp,
                        "exposes_targets": False,
                    },
                }
    return index


def lookup_observation_features(record_id: str) -> dict[str, Any] | None:
    hit = _observation_index().get(str(record_id))
    if not hit:
        return None
    # 防御性拷贝，且再次剥离答案键
    feats = {k: v for k, v in (hit.get("features") or {}).items() if k in FEATURE_WHITELIST}
    return {
        "features": feats,
        "设备类别": hit.get("设备类别"),
        "equipment_category": hit.get("equipment_category"),
        "equipment_id": hit.get("equipment_id"),
        "collection_time": hit.get("collection_time"),
        "provenance": dict(hit.get("provenance") or {}),
    }


def _collect_live_features(context: dict[str, Any]) -> dict[str, Any]:
    found: dict[str, Any] = {}
    bundle = context.get("evidence_bundle") or {}
    for item in bundle.get("evidence_items") or []:
        if not isinstance(item, dict):
            continue
        name = item.get("feature_name") or item.get("parameter")
        if name in FEATURE_WHITELIST and name not in found and item.get("observed_value") is not None:
            found[str(name)] = item.get("observed_value")
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
            if k in TARGET_COLUMNS or k in {"记录编号", "数据集划分"}:
                continue
            if k in FEATURE_WHITELIST and k not in found and v is not None:
                found[str(k)] = v
    return found


def _equipment_matches(context: dict[str, Any], hit: dict[str, Any]) -> bool:
    ctx_cat = normalize_equipment_category(
        context.get("_resolved_equipment_category")
        or context.get("equipment_category")
        or (context.get("evidence_bundle") or {}).get("equipment_category"),
        context.get("equipment_type"),
    )
    hit_cat = hit.get("equipment_category") or normalize_equipment_category(hit.get("设备类别"))
    if ctx_cat and hit_cat and ctx_cat != hit_cat:
        return False
    # 若上下文给出设备编号，必须一致
    ctx_eid = context.get("equipment_id")
    hit_eid = hit.get("equipment_id")
    if ctx_eid and hit_eid and str(ctx_eid) != str(hit_eid):
        return False
    return True


def extract_feature_map(
    context: dict[str, Any],
    *,
    allow_dataset_observation_backfill: bool = True,
) -> tuple[dict[str, Any] | None, list[str], dict[str, Any]]:
    """
    返回 (features|None, gaps_or_errors, provenance_meta)。

    allow_dataset_observation_backfill=False：仅用当前上下文，缺则失败（用于新数据 / 扰动测试）。
    """
    meta: dict[str, Any] = {
        "live_feature_count": 0,
        "backfill_used": False,
        "backfill_fields": [],
        "conflicts": [],
        "provenance": None,
        "policy": "live_first_no_silent_overwrite",
    }
    live = _collect_live_features(context)
    meta["live_feature_count"] = len(live)
    found = dict(live)

    gaps = [n for n in FEATURE_WHITELIST if n not in found]
    if not gaps:
        meta["provenance"] = {"channel": "live_context_only", "exposes_targets": False}
        return found, [], meta

    if not allow_dataset_observation_backfill:
        meta["refusal"] = "dataset_backfill_disabled"
        return None, gaps, meta

    hit = lookup_observation_features(str(context.get("record_id") or ""))
    if not hit:
        meta["refusal"] = "no_dataset_observation_for_record"
        return None, gaps, meta
    if not _equipment_matches(context, hit):
        meta["refusal"] = "dataset_observation_equipment_mismatch"
        return None, gaps, meta

    # 共同特征冲突 → 拒绝静默覆盖 / 拒绝合并
    hist = hit.get("features") or {}
    conflicts = []
    for k, v in live.items():
        if k in hist and _values_conflict(v, hist[k]):
            conflicts.append({"feature": k, "live": v, "dataset": hist[k]})
    if conflicts:
        meta["conflicts"] = conflicts
        meta["refusal"] = "live_vs_dataset_feature_conflict"
        return None, [c["feature"] for c in conflicts], meta

    # 仅填充缺失列；已有 live 值绝不改写
    filled = []
    for k in gaps:
        if k in hist and hist[k] is not None:
            found[k] = hist[k]
            filled.append(k)
    meta["backfill_used"] = bool(filled)
    meta["backfill_fields"] = filled
    meta["provenance"] = {
        **(hit.get("provenance") or {}),
        "equipment_category": hit.get("equipment_category"),
        "equipment_id": hit.get("equipment_id"),
        "collection_time": hit.get("collection_time"),
        "record_id_bound": str(context.get("record_id")),
        "filled_from_dataset_observation": filled,
        "live_retained": sorted(live.keys()),
    }
    if hit.get("equipment_category"):
        context["_resolved_equipment_category"] = hit.get("equipment_category")
        context.setdefault("equipment_category", hit.get("equipment_category"))

    still = [n for n in FEATURE_WHITELIST if n not in found]
    if still:
        meta["refusal"] = "still_missing_after_backfill"
        return None, still, meta
    return found, [], meta
