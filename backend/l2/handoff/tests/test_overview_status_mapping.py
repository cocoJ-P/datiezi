"""总览 L2 主状态映射（后端状态 → 用户文案语义）契约。"""

from __future__ import annotations


def overview_main_status(l2_status: str | None, *, l1_ok: bool) -> str:
    if not l1_ok:
        return "未开始"
    if not l2_status or l2_status == "waiting_for_l1":
        return "未开始"
    if l2_status == "completed":
        return "已完成"
    if l2_status == "failed":
        return "执行失败"
    if l2_status == "training_confirmation_required":
        return "待训练"
    if l2_status in {
        "equipment_config_required",
        "dataset_not_ready",
        "model_activation_required",
        "skipped",
    }:
        return "需要处理"
    return "进行中"


def overview_tone(main: str) -> str:
    return {
        "已完成": "green",
        "进行中": "blue",
        "待训练": "orange",
        "需要处理": "orange",
        "执行失败": "red",
        "未开始": "gray",
    }[main]


def test_completed_only_when_diagnosis_done():
    assert overview_main_status("completed", l1_ok=True) == "已完成"
    assert overview_tone("已完成") == "green"
    # PED 发布中 / 匹配中不能变绿
    assert overview_main_status("preparing_dataset", l1_ok=True) == "进行中"
    assert overview_main_status("matching_model", l1_ok=True) == "进行中"
    assert overview_main_status("inference_running", l1_ok=True) == "进行中"


def test_no_active_is_orange_train():
    assert overview_main_status("training_confirmation_required", l1_ok=True) == "待训练"
    assert overview_tone("待训练") == "orange"


def test_blocked_is_orange_or_red():
    assert overview_main_status("equipment_config_required", l1_ok=True) == "需要处理"
    assert overview_main_status("dataset_not_ready", l1_ok=True) == "需要处理"
    assert overview_main_status("failed", l1_ok=True) == "执行失败"
    assert overview_tone("执行失败") == "red"


def test_waiting_l1_is_gray():
    assert overview_main_status(None, l1_ok=False) == "未开始"
    assert overview_main_status("waiting_for_l1", l1_ok=True) == "未开始"
    assert overview_tone("未开始") == "gray"
