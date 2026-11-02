"""双分类器训练。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np
from catboost import CatBoostClassifier
from sklearn.ensemble import RandomForestClassifier

from l2.training.schema import BINARY_LABELS, MULTICLASS_LABELS


@dataclass
class TrainedBinary:
    model: RandomForestClassifier
    class_names: tuple[str, ...]
    params: dict[str, Any]


@dataclass
class TrainedMulticlass:
    model: CatBoostClassifier
    class_names: tuple[str, ...]
    params: dict[str, Any]


def train_binary_classifier(
    x_train: np.ndarray,
    y_bin_train: np.ndarray,
    params: dict[str, Any] | None = None,
    *,
    class_names: Sequence[str] | None = None,
) -> TrainedBinary:
    names = tuple(class_names) if class_names is not None else BINARY_LABELS
    defaults = {
        "n_estimators": 250,
        "max_depth": 12,
        "min_samples_leaf": 2,
        "random_state": 42,
        "n_jobs": -1,
        "class_weight": "balanced_subsample",
    }
    cfg = {**defaults, **(params or {})}
    model = RandomForestClassifier(**cfg)
    model.fit(x_train, y_bin_train)
    # 强制按业务顺序解释：0=正常, 1=非正常
    if list(model.classes_) != [0, 1]:
        raise RuntimeError(f"二分类 classes_ 不符合预期：{model.classes_}")
    return TrainedBinary(model=model, class_names=names, params=cfg)


def train_multiclass_classifier(
    x_train: np.ndarray,
    y_multi_train: np.ndarray,
    params: dict[str, Any] | None = None,
    *,
    class_names: Sequence[str] | None = None,
) -> TrainedMulticlass:
    names = tuple(class_names) if class_names is not None else MULTICLASS_LABELS
    n_classes = len(names)
    unique = sorted({int(v) for v in np.unique(y_multi_train)})
    if unique != list(range(n_classes)):
        raise RuntimeError(f"多分类标签索引不符合 0..{n_classes-1}：{unique}")

    defaults = {
        "iterations": 350,
        "depth": 5,
        "learning_rate": 0.06,
        "loss_function": "MultiClass" if n_classes > 2 else "Logloss",
        "random_seed": 42,
        "task_type": "CPU",
        "verbose": False,
        "allow_writing_files": False,
        "classes_count": n_classes if n_classes > 2 else None,
    }
    cfg = {**defaults, **(params or {})}
    # CatBoost 二类时 classes_count 可不设；清理 None
    clean = {k: v for k, v in cfg.items() if v is not None}
    if n_classes == 2 and clean.get("loss_function") == "MultiClass":
        clean["loss_function"] = "Logloss"
    model = CatBoostClassifier(**clean)
    model.fit(x_train, y_multi_train)
    classes = [int(v) for v in model.classes_]
    if classes != list(range(n_classes)):
        raise RuntimeError(f"多分类 classes_ 不符合固定顺序：{classes} expected={list(range(n_classes))}")
    return TrainedMulticlass(model=model, class_names=names, params=clean)


def predict_proba_binary(model: RandomForestClassifier, x: np.ndarray) -> np.ndarray:
    proba = model.predict_proba(x)
    return np.asarray(proba, dtype=float)


def predict_proba_multiclass(model: CatBoostClassifier, x: np.ndarray) -> np.ndarray:
    proba = model.predict_proba(x)
    arr = np.asarray(proba, dtype=float)
    if arr.ndim == 1:
        # 二分类 Logloss 可能返回一维正类概率
        arr = np.column_stack([1.0 - arr, arr])
    return arr
