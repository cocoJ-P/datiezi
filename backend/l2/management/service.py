"""Management 业务服务。"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from l2.equipment.registry import get_equipment_spec, has_equipment, list_equipment_types
from l2.inference.model_manager import MODEL_MANAGER, ModelManager
from l2.management.activation import activate_model, rollback_model
from l2.management.audit import list_events
from l2.management.config import ManagementConfig, load_management_config
from l2.management.database import connect, init_db
from l2.management.errors import ManagementError
from l2.management.jobs import create_training_job, get_job, list_jobs
from l2.management.registry import (
    bootstrap_existing_candidates,
    get_active_model,
    get_model,
    list_models,
    register_bundle,
)


class ManagementService:
    def __init__(
        self,
        config: ManagementConfig | None = None,
        manager: ModelManager | None = None,
    ):
        self.config = config or load_management_config()
        self.manager = manager or MODEL_MANAGER
        init_db(self.config.db_path)

    def connection(self) -> sqlite3.Connection:
        return connect(self.config.db_path)

    def require_token(self, token: str | None) -> None:
        expected = self.config.management_token
        if not expected:
            raise ManagementError(
                "MANAGEMENT_UNAUTHORIZED",
                "未配置 L2_MANAGEMENT_TOKEN，拒绝管理写操作。",
                status_code=401,
            )
        if not token or token.strip() != expected:
            raise ManagementError(
                "MANAGEMENT_UNAUTHORIZED",
                "管理 Token 无效。",
                status_code=401,
            )

    def list_equipment_types_api(self) -> dict[str, Any]:
        connection = self.connection()
        try:
            items = []
            for spec in list_equipment_types():
                active = get_active_model(connection, spec.equipment_type)
                items.append(
                    {
                        **spec.to_summary(),
                        "has_active_model": active is not None,
                        "active_model_bundle_id": None if active is None else active["model_bundle_id"],
                    }
                )
            return {"equipment_types": items}
        finally:
            connection.close()

    def get_equipment_type_api(self, equipment_type: str) -> dict[str, Any]:
        if not has_equipment(equipment_type):
            raise ManagementError(
                "DATASET_NOT_FOUND",
                f"未注册设备类型：{equipment_type}",
                status_code=404,
            )
        spec = get_equipment_spec(equipment_type)
        connection = self.connection()
        try:
            active = get_active_model(connection, equipment_type)
        finally:
            connection.close()
        return {
            **spec.to_summary(),
            "feature_schema": spec.feature_schema_dict(),
            "has_active_model": active is not None,
            "active_model_bundle_id": None if active is None else active["model_bundle_id"],
            "runtime_loaded": self.manager.is_ready(equipment_type),
        }

    def list_datasets(self) -> dict[str, Any]:
        items = []
        for dataset_id, ref in self.config.datasets.items():
            schema_version = None
            label_count = None
            registration_status = "unregistered"
            feature_count = None
            if has_equipment(ref.equipment_type):
                spec = get_equipment_spec(ref.equipment_type)
                schema_version = spec.schema_version
                registration_status = "registered"
                label_count = spec.class_count
                feature_count = spec.feature_count
            record_count = None
            if ref.features_path.is_file():
                try:
                    with ref.features_path.open("r", encoding="utf-8-sig") as handle:
                        record_count = max(sum(1 for _ in handle) - 1, 0)
                except OSError:
                    record_count = None
            items.append(
                {
                    "dataset_id": dataset_id,
                    "revision_id": ref.dataset_revision_id,
                    "equipment_type": ref.equipment_type,
                    "schema_version": schema_version,
                    "source_type": "legacy_csv",
                    "release_level": "development",
                    "release_status": "ready",
                    "training_ready": ref.features_path.is_file() and ref.labels_path.is_file(),
                    "inference_ready": ref.features_path.is_file(),
                    "record_count": record_count,
                    "feature_count": feature_count,
                    "label_count": label_count,
                    "warning_count": 0,
                    "registration_status": registration_status,
                    "features_exists": ref.features_path.is_file(),
                    "labels_exists": ref.labels_path.is_file(),
                }
            )
        try:
            from l2.prepared.catalog import list_published_datasets

            for item in list_published_datasets():
                items.append(
                    {
                        "dataset_id": item["prepared_dataset_id"],
                        "prepared_dataset_id": item["prepared_dataset_id"],
                        "revision_id": item.get("revision_id"),
                        "equipment_type": item.get("equipment_type"),
                        "schema_version": item.get("schema_version"),
                        "source_type": "l1_prepared",
                        "release_level": item.get("release_level"),
                        "release_status": item.get("release_status"),
                        "training_ready": bool(item.get("training_ready")),
                        "inference_ready": bool(item.get("inference_ready")),
                        "record_count": item.get("record_count"),
                        "feature_count": item.get("feature_count"),
                        "label_count": item.get("label_count"),
                        "warning_count": item.get("warning_count", 0),
                        "l1_prepared_version_id": item.get("l1_prepared_version_id"),
                        "registration_status": "registered"
                        if has_equipment(str(item.get("equipment_type") or ""))
                        else "unregistered",
                    }
                )
        except Exception:
            # 目录缺失时不影响 Legacy CSV 列表
            pass
        return {"datasets": items}

    def submit_train_job(
        self,
        *,
        equipment_type: str,
        dataset_id: str | None = None,
        prepared_dataset_id: str | None = None,
    ) -> dict[str, Any]:
        if not has_equipment(equipment_type):
            raise ManagementError(
                "DATASET_NOT_FOUND",
                f"未注册设备类型：{equipment_type}",
                status_code=404,
            )
        if not prepared_dataset_id and not dataset_id:
            raise ManagementError(
                "DATASET_NOT_FOUND",
                "必须提供 dataset_id 或 prepared_dataset_id。",
                status_code=400,
            )
        if prepared_dataset_id:
            # PED-* 优先；也允许把 dataset_id 写成 PED-*
            pass
        elif dataset_id and str(dataset_id).startswith("PED-"):
            prepared_dataset_id = dataset_id
            dataset_id = prepared_dataset_id
        elif dataset_id not in self.config.datasets:
            raise ManagementError("DATASET_NOT_FOUND", f"数据集不存在：{dataset_id}", status_code=404)
        connection = self.connection()
        try:
            job = create_training_job(
                connection,
                self.config,
                equipment_type=equipment_type,
                dataset_id=dataset_id or prepared_dataset_id or "",
                prepared_dataset_id=prepared_dataset_id,
            )
            connection.commit()
            return {
                "job_id": job["job_id"],
                "status": job["status"],
                "equipment_type": job["equipment_type"],
                "dataset_id": job["dataset_id"],
                "dataset_revision_id": job.get("dataset_revision_id"),
                "created_at": job["created_at"],
            }
        except KeyError as exc:
            raise ManagementError("DATASET_NOT_FOUND", f"数据集不存在：{exc}", status_code=404) from exc
        except FileNotFoundError as exc:
            raise ManagementError("DATASET_NOT_FOUND", str(exc), status_code=404) from exc
        except ValueError as exc:
            message = str(exc)
            code = "EQUIPMENT_CONFIG_REQUIRED" if "未确认" in message or "Schema" in message else "DATASET_NOT_FOUND"
            raise ManagementError(code, message, status_code=400) from exc
        finally:
            connection.close()

    def list_train_jobs(self, *, status: str | None = None, offset: int = 0, limit: int = 50) -> dict[str, Any]:
        connection = self.connection()
        try:
            return list_jobs(connection, status=status, offset=offset, limit=limit)
        finally:
            connection.close()

    def get_train_job(self, job_id: str) -> dict[str, Any]:
        connection = self.connection()
        try:
            return get_job(connection, job_id)
        except KeyError as exc:
            raise ManagementError("TRAINING_JOB_NOT_FOUND", f"训练任务不存在：{job_id}", status_code=404) from exc
        finally:
            connection.close()

    def get_train_job_logs(self, job_id: str, *, max_lines: int = 200) -> dict[str, Any]:
        job = self.get_train_job(job_id)
        log_path = Path(job.get("log_path") or "")
        lines: list[str] = []
        truncated = False
        if log_path.is_file():
            raw = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
            if len(raw) > max_lines:
                truncated = True
                lines = raw[-max_lines:]
            else:
                lines = raw
        return {
            "job_id": job_id,
            "log_path": str(log_path) if log_path else None,
            "max_lines": max_lines,
            "truncated": truncated,
            "lines": lines,
        }

    def bootstrap_models(self, *, equipment_type: str = "main_mill_motor") -> dict[str, Any]:
        connection = self.connection()
        try:
            rows = bootstrap_existing_candidates(connection, self.config, equipment_type=equipment_type)
            connection.commit()
            return {"registered": rows, "count": len(rows)}
        finally:
            connection.close()

    def register_local_bundle(self, model_bundle_id: str, *, equipment_type: str) -> dict[str, Any]:
        if "/" in model_bundle_id or "\\" in model_bundle_id or ".." in model_bundle_id:
            raise ManagementError("MODEL_BUNDLE_INVALID", "不允许通过路径注册模型。")
        if not has_equipment(equipment_type):
            raise ManagementError("DATASET_NOT_FOUND", f"未注册设备类型：{equipment_type}", status_code=404)
        path = self.config.models_root / equipment_type / model_bundle_id
        connection = self.connection()
        try:
            row = register_bundle(
                connection,
                self.config,
                path,
                expected_equipment_type=equipment_type,
                force_status="candidate",
            )
            connection.commit()
            return row
        finally:
            connection.close()

    def list_models_api(
        self,
        *,
        equipment_type: str | None = None,
        status: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> dict[str, Any]:
        connection = self.connection()
        try:
            return list_models(
                connection,
                equipment_type=equipment_type,
                status=status,
                offset=offset,
                limit=limit,
            )
        finally:
            connection.close()

    def get_model_detail(self, model_bundle_id: str) -> dict[str, Any]:
        connection = self.connection()
        try:
            model = get_model(connection, model_bundle_id)
            integrity_ok = True
            integrity_error = None
            try:
                from l2.management.registry import validate_bundle_for_registry

                validated = validate_bundle_for_registry(
                    self.config,
                    Path(model["bundle_path"]),
                    expected_equipment_type=model["equipment_type"],
                )
                model["algorithms"] = {
                    "binary": "RandomForestClassifier",
                    "multiclass": "CatBoostClassifier",
                }
                model["integrity_check"] = {"ok": True, "verify_report": validated["verify_report"]}
            except ManagementError as exc:
                integrity_ok = False
                integrity_error = exc.message
                model["integrity_check"] = {"ok": False, "error": integrity_error}
            model["integrity_ok"] = integrity_ok
            return model
        finally:
            connection.close()

    def activate(self, model_bundle_id: str) -> dict[str, Any]:
        connection = self.connection()
        try:
            return activate_model(connection, self.config, model_bundle_id, manager=self.manager)
        finally:
            connection.close()

    def rollback(self, *, equipment_type: str, target_model_bundle_id: str) -> dict[str, Any]:
        connection = self.connection()
        try:
            return rollback_model(
                connection,
                self.config,
                equipment_type=equipment_type,
                target_model_bundle_id=target_model_bundle_id,
                manager=self.manager,
            )
        finally:
            connection.close()

    def active_status(self, equipment_type: str | None = "main_mill_motor") -> dict[str, Any]:
        if equipment_type is None or equipment_type == "" or equipment_type == "*":
            return self.active_status_all()
        connection = self.connection()
        try:
            active = get_active_model(connection, equipment_type)
        finally:
            connection.close()
        runtime_id = None
        model_loaded = self.manager.is_ready(equipment_type)
        if model_loaded:
            runtime_id = self.manager.require(equipment_type).bundle.manifest.get("model_bundle_id")
        active_id = None if active is None else active["model_bundle_id"]
        return {
            "equipment_type": equipment_type,
            "active_model_bundle_id": active_id,
            "runtime_model_bundle_id": runtime_id,
            "consistent": active_id is not None and active_id == runtime_id,
            "model_loaded": model_loaded,
            "activated_at": None if active is None else active.get("activated_at"),
            "compatibility_mode": active is None and model_loaded,
            "active_record": active,
        }

    def active_status_all(self) -> dict[str, Any]:
        connection = self.connection()
        try:
            items = []
            for spec in list_equipment_types():
                active = get_active_model(connection, spec.equipment_type)
                runtime_id = None
                loaded = self.manager.is_ready(spec.equipment_type)
                if loaded:
                    runtime_id = self.manager.require(spec.equipment_type).bundle.manifest.get(
                        "model_bundle_id"
                    )
                active_id = None if active is None else active["model_bundle_id"]
                items.append(
                    {
                        "equipment_type": spec.equipment_type,
                        "display_name": spec.display_name,
                        "active_model_bundle_id": active_id,
                        "runtime_model_bundle_id": runtime_id,
                        "consistent": active_id is not None and active_id == runtime_id,
                        "model_loaded": loaded,
                        "activated_at": None if active is None else active.get("activated_at"),
                    }
                )
            return {"equipment": items}
        finally:
            connection.close()

    def list_events_api(self, *, offset: int = 0, limit: int = 50) -> dict[str, Any]:
        connection = self.connection()
        try:
            return list_events(connection, offset=offset, limit=limit)
        finally:
            connection.close()

    def resolve_startup_bundle(self, equipment_type: str) -> dict[str, Any] | None:
        """启动时优先返回 Active；无 Active 时返回 None（由调用方使用环境兼容路径）。"""
        connection = self.connection()
        try:
            init_db(self.config.db_path)
            bootstrap_existing_candidates(connection, self.config, equipment_type=equipment_type)
            connection.commit()
            return get_active_model(connection, equipment_type)
        finally:
            connection.close()

    def resolve_all_startup_actives(self) -> list[dict[str, Any]]:
        """启动时登记各设备本地包并返回全部 Active 记录。"""
        connection = self.connection()
        try:
            init_db(self.config.db_path)
            for spec in list_equipment_types():
                bootstrap_existing_candidates(
                    connection,
                    self.config,
                    equipment_type=spec.equipment_type,
                )
            connection.commit()
            actives = []
            for spec in list_equipment_types():
                active = get_active_model(connection, spec.equipment_type)
                if active is not None:
                    actives.append(active)
            return actives
        finally:
            connection.close()


MANAGEMENT_SERVICE = ManagementService()
