"""时间留出划分与泄漏检查。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd

from l5.prediction.contract import FEATURE_WHITELIST, TARGET_COLUMNS


def _obs_fingerprint(row: pd.Series) -> str:
    cols = FEATURE_WHITELIST + ["设备编号", "设备类别", "采集时间"]
    raw = "||".join("" if pd.isna(row[c]) else str(row[c]) for c in cols)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def time_split_equipment(
    df: pd.DataFrame,
    *,
    equipment_type: str,
    equipment_category: str,
    train_ratio: float = 0.8,
    dataset_fingerprint: str,
    split_version: str = "time80_groupby_device_time_v1",
) -> dict[str, Any]:
    if "采集时间" not in df.columns or "设备编号" not in df.columns or "记录编号" not in df.columns:
        raise ValueError("split_requires_id_and_time_columns")
    work = df.copy()
    work["采集时间"] = pd.to_datetime(work["采集时间"])
    work["__group"] = work["设备编号"].astype(str) + "|" + work["采集时间"].astype(str)
    # 按组的最早时间排序，整组不跨界
    group_time = work.groupby("__group", sort=False)["采集时间"].min().sort_values()
    groups = list(group_time.index)
    cut = int(len(groups) * train_ratio)
    if cut <= 0 or cut >= len(groups):
        raise ValueError(f"invalid_split_cut:{cut}/{len(groups)}")
    train_groups = set(groups[:cut])
    valid_groups = set(groups[cut:])
    train_df = work[work["__group"].isin(train_groups)].copy()
    valid_df = work[work["__group"].isin(valid_groups)].copy()

    train_ids = train_df["记录编号"].astype(str).tolist()
    valid_ids = valid_df["记录编号"].astype(str).tolist()
    overlap = set(train_ids) & set(valid_ids)
    if overlap:
        raise RuntimeError(f"record_id_leak_across_split:{len(overlap)}")

    # 观测指纹跨分区
    train_fp = set(train_df.apply(_obs_fingerprint, axis=1))
    valid_fp = set(valid_df.apply(_obs_fingerprint, axis=1))
    fp_overlap = train_fp & valid_fp

    cutoff_time = str(group_time.iloc[cut - 1])
    valid_min = str(valid_df["采集时间"].min())
    train_max = str(train_df["采集时间"].max())
    # 因同刻多记录，valid_min 可能等于部分 train 时间；报告边界
    boundary_note = None
    if pd.Timestamp(valid_min) <= pd.Timestamp(train_max):
        boundary_note = (
            "valid_min_time_not_strictly_after_all_train_rows_due_to_same_timestamp_multi_records;"
            "groups_are_isolated_but_wall_clock_windows_may_overlap"
        )

    split_id = hashlib.sha256(
        f"{split_version}|{equipment_type}|{dataset_fingerprint}|{cutoff_time}|{len(train_ids)}|{len(valid_ids)}".encode()
    ).hexdigest()[:16]

    manifest = {
        "split_id": f"SPL-{split_id}",
        "split_version": split_version,
        "dataset_fingerprint": dataset_fingerprint,
        "equipment_type": equipment_type,
        "equipment_category": equipment_category,
        "train_ratio": train_ratio,
        "cutoff_time": cutoff_time,
        "train_max_time": train_max,
        "valid_min_time": valid_min,
        "boundary_note": boundary_note,
        "row_count": {"train": len(train_ids), "valid": len(valid_ids), "total": len(work)},
        "group_count": {"train": len(train_groups), "valid": len(valid_groups), "total": len(groups)},
        "train_record_ids": train_ids,
        "valid_record_ids": valid_ids,
        "obs_fingerprint_overlap_count": len(fp_overlap),
        "leakage_checks": {
            "record_id_overlap": 0,
            "obs_fingerprint_overlap": len(fp_overlap),
            "passed_record_id": True,
            "passed_group_isolation": True,
            "passed_obs_fingerprint": len(fp_overlap) == 0,
        },
    }
    if fp_overlap:
        # 同观测跨折视为泄漏；硬失败
        raise RuntimeError(f"obs_fingerprint_leak_across_split:{equipment_category}:{len(fp_overlap)}")

    return {
        "manifest": manifest,
        "train_df": train_df.drop(columns=["__group"]),
        "valid_df": valid_df.drop(columns=["__group"]),
    }


def assert_no_target_in_features(feature_columns: list[str]) -> None:
    bad = [c for c in feature_columns if c in TARGET_COLUMNS or c == "数据集划分"]
    if bad:
        raise RuntimeError(f"target_leak_in_features:{bad}")
    if "记录编号" in feature_columns or "设备编号" in feature_columns or "采集时间" in feature_columns:
        raise RuntimeError(f"identity_leak_in_features:{feature_columns}")


def save_split_manifests(manifests: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # 完整 ID 列表可能很大；仍按需求保存
    path.write_text(json.dumps({"splits": manifests}, ensure_ascii=False, indent=2), encoding="utf-8")
