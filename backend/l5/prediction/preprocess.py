"""仅在训练区拟合的预处理。"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from l5.prediction.contract import FEATURE_WHITELIST


class FeaturePreprocessor:
    """数值缺失用训练区中位数填充；设备类别保持字符串给 CatBoost。"""

    def __init__(self) -> None:
        self.medians_: dict[str, float] = {}
        self.feature_names_: list[str] = FEATURE_WHITELIST + ["设备类别"]
        self.fitted_ = False

    def fit(self, df: pd.DataFrame) -> FeaturePreprocessor:
        x = df[self.feature_names_].copy()
        for col in FEATURE_WHITELIST:
            series = pd.to_numeric(x[col], errors="coerce")
            med = float(series.median()) if series.notna().any() else 0.0
            self.medians_[col] = med
        self.fitted_ = True
        return self

    def transform(self, df: pd.DataFrame) -> pd.DataFrame:
        if not self.fitted_:
            raise RuntimeError("preprocessor_not_fitted")
        x = df[self.feature_names_].copy()
        for col in FEATURE_WHITELIST:
            x[col] = pd.to_numeric(x[col], errors="coerce").fillna(self.medians_[col])
        x["设备类别"] = x["设备类别"].astype(str)
        return x

    def fit_transform(self, df: pd.DataFrame) -> pd.DataFrame:
        return self.fit(df).transform(df)

    def to_dict(self) -> dict[str, Any]:
        return {"medians": self.medians_, "feature_names": self.feature_names_}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> FeaturePreprocessor:
        obj = cls()
        obj.medians_ = {k: float(v) for k, v in (payload.get("medians") or {}).items()}
        obj.feature_names_ = list(payload.get("feature_names") or FEATURE_WHITELIST + ["设备类别"])
        obj.fitted_ = True
        return obj


def matrix_for_catboost(x: pd.DataFrame) -> tuple[np.ndarray, list[str], list[int]]:
    """返回 ndarray、列名、类别特征下标。"""
    cols = list(x.columns)
    cat_idx = [cols.index("设备类别")] if "设备类别" in cols else []
    # CatBoost 接受 DataFrame 更稳；这里仍返回 DataFrame 由调用方决定
    return x, cols, cat_idx
