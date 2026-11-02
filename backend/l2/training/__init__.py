"""L2 Training Core：本地双分类器离线训练内核。"""

from l2.training.artifacts import load_model_bundle, predict_bundle, verify_bundle
from l2.training.pipeline import run_training

__all__ = ["run_training", "load_model_bundle", "predict_bundle", "verify_bundle"]
