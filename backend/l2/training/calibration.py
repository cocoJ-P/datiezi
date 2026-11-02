"""概率校准：二分类 Sigmoid / 五分类 Temperature Scaling。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy.optimize import minimize_scalar
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression


@dataclass
class BinaryCalibrationResult:
    calibrator: Any
    method: str
    status: str
    reason: str
    params: dict[str, Any]


@dataclass
class MulticlassCalibrationResult:
    temperature: float
    method: str
    status: str
    reason: str
    params: dict[str, Any]


class PlattCalibrator:
    """在冻结基模型概率上拟合 Sigmoid（仅校准集）。"""

    def __init__(self, base_model: RandomForestClassifier):
        self.base_model = base_model
        self.logistic = LogisticRegression(solver="lbfgs", max_iter=1000)

    def fit(self, x_calib: np.ndarray, y_bin_calib: np.ndarray) -> "PlattCalibrator":
        raw = np.asarray(self.base_model.predict_proba(x_calib), dtype=float)[:, 1].reshape(-1, 1)
        self.logistic.fit(raw, y_bin_calib)
        return self

    def predict_proba(self, x: np.ndarray) -> np.ndarray:
        raw = np.asarray(self.base_model.predict_proba(x), dtype=float)[:, 1].reshape(-1, 1)
        pos = self.logistic.predict_proba(raw)[:, 1]
        return np.column_stack([1.0 - pos, pos])


def _safe_probs(proba: np.ndarray, eps: float = 1e-12) -> np.ndarray:
    clipped = np.clip(proba, eps, 1.0)
    clipped = clipped / clipped.sum(axis=1, keepdims=True)
    return clipped


def calibrate_binary(
    model: RandomForestClassifier,
    x_calib: np.ndarray,
    y_bin_calib: np.ndarray,
) -> BinaryCalibrationResult:
    if len(np.unique(y_bin_calib)) < 2:
        return BinaryCalibrationResult(
            calibrator=None,
            method="none",
            status="unverified",
            reason="校准集缺少二分类别，跳过 Platt 校准。",
            params={},
        )
    try:
        from sklearn.calibration import CalibratedClassifierCV
        from sklearn.frozen import FrozenEstimator

        calibrator = CalibratedClassifierCV(FrozenEstimator(model), method="sigmoid")
        calibrator.fit(x_calib, y_bin_calib)
        return BinaryCalibrationResult(
            calibrator=calibrator,
            method="sigmoid_platt",
            status="applied",
            reason="",
            params={"method": "sigmoid", "backend": "FrozenEstimator"},
        )
    except Exception:
        calibrator = PlattCalibrator(model).fit(x_calib, y_bin_calib)
        return BinaryCalibrationResult(
            calibrator=calibrator,
            method="sigmoid_platt_manual",
            status="applied",
            reason="使用手动 Platt 校准，仅拟合校准集概率，不重训基模型。",
            params={"method": "sigmoid", "backend": "manual_logistic"},
        )


def binary_proba(model: RandomForestClassifier, calibration: BinaryCalibrationResult, x: np.ndarray) -> np.ndarray:
    if calibration.calibrator is None:
        return np.asarray(model.predict_proba(x), dtype=float)
    return np.asarray(calibration.calibrator.predict_proba(x), dtype=float)


def _multiclass_nll(temperature: float, logits: np.ndarray, y: np.ndarray) -> float:
    temp = max(float(temperature), 1e-3)
    scaled = logits / temp
    scaled = scaled - scaled.max(axis=1, keepdims=True)
    exp = np.exp(scaled)
    probs = exp / exp.sum(axis=1, keepdims=True)
    probs = np.clip(probs, 1e-12, 1.0)
    return float(-np.mean(np.log(probs[np.arange(len(y)), y])))


def calibrate_multiclass_temperature(
    raw_proba_calib: np.ndarray,
    y_multi_calib: np.ndarray,
) -> MulticlassCalibrationResult:
    probs = _safe_probs(np.asarray(raw_proba_calib, dtype=float))
    logits = np.log(probs)
    preds = probs.argmax(axis=1)
    perfect = bool(np.all(preds == y_multi_calib))
    if perfect:
        return MulticlassCalibrationResult(
            temperature=1.0,
            method="temperature_scaling",
            status="unverified",
            reason="校准集全部正确分类；温度缩放易导致极端概率，保留 temperature=1.0。",
            params={"temperature": 1.0},
        )

    result = minimize_scalar(
        lambda t: _multiclass_nll(t, logits, y_multi_calib),
        bounds=(0.05, 5.0),
        method="bounded",
        options={"xatol": 1e-4},
    )
    temperature = float(result.x) if result.success else 1.0
    if not np.isfinite(temperature) or temperature <= 0:
        return MulticlassCalibrationResult(
            temperature=1.0,
            method="temperature_scaling",
            status="unverified",
            reason="温度优化未得到有限正数，保留 temperature=1.0。",
            params={"temperature": 1.0},
        )
    if temperature <= 0.2:
        return MulticlassCalibrationResult(
            temperature=1.0,
            method="temperature_scaling",
            status="unverified",
            reason="优化温度过低，概率将被过度锐化；保留 temperature=1.0。",
            params={"temperature": 1.0, "optimized_temperature": temperature},
        )
    status = "applied" if abs(temperature - 1.0) > 1e-4 else "unverified"
    reason = "" if status == "applied" else "优化温度接近 1.0，视为未显著改变概率。"
    return MulticlassCalibrationResult(
        temperature=temperature,
        method="temperature_scaling",
        status=status,
        reason=reason,
        params={"temperature": temperature},
    )


def apply_temperature(raw_proba: np.ndarray, temperature: float) -> np.ndarray:
    probs = _safe_probs(np.asarray(raw_proba, dtype=float))
    logits = np.log(probs)
    temp = max(float(temperature), 1e-3)
    scaled = logits / temp
    scaled = scaled - scaled.max(axis=1, keepdims=True)
    exp = np.exp(scaled)
    out = exp / exp.sum(axis=1, keepdims=True)
    return _safe_probs(out)
