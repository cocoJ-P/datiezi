"""模型激活与回滚（完整模型包原子切换）。"""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from l2.inference.config import L2InferenceConfig, load_config
from l2.inference.model_manager import MODEL_MANAGER, LoadedModelState, ModelManager
from l2.management.audit import append_event
from l2.management.config import ManagementConfig
from l2.management.errors import ManagementError
from l2.management.registry import get_active_model, get_model, validate_bundle_for_registry


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def activate_model(
    connection: sqlite3.Connection,
    config: ManagementConfig,
    model_bundle_id: str,
    *,
    manager: ModelManager | None = None,
    event_type: str = "model_activated",
    load_config_fn: Callable[[], L2InferenceConfig] | None = None,
) -> dict[str, Any]:
    model_manager = manager or MODEL_MANAGER
    cfg_loader = load_config_fn or load_config
    target = get_model(connection, model_bundle_id)
    equipment_type = target["equipment_type"]
    if target["status"] not in {"candidate", "archived"}:
        if target["status"] == "active":
            raise ManagementError(
                "MODEL_ALREADY_ACTIVE",
                f"模型 {model_bundle_id} 已经是 Active。",
            )
        raise ManagementError(
            "MODEL_ACTIVATION_FAILED",
            f"模型状态 {target['status']} 不可激活。",
        )

    validated = validate_bundle_for_registry(
        config,
        Path(target["bundle_path"]),
        expected_equipment_type=equipment_type,
    )
    base_cfg = cfg_loader()
    new_cfg = replace(
        base_cfg,
        equipment_type=equipment_type,
        model_bundle_id=model_bundle_id,
        model_bundle_path=Path(validated["bundle_path"]),
    )
    previous = get_active_model(connection, equipment_type)
    previous_id = None if previous is None else previous["model_bundle_id"]

    # 先预加载，失败则不改 DB / 不改内存
    try:
        preload = model_manager.preload(new_cfg)
    except Exception as exc:  # noqa: BLE001
        append_event(
            connection,
            event_type="model_activation_failed",
            status="failed",
            message=f"预加载失败：{exc}",
            equipment_type=equipment_type,
            model_bundle_id=model_bundle_id,
            previous_model_bundle_id=previous_id,
        )
        connection.commit()
        raise ManagementError(
            "MODEL_ACTIVATION_FAILED",
            f"模型预加载失败：{exc}",
            status_code=400,
            details={"model_bundle_id": model_bundle_id},
        ) from exc

    activated_at = _now()
    # 结束可能存在的隐式事务，再以 IMMEDIATE 锁开启写事务
    connection.commit()
    connection.execute("BEGIN IMMEDIATE")
    try:
        # 再次确认目标仍可激活，避免并发冲突
        current_target = get_model(connection, model_bundle_id)
        if current_target["status"] not in {"candidate", "archived"}:
            raise ManagementError(
                "MODEL_VERSION_CONFLICT",
                f"模型状态已变化为 {current_target['status']}。",
            )
        current_active = get_active_model(connection, equipment_type)
        current_active_id = None if current_active is None else current_active["model_bundle_id"]
        if current_active_id != previous_id:
            raise ManagementError(
                "MODEL_VERSION_CONFLICT",
                "Active 模型在激活过程中发生变化。",
                details={"expected": previous_id, "actual": current_active_id},
            )

        if current_active_id is not None and current_active_id != model_bundle_id:
            connection.execute(
                """
                UPDATE model_registry
                SET status = 'archived'
                WHERE model_bundle_id = ? AND status = 'active'
                """,
                (current_active_id,),
            )
        connection.execute(
            """
            UPDATE model_registry
            SET status = 'active', activated_at = ?
            WHERE model_bundle_id = ?
            """,
            (activated_at, model_bundle_id),
        )
        # 同一临界区交换内存引用；DB 提交失败则回滚内存（仅本设备）
        previous_state = model_manager.swap_preloaded(preload)
        try:
            connection.commit()
        except Exception:
            model_manager.restore_state(previous_state, equipment_type=equipment_type)
            raise
    except Exception as exc:
        connection.rollback()
        append_event(
            connection,
            event_type="model_activation_failed",
            status="failed",
            message=f"激活事务失败：{exc}",
            equipment_type=equipment_type,
            model_bundle_id=model_bundle_id,
            previous_model_bundle_id=previous_id,
        )
        connection.commit()
        if isinstance(exc, ManagementError):
            raise
        raise ManagementError(
            "MODEL_ACTIVATION_FAILED",
            f"激活失败：{exc}",
            status_code=500,
        ) from exc

    append_event(
        connection,
        event_type=event_type,
        status="ok",
        message=f"激活模型 {model_bundle_id}" + (f"（原 Active={previous_id}）" if previous_id else ""),
        equipment_type=equipment_type,
        model_bundle_id=model_bundle_id,
        previous_model_bundle_id=previous_id,
    )
    connection.commit()

    runtime_id = None
    if model_manager.is_ready(equipment_type):
        runtime_id = model_manager.require(equipment_type).bundle.manifest.get("model_bundle_id")

    return {
        "activated_model_bundle_id": model_bundle_id,
        "previous_model_bundle_id": previous_id,
        "equipment_type": equipment_type,
        "status": "active",
        "activated_at": activated_at,
        "runtime_model_bundle_id": runtime_id,
        "runtime_matches_active": runtime_id == model_bundle_id,
    }


def rollback_model(
    connection: sqlite3.Connection,
    config: ManagementConfig,
    *,
    equipment_type: str,
    target_model_bundle_id: str,
    manager: ModelManager | None = None,
) -> dict[str, Any]:
    target = get_model(connection, target_model_bundle_id)
    if target["equipment_type"] != equipment_type:
        raise ManagementError(
            "MODEL_ACTIVATION_FAILED",
            "回滚目标设备类型与请求不一致。",
            details={
                "requested": equipment_type,
                "target": target["equipment_type"],
            },
        )
    if target["status"] != "archived":
        raise ManagementError(
            "MODEL_ACTIVATION_FAILED",
            f"仅允许回滚到 archived 模型，当前状态为 {target['status']}。",
        )
    result = activate_model(
        connection,
        config,
        target_model_bundle_id,
        manager=manager,
        event_type="model_rollback",
    )
    return result
