"""数据集契约、特征白名单与列核验。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

# 标识列：仅划分/追溯，默认不进入模型
ID_COLUMNS = ["记录编号", "设备编号", "设备类别", "采集时间"]

# 显式观测特征白名单（19）
FEATURE_WHITELIST = [
    "转速(rpm)",
    "转频(Hz)",
    "电机电流(A)",
    "X向振动(mm/s)",
    "Y向振动(mm/s)",
    "最大振动(mm/s)",
    "振动均方根",
    "驱动端轴承温度(°C)",
    "非驱动端轴承温度(°C)",
    "包络谱峰值(g)",
    "BPFO幅值(g)",
    "BPFI幅值(g)",
    "BSF幅值(g)",
    "啮合频率幅值(g)",
    "1X转频幅值(mm/s)",
    "BPFO频率(Hz)",
    "BPFI频率(Hz)",
    "BSF频率(Hz)",
    "啮合频率(Hz)",
]

# 条件特征：允许进入模型
CONDITION_FEATURES = ["设备类别"]

# 十字段目标（10）
TARGET_COLUMNS = [
    "设备状态",
    "故障类型",
    "故障严重度",
    "故障位置",
    "诊断置信度",
    "频谱描述",
    "根因分析",
    "建议措施",
    "优先级",
    "影响程度",
]

# 官方开发集独有；训练集单文件无此列；永不进入特征
KNOWN_NON_FEATURE_EXTRA = ["数据集划分"]

# 训练单表期望列（33）= 4 标识 + 19 观测 + 10 目标
EXPECTED_TRAIN_COLUMNS = ID_COLUMNS + FEATURE_WHITELIST + TARGET_COLUMNS
EXPECTED_TEST_COLUMNS = ID_COLUMNS + FEATURE_WHITELIST

# 有序等级约定（仅评估/报告；不改变标签字符串）
STATUS_ORDER = ["正常", "预警", "报警", "危险"]
SEVERITY_ORDER = ["1", "2", "3", "4", "5"]
PRIORITY_ORDER = ["P3", "P2", "P1", "P0"]  # 低→高紧急：P3 最低，P0 最高

TEMPLATE_TARGETS = ["频谱描述", "根因分析", "建议措施", "影响程度"]
CLASSIFICATION_TARGETS = [
    "设备状态",
    "故障类型",
    "故障严重度",
    "故障位置",
    "频谱描述",
    "根因分析",
    "建议措施",
    "优先级",
    "影响程度",
]
REGRESSION_TARGETS = ["诊断置信度"]

EQUIPMENT_FILE_STEMS = [
    "主轧机电机",
    "主减速机_齿轮箱",
    "冷却水循环泵",
    "加热炉助燃风机",
    "高压除鳞泵",
    "连铸机扇形段驱动电机",
]

SHEET_TO_EQUIPMENT_TYPE = {
    "主轧机电机": "main_mill_motor",
    "主减速机_齿轮箱": "gearbox",
    "冷却水循环泵": "cooling_water_circulation_pump",
    "加热炉助燃风机": "furnace_combustion_fan",
    "高压除鳞泵": "high_pressure_descaling_pump",
    "连铸机扇形段驱动电机": "caster_segment_drive_motor",
}


@dataclass
class DatasetContract:
    equipment_type: str
    equipment_category: str
    record_id: str
    equipment_id: str
    collection_time: str
    feature_names: list[str]
    feature_values: dict[str, Any]
    target_names: list[str] | None = None
    targets: dict[str, Any] | None = None
    dataset_fingerprint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def verify_columns(columns: list[str], *, mode: str) -> dict[str, Any]:
    """mode: train | test | official."""
    cols = list(columns)
    unexpected = [c for c in cols if c not in EXPECTED_TRAIN_COLUMNS + KNOWN_NON_FEATURE_EXTRA]
    missing_id = [c for c in ID_COLUMNS if c not in cols]
    missing_feat = [c for c in FEATURE_WHITELIST if c not in cols]
    result: dict[str, Any] = {
        "mode": mode,
        "n_columns": len(cols),
        "columns": cols,
        "missing_id": missing_id,
        "missing_features": missing_feat,
        "unexpected": unexpected,
        "has_targets": {t: (t in cols) for t in TARGET_COLUMNS},
        "has_dataset_split": "数据集划分" in cols,
    }
    if mode == "train":
        # 训练单文件应为 33 列；允许官方 34 列含 数据集划分但不进入特征
        missing_tgt = [t for t in TARGET_COLUMNS if t not in cols]
        result["missing_targets"] = missing_tgt
        if missing_id or missing_feat or missing_tgt:
            raise ValueError(f"train_schema_invalid:{result}")
        if unexpected:
            raise ValueError(f"train_unexpected_columns:{unexpected}")
        if len(cols) not in (33, 34):
            raise ValueError(f"train_column_count_unexpected:{len(cols)}")
        if len(cols) == 34 and "数据集划分" not in cols:
            raise ValueError("train_34_cols_but_missing_数据集划分")
        if len(cols) == 33 and "数据集划分" in cols:
            raise ValueError("inconsistent_split_column")
    elif mode == "test":
        if missing_id or missing_feat:
            raise ValueError(f"test_schema_invalid:{result}")
        # 测试不得依赖目标列；若存在则必须被投影丢弃
        extra = [c for c in cols if c not in EXPECTED_TEST_COLUMNS and c not in KNOWN_NON_FEATURE_EXTRA]
        # 允许误带目标列，但不计入特征
        result["target_columns_present_will_be_ignored"] = [t for t in TARGET_COLUMNS if t in cols]
        bad = [c for c in extra if c not in TARGET_COLUMNS]
        if bad:
            raise ValueError(f"test_unexpected_columns:{bad}")
        if len([c for c in cols if c in EXPECTED_TEST_COLUMNS]) != 23:
            # 至少应具备 23 列核心
            if len(cols) < 23:
                raise ValueError(f"test_too_few_columns:{len(cols)}")
    elif mode == "official":
        if "数据集划分" not in cols:
            raise ValueError("official_missing_数据集划分")
        if len(cols) != 34:
            raise ValueError(f"official_column_count:{len(cols)}")
    return result


def model_feature_names() -> list[str]:
    """实际进入模型的特征名：19 观测 + 设备类别。"""
    return FEATURE_WHITELIST + CONDITION_FEATURES
