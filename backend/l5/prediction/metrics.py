"""评测指标。"""

from __future__ import annotations

from typing import Any

import numpy as np
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    confusion_matrix,
    f1_score,
    mean_absolute_error,
    mean_squared_error,
)

from l5.prediction.contract import PRIORITY_ORDER, SEVERITY_ORDER, STATUS_ORDER


def _ord_map(order: list[str]) -> dict[str, int]:
    return {v: i for i, v in enumerate(order)}


def adjacent_hit_rate(y_true: list[str], y_pred: list[str], order: list[str]) -> float:
    m = _ord_map(order)
    vals = []
    for t, p in zip(y_true, y_pred):
        if t not in m or p not in m:
            vals.append(0.0)
            continue
        vals.append(1.0 if abs(m[t] - m[p]) <= 1 else 0.0)
    return float(np.mean(vals)) if vals else 0.0


def ordinal_mae(y_true: list[str], y_pred: list[str], order: list[str]) -> float:
    m = _ord_map(order)
    errs = []
    for t, p in zip(y_true, y_pred):
        if t not in m or p not in m:
            continue
        errs.append(abs(m[t] - m[p]))
    return float(np.mean(errs)) if errs else float("nan")


def major_misrank_rate(y_true: list[str], y_pred: list[str], order: list[str], threshold: int = 2) -> float:
    m = _ord_map(order)
    flags = []
    for t, p in zip(y_true, y_pred):
        if t not in m or p not in m:
            flags.append(1.0)
            continue
        flags.append(1.0 if abs(m[t] - m[p]) >= threshold else 0.0)
    return float(np.mean(flags)) if flags else 0.0


def classification_metrics(
    y_true: list[str],
    y_pred: list[str],
    *,
    labels: list[str] | None = None,
    ordinal_order: list[str] | None = None,
) -> dict[str, Any]:
    labels = labels or sorted(set(y_true) | set(y_pred))
    out: dict[str, Any] = {
        "n": len(y_true),
        "n_classes_true": len(set(y_true)),
        "n_classes_pred": len(set(y_pred)),
        "unseen_in_pred_vocab": sorted(set(y_true) - set(labels)),
        "accuracy": float(accuracy_score(y_true, y_pred)) if y_true else 0.0,
        "macro_f1": float(f1_score(y_true, y_pred, average="macro", zero_division=0)) if y_true else 0.0,
        "labels": labels,
        "confusion_matrix": confusion_matrix(y_true, y_pred, labels=labels).tolist() if y_true else [],
        "classification_report": classification_report(
            y_true, y_pred, labels=labels, output_dict=True, zero_division=0
        )
        if y_true
        else {},
        "exact_match_rate": float(np.mean([a == b for a, b in zip(y_true, y_pred)])) if y_true else 0.0,
    }
    if ordinal_order:
        out["adjacent_hit_rate"] = adjacent_hit_rate(y_true, y_pred, ordinal_order)
        out["ordinal_mae"] = ordinal_mae(y_true, y_pred, ordinal_order)
        out["major_misrank_rate"] = major_misrank_rate(y_true, y_pred, ordinal_order)
    return out


def regression_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, Any]:
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    return {
        "n": int(len(y_true)),
        "mae": float(mean_absolute_error(y_true, y_pred)) if len(y_true) else float("nan"),
        "rmse": float(np.sqrt(mean_squared_error(y_true, y_pred))) if len(y_true) else float("nan"),
        "y_true_min": float(y_true.min()) if len(y_true) else None,
        "y_true_max": float(y_true.max()) if len(y_true) else None,
        "y_pred_min": float(y_pred.min()) if len(y_pred) else None,
        "y_pred_max": float(y_pred.max()) if len(y_pred) else None,
    }


ORDINAL_FOR_TARGET = {
    "设备状态": STATUS_ORDER,
    "故障严重度": SEVERITY_ORDER,
    "优先级": PRIORITY_ORDER,
}
