"""评测指标与双模型一致性。"""

from __future__ import annotations

from typing import Any

import numpy as np
from sklearn.metrics import (
    accuracy_score,
    brier_score_loss,
    classification_report,
    confusion_matrix,
    f1_score,
    log_loss,
    precision_score,
    recall_score,
)

from l2.training.schema import BINARY_LABELS, MULTICLASS_LABELS


def _safe_probs(proba: np.ndarray) -> np.ndarray:
    clipped = np.clip(proba, 1e-12, 1.0)
    return clipped / clipped.sum(axis=1, keepdims=True)


def multiclass_brier(y_true: np.ndarray, proba: np.ndarray, n_classes: int) -> float:
    """多分类 Brier：对 one-hot 与概率向量平方误差求均值。"""
    probs = _safe_probs(proba)
    one_hot = np.eye(n_classes)[y_true]
    return float(np.mean(np.sum((probs - one_hot) ** 2, axis=1)))


def classification_block(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    proba: np.ndarray,
    *,
    labels: list[int],
    target_names: list[str],
    multiclass: bool,
) -> dict[str, Any]:
    probs = _safe_probs(proba)
    metrics = {
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "precision_macro": float(precision_score(y_true, y_pred, average="macro", zero_division=0)),
        "recall_macro": float(recall_score(y_true, y_pred, average="macro", zero_division=0)),
        "macro_f1": float(f1_score(y_true, y_pred, average="macro", zero_division=0)),
        "log_loss": float(log_loss(y_true, probs, labels=labels)),
        "confusion_matrix": confusion_matrix(y_true, y_pred, labels=labels).tolist(),
        "classification_report": classification_report(
            y_true,
            y_pred,
            labels=labels,
            target_names=target_names,
            zero_division=0,
            output_dict=True,
        ),
    }
    if multiclass:
        metrics["brier_score"] = multiclass_brier(y_true, probs, len(labels))
        metrics["brier_definition"] = "mean_sum_squared_error_one_hot_vs_proba"
    else:
        metrics["brier_score"] = float(brier_score_loss(y_true, probs[:, 1]))
    return metrics


def consistency_analysis(
    y_bin_true: np.ndarray,
    binary_proba: np.ndarray,
    multiclass_proba: np.ndarray,
    record_ids: list[str],
    *,
    normal_class_index: int = 0,
) -> dict[str, Any]:
    p_a = _safe_probs(binary_proba)[:, 1]
    p_b = 1.0 - _safe_probs(multiclass_proba)[:, normal_class_index]
    pred_a = (p_a >= 0.5).astype(int)
    pred_b = (p_b >= 0.5).astype(int)
    gap = np.abs(p_a - p_b)
    agree = pred_a == pred_b
    conflict_idx = np.where(~agree)[0].tolist()
    return {
        "agreement_rate": float(np.mean(agree)),
        "conflict_count": int(np.sum(~agree)),
        "mean_probability_gap": float(np.mean(gap)),
        "max_probability_gap": float(np.max(gap)),
        "probability_gap_percentiles": {
            "p50": float(np.percentile(gap, 50)),
            "p90": float(np.percentile(gap, 90)),
            "p99": float(np.percentile(gap, 99)),
        },
        "conflict_record_ids": [record_ids[i] for i in conflict_idx[:200]],
        "note": "两模型一致只代表模型一致性，不代表独立物理证据验证。",
    }


def evaluate_pair(
    *,
    record_ids: list[str],
    y_bin_true: np.ndarray,
    y_multi_true: np.ndarray,
    binary_raw: np.ndarray,
    binary_final: np.ndarray,
    multi_raw: np.ndarray,
    multi_final: np.ndarray,
    binary_labels: list[str] | None = None,
    multiclass_labels: list[str] | None = None,
    normal_label: str = "正常",
) -> dict[str, Any]:
    bin_names = list(binary_labels) if binary_labels is not None else list(BINARY_LABELS)
    multi_names = list(multiclass_labels) if multiclass_labels is not None else list(MULTICLASS_LABELS)
    normal_idx = multi_names.index(normal_label) if normal_label in multi_names else 0
    bin_pred = binary_final.argmax(axis=1)
    multi_pred = multi_final.argmax(axis=1)
    binary_metrics = {
        "raw": classification_block(
            y_bin_true,
            binary_raw.argmax(axis=1),
            binary_raw,
            labels=[0, 1],
            target_names=bin_names,
            multiclass=False,
        ),
        "final": classification_block(
            y_bin_true,
            bin_pred,
            binary_final,
            labels=[0, 1],
            target_names=bin_names,
            multiclass=False,
        ),
    }
    multi_metrics = {
        "raw": classification_block(
            y_multi_true,
            multi_raw.argmax(axis=1),
            multi_raw,
            labels=list(range(len(multi_names))),
            target_names=multi_names,
            multiclass=True,
        ),
        "final": classification_block(
            y_multi_true,
            multi_pred,
            multi_final,
            labels=list(range(len(multi_names))),
            target_names=multi_names,
            multiclass=True,
        ),
    }
    consistency = consistency_analysis(
        y_bin_true,
        binary_final,
        multi_final,
        record_ids,
        normal_class_index=normal_idx,
    )
    binary_errors = [record_ids[i] for i in np.where(bin_pred != y_bin_true)[0].tolist()]
    multi_errors = [record_ids[i] for i in np.where(multi_pred != y_multi_true)[0].tolist()]
    return {
        "binary": binary_metrics,
        "multiclass": multi_metrics,
        "consistency": consistency,
        "binary_error_record_ids": binary_errors[:200],
        "multiclass_error_record_ids": multi_errors[:200],
        "disclaimer": "这是固定比赛数据集的内部测试结果，不代表真实工业场景泛化能力。",
    }
