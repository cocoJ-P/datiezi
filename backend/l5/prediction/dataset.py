"""训练/测试数据加载（显式白名单）。"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pandas as pd

from l5.prediction.contract import (
    EQUIPMENT_FILE_STEMS,
    FEATURE_WHITELIST,
    ID_COLUMNS,
    SHEET_TO_EQUIPMENT_TYPE,
    TARGET_COLUMNS,
    verify_columns,
)
from l5.prediction.paths import TEST_DIR, TRAIN_DIR


def _file_md5(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def dataset_fingerprint(paths: list[Path]) -> str:
    parts = []
    for p in sorted(paths, key=lambda x: x.name):
        parts.append(f"{p.name}:{_file_md5(p)}")
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def load_train_tables(train_dir: Path | None = None) -> dict[str, Any]:
    root = train_dir or TRAIN_DIR
    tables: dict[str, pd.DataFrame] = {}
    meta: dict[str, Any] = {"files": [], "column_audit": {}}
    paths = []
    for stem in EQUIPMENT_FILE_STEMS:
        path = root / f"{stem}.xlsx"
        if not path.is_file():
            raise FileNotFoundError(path)
        paths.append(path)
        df = pd.read_excel(path)
        audit = verify_columns(list(map(str, df.columns)), mode="train")
        meta["column_audit"][stem] = audit
        meta["files"].append({"stem": stem, "path": str(path), "rows": int(len(df)), "ncols": int(df.shape[1])})
        # 丢弃官方独有列（若误入）
        if "数据集划分" in df.columns:
            df = df.drop(columns=["数据集划分"])
        df = df.copy()
        df["__equipment_category"] = stem if "设备类别" not in df.columns else df["设备类别"].astype(str)
        # 统一设备类别为表名对应中文类别（以文件内设备类别众数为准也可；这里校验一致性）
        if "设备类别" in df.columns:
            cats = df["设备类别"].dropna().astype(str).unique().tolist()
            if len(cats) != 1:
                raise ValueError(f"multiple_equipment_category_in_file:{stem}:{cats}")
        df["__equipment_type"] = SHEET_TO_EQUIPMENT_TYPE[stem]
        df["__sheet"] = stem
        tables[stem] = df
    meta["dataset_fingerprint"] = dataset_fingerprint(paths)
    meta["n_columns_resolved"] = {
        "train_files": 33,
        "note": "训练集单文件为 33 列（4 标识+19 观测+10 目标）；官方开发集第 34 列为「数据集划分」，不进入特征白名单。",
    }
    return {"tables": tables, "meta": meta}


def load_test_tables(test_dir: Path | None = None) -> dict[str, Any]:
    root = test_dir or TEST_DIR
    tables: dict[str, pd.DataFrame] = {}
    meta: dict[str, Any] = {"files": [], "column_audit": {}}
    for stem in EQUIPMENT_FILE_STEMS:
        path = root / f"{stem}.xlsx"
        if not path.is_file():
            raise FileNotFoundError(path)
        df = pd.read_excel(path)
        audit = verify_columns(list(map(str, df.columns)), mode="test")
        meta["column_audit"][stem] = audit
        # 显式投影：即使误带目标列也丢弃
        keep = [c for c in ID_COLUMNS + FEATURE_WHITELIST if c in df.columns]
        projected = df[keep].copy()
        # 若测试缺设备类别则用文件名
        if "设备类别" not in projected.columns:
            raise ValueError(f"test_missing_设备类别:{stem}")
        projected["__equipment_type"] = SHEET_TO_EQUIPMENT_TYPE[stem]
        projected["__sheet"] = stem
        tables[stem] = projected
        meta["files"].append({"stem": stem, "path": str(path), "rows": int(len(projected)), "ncols_raw": int(df.shape[1])})
    return {"tables": tables, "meta": meta}


def project_features(df: pd.DataFrame) -> pd.DataFrame:
    """仅保留白名单观测 + 设备类别；不含 ID/目标。"""
    missing = [c for c in FEATURE_WHITELIST + ["设备类别"] if c not in df.columns]
    if missing:
        raise ValueError(f"missing_feature_columns:{missing}")
    out = df[FEATURE_WHITELIST + ["设备类别"]].copy()
    return out


def extract_targets(df: pd.DataFrame) -> pd.DataFrame:
    missing = [c for c in TARGET_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"missing_targets:{missing}")
    return df[TARGET_COLUMNS].copy()
