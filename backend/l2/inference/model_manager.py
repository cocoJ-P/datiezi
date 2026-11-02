"""多设备模型生命周期：按 equipment_type 常驻加载与安全切换。"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from l2.inference.config import L2InferenceConfig, load_config
from l2.training import load_model_bundle
from l2.training.artifacts import ModelBundle

LOG = logging.getLogger("l2.inference")


class ModelNotReadyError(Exception):
    def __init__(self, message: str, *, error_code: str = "MODEL_NOT_READY"):
        super().__init__(message)
        self.message = message
        self.error_code = error_code


@dataclass
class LoadedModelState:
    bundle: ModelBundle
    config: L2InferenceConfig
    load_time_ms: float
    loaded_at: float


class ModelManager:
    def __init__(self) -> None:
        self._global_lock = threading.RLock()
        self._equipment_locks: dict[str, threading.RLock] = {}
        self._states: dict[str, LoadedModelState] = {}
        self._errors: dict[str, str] = {}
        self._load_count = 0

    def _lock_for(self, equipment_type: str) -> threading.RLock:
        with self._global_lock:
            if equipment_type not in self._equipment_locks:
                self._equipment_locks[equipment_type] = threading.RLock()
            return self._equipment_locks[equipment_type]

    @property
    def load_count(self) -> int:
        return self._load_count

    @property
    def last_error(self) -> str | None:
        if not self._errors:
            return None
        # 兼容旧字段：返回任意一个错误摘要
        equipment, message = next(iter(self._errors.items()))
        return f"{equipment}: {message}"

    def is_ready(self, equipment_type: str | None = None) -> bool:
        with self._global_lock:
            if equipment_type is None:
                return bool(self._states)
            return equipment_type in self._states

    def loaded_equipment_types(self) -> list[str]:
        with self._global_lock:
            return sorted(self._states.keys())

    def _build_state(self, cfg: L2InferenceConfig) -> LoadedModelState:
        started = time.perf_counter()
        if not cfg.model_bundle_path.is_dir():
            raise FileNotFoundError(f"模型包目录不存在：{cfg.model_bundle_path}")
        bundle = load_model_bundle(cfg.model_bundle_path)
        manifest = bundle.manifest
        equipment = str(manifest.get("equipment_type") or "")
        bundle_id = str(manifest.get("model_bundle_id") or "")
        if equipment != cfg.equipment_type:
            raise ValueError(
                f"模型包设备类型 {equipment} 与配置 {cfg.equipment_type} 不一致。"
            )
        if cfg.model_bundle_id and bundle_id != cfg.model_bundle_id:
            raise ValueError(
                f"模型包 ID {bundle_id} 与配置 {cfg.model_bundle_id} 不一致。"
            )
        if not bundle.feature_schema.get("feature_columns"):
            raise ValueError("模型包缺少 feature_schema.feature_columns。")
        if not bundle.label_map.get("binary_labels") or not bundle.label_map.get("multiclass_labels"):
            raise ValueError("模型包缺少类别映射。")
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return LoadedModelState(bundle=bundle, config=cfg, load_time_ms=elapsed_ms, loaded_at=time.time())

    def load(self, config: L2InferenceConfig | None = None) -> LoadedModelState:
        """加载单个设备模型（兼容旧启动路径）。"""
        cfg = config or load_config()
        return self.load_equipment(cfg.equipment_type, cfg)

    def load_equipment(self, equipment_type: str, config: L2InferenceConfig) -> LoadedModelState:
        if config.equipment_type != equipment_type:
            config = replace(config, equipment_type=equipment_type)
        state = self._build_state(config)
        lock = self._lock_for(equipment_type)
        with lock:
            with self._global_lock:
                self._states[equipment_type] = state
                self._errors.pop(equipment_type, None)
                self._load_count += 1
        LOG.info(
            "L2 模型已加载 equipment=%s bundle=%s load_ms=%.1f",
            equipment_type,
            state.bundle.manifest.get("model_bundle_id"),
            state.load_time_ms,
        )
        return state

    def preload(self, config: L2InferenceConfig) -> LoadedModelState:
        return self._build_state(config)

    def swap_preloaded(self, state: LoadedModelState) -> LoadedModelState | None:
        equipment_type = state.config.equipment_type
        lock = self._lock_for(equipment_type)
        with lock:
            with self._global_lock:
                previous = self._states.get(equipment_type)
                self._states[equipment_type] = state
                self._errors.pop(equipment_type, None)
                self._load_count += 1
                LOG.info(
                    "L2 模型已切换 equipment=%s bundle=%s previous=%s",
                    equipment_type,
                    state.bundle.manifest.get("model_bundle_id"),
                    None if previous is None else previous.bundle.manifest.get("model_bundle_id"),
                )
                return previous

    def restore_state(self, state: LoadedModelState | None, *, equipment_type: str | None = None) -> None:
        eq = equipment_type or (None if state is None else state.config.equipment_type)
        if eq is None:
            return
        lock = self._lock_for(eq)
        with lock:
            with self._global_lock:
                if state is None:
                    self._states.pop(eq, None)
                else:
                    self._states[eq] = state

    def require(self, equipment_type: str | None = None) -> LoadedModelState:
        with self._global_lock:
            if equipment_type is None:
                if len(self._states) == 1:
                    return next(iter(self._states.values()))
                if "main_mill_motor" in self._states:
                    return self._states["main_mill_motor"]
                if not self._states:
                    raise ModelNotReadyError(self.last_error or "L2 模型尚未加载。")
                raise ModelNotReadyError("请指定 equipment_type。")
            state = self._states.get(equipment_type)
            if state is None:
                err = self._errors.get(equipment_type)
                raise ModelNotReadyError(
                    err or f"设备 {equipment_type} 没有可用的 Active 模型。",
                    error_code="MODEL_NOT_AVAILABLE",
                )
            return state

    def mark_error(self, message: str, *, equipment_type: str | None = None) -> None:
        with self._global_lock:
            if equipment_type:
                self._states.pop(equipment_type, None)
                self._errors[equipment_type] = message
            else:
                self._states.clear()
                self._errors["*"] = message

    def unload(self, equipment_type: str | None = None) -> None:
        with self._global_lock:
            if equipment_type is None:
                self._states.clear()
            else:
                self._states.pop(equipment_type, None)

    def health(self) -> dict[str, Any]:
        with self._global_lock:
            equipment = {}
            for name, state in self._states.items():
                equipment[name] = {
                    "model_loaded": True,
                    "model_bundle_id": state.bundle.manifest.get("model_bundle_id"),
                    "load_time_ms": state.load_time_ms,
                    "status": "ready",
                }
            for name, err in self._errors.items():
                if name == "*":
                    continue
                equipment.setdefault(
                    name,
                    {
                        "model_loaded": False,
                        "model_bundle_id": None,
                        "load_time_ms": None,
                        "status": "unavailable",
                        "error": err,
                    },
                )
            any_ready = bool(self._states)
            # 兼容旧单设备字段：优先 main_mill_motor
            primary = self._states.get("main_mill_motor") or (
                next(iter(self._states.values())) if self._states else None
            )
            return {
                "service_status": "ready" if any_ready else "not_ready",
                "model_loaded": any_ready,
                "model_bundle_id": None
                if primary is None
                else primary.bundle.manifest.get("model_bundle_id"),
                "equipment_type": None if primary is None else primary.config.equipment_type,
                "equipment": equipment,
                "service_version": "l2-inference-v1",
                "load_count": self._load_count,
                "last_error": self.last_error,
                "load_time_ms": None if primary is None else primary.load_time_ms,
            }

    def model_info(self, equipment_type: str | None = None) -> dict[str, Any]:
        state = self.require(equipment_type)
        manifest = state.bundle.manifest
        schema = state.bundle.feature_schema
        return {
            "model_bundle_id": manifest.get("model_bundle_id"),
            "equipment_type": manifest.get("equipment_type"),
            "feature_schema_version": schema.get("version") or manifest.get("feature_schema_version"),
            "feature_count": len(schema.get("feature_columns") or []),
            "feature_columns": list(schema.get("feature_columns") or []),
            "class_labels": manifest.get("class_labels"),
            "binary_algorithm": "RandomForestClassifier",
            "multiclass_algorithm": "CatBoostClassifier",
            "calibration_status": manifest.get("calibration_status"),
            "model_status": manifest.get("status"),
            "created_at": manifest.get("created_at"),
            "evaluation_summary": manifest.get("evaluation_summary"),
            "decision_thresholds": state.config.decision_thresholds,
            "max_batch_size": state.config.max_batch_size,
        }


MODEL_MANAGER = ModelManager()
