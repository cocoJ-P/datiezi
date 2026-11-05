"""L2 Management 配置。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from l2.inference.config import PROJECT_ROOT

SERVICE_VERSION = "l2-management-v1"


def _env(name: str, default: str = "") -> str:
    value = os.getenv(name)
    return default if value is None or value.strip() == "" else value.strip()


def _resolve(raw: str) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        path = (PROJECT_ROOT / path).resolve()
    return path


@dataclass(frozen=True)
class DatasetRef:
    dataset_id: str
    equipment_type: str
    features_path: Path
    labels_path: Path
    dataset_revision_id: str = "rev1"


@dataclass(frozen=True)
class ManagementConfig:
    db_path: Path
    models_root: Path
    logs_root: Path
    datasets: dict[str, DatasetRef]
    training_config_path: Path
    poll_interval_sec: float
    max_concurrent_training_jobs: int = 1
    management_token: str = ""


def default_datasets() -> dict[str, DatasetRef]:
    config_file = Path(__file__).resolve().parent / "config" / "datasets.yaml"
    if config_file.is_file():
        payload = yaml.safe_load(config_file.read_text(encoding="utf-8")) or {}
        items = payload.get("datasets") or {}
        out: dict[str, DatasetRef] = {}
        for dataset_id, body in items.items():
            out[dataset_id] = DatasetRef(
                dataset_id=dataset_id,
                equipment_type=str(body["equipment_type"]),
                features_path=_resolve(str(body["features_path"])),
                labels_path=_resolve(str(body["labels_path"])),
                dataset_revision_id=str(body.get("dataset_revision_id") or "rev1"),
            )
        return out
    return {
        "main_mill_motor_dev": DatasetRef(
            dataset_id="main_mill_motor_dev",
            equipment_type="main_mill_motor",
            features_path=_resolve("data/l2_dev/main_mill_motor/features.csv"),
            labels_path=_resolve("data/l2_dev/main_mill_motor/labels.csv"),
        ),
        "high_pressure_descaling_pump_dev": DatasetRef(
            dataset_id="high_pressure_descaling_pump_dev",
            equipment_type="high_pressure_descaling_pump",
            features_path=_resolve("data/l2_dev/high_pressure_descaling_pump/features.csv"),
            labels_path=_resolve("data/l2_dev/high_pressure_descaling_pump/labels.csv"),
        ),
    }


def load_management_config() -> ManagementConfig:
    return ManagementConfig(
        db_path=_resolve(_env("L2_MANAGEMENT_DB", "backend/data/l2_management.sqlite3")),
        models_root=_resolve(_env("L2_MODELS_ROOT", "models")),
        logs_root=_resolve(_env("L2_TRAINING_LOGS_ROOT", "backend/data/l2_training_logs")),
        datasets=default_datasets(),
        training_config_path=_resolve(
            _env("L2_TRAINING_CONFIG", "backend/l2/training/config/main_mill_motor.yaml")
        ),
        poll_interval_sec=float(_env("L2_WORKER_POLL_INTERVAL", "2") or "2"),
        management_token=_env("L2_MANAGEMENT_TOKEN", ""),
        max_concurrent_training_jobs=1,
    )
