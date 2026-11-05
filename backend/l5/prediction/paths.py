"""路径约定。"""

from __future__ import annotations

from pathlib import Path

# .../backend/l5/prediction/paths.py → parents[2]=backend, parents[3]=repo
BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_ROOT.parent
DATA_ROOT = REPO_ROOT / "data" / "钢铁数据集"
TRAIN_DIR = DATA_ROOT / "训练集"
TEST_DIR = DATA_ROOT / "测试集"
OFFICIAL_DEV = REPO_ROOT / "data" / "official_dataset" / "钢铁设备数据集_开发集.xlsx"

ARTIFACT_ROOT = REPO_ROOT / "models" / "l5_prediction"
DEFAULT_BUNDLE = ARTIFACT_ROOT / "L5P-v1"
