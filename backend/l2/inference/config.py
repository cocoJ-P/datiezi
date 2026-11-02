"""L2 推理服务配置（支持环境变量覆盖）。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

SERVICE_VERSION = "l2-inference-v1"
PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _env(name: str, default: str) -> str:
    value = os.getenv(name)
    return default if value is None or value.strip() == "" else value.strip()


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return float(raw)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


@dataclass(frozen=True)
class L2InferenceConfig:
    equipment_type: str
    model_bundle_id: str
    model_bundle_path: Path
    max_batch_size: int
    clear_probability_threshold: float
    fault_top1_threshold: float
    max_probability_gap: float
    enabled: bool

    @property
    def decision_thresholds(self) -> dict[str, float]:
        return {
            "clear_probability_threshold": self.clear_probability_threshold,
            "fault_top1_threshold": self.fault_top1_threshold,
            "max_probability_gap": self.max_probability_gap,
        }


def resolve_bundle_path(raw: str) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        path = (PROJECT_ROOT / path).resolve()
    return path


def load_config() -> L2InferenceConfig:
    default_bundle = "models/main_mill_motor/TR-0b062172078e"
    bundle_path = resolve_bundle_path(_env("L2_MODEL_BUNDLE_PATH", default_bundle))
    enabled = _env("L2_INFERENCE_ENABLED", "1") not in {"0", "false", "False", "no"}
    return L2InferenceConfig(
        equipment_type=_env("L2_EQUIPMENT_TYPE", "main_mill_motor"),
        model_bundle_id=_env("L2_MODEL_BUNDLE_ID", "TR-0b062172078e"),
        model_bundle_path=bundle_path,
        max_batch_size=_env_int("L2_MAX_BATCH_SIZE", 200),
        clear_probability_threshold=_env_float("L2_CLEAR_PROBABILITY_THRESHOLD", 0.80),
        fault_top1_threshold=_env_float("L2_FAULT_TOP1_THRESHOLD", 0.70),
        max_probability_gap=_env_float("L2_MAX_PROBABILITY_GAP", 0.20),
        enabled=enabled,
    )
