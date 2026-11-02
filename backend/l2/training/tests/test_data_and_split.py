from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from l2.training.data_adapter import load_csv_snapshot
from l2.training.dataset import audit_training_frame, compare_status_and_fault, make_splits
from l2.training.schema import FEATURE_COLUMNS, assert_no_leakage, fault_to_binary, status_to_binary

ROOT = Path(__file__).resolve().parents[4]
DEV = ROOT / "data" / "l2_dev" / "main_mill_motor"


def test_assert_no_leakage():
    with pytest.raises(ValueError):
        assert_no_leakage(["转速(rpm)", "故障类型"])


def test_duplicate_record_id_raises(tmp_path: Path):
    features = pd.read_csv(DEV / "features.csv", encoding="utf-8-sig")
    labels = pd.read_csv(DEV / "labels.csv", encoding="utf-8-sig")
    features = pd.concat([features, features.iloc[[0]]], ignore_index=True)
    f = tmp_path / "features.csv"
    l = tmp_path / "labels.csv"
    features.to_csv(f, index=False, encoding="utf-8-sig")
    labels.to_csv(l, index=False, encoding="utf-8-sig")
    with pytest.raises(ValueError, match="重复"):
        load_csv_snapshot(f, l)


def test_missing_join_raises(tmp_path: Path):
    features = pd.read_csv(DEV / "features.csv", encoding="utf-8-sig").iloc[:10]
    labels = pd.read_csv(DEV / "labels.csv", encoding="utf-8-sig").iloc[:9]
    f = tmp_path / "features.csv"
    l = tmp_path / "labels.csv"
    features.to_csv(f, index=False, encoding="utf-8-sig")
    labels.to_csv(l, index=False, encoding="utf-8-sig")
    with pytest.raises(ValueError, match="一一对应"):
        load_csv_snapshot(f, l)


def test_missing_feature_raises(tmp_path: Path):
    features = pd.read_csv(DEV / "features.csv", encoding="utf-8-sig").drop(columns=[FEATURE_COLUMNS[0]])
    labels = pd.read_csv(DEV / "labels.csv", encoding="utf-8-sig")
    f = tmp_path / "features.csv"
    l = tmp_path / "labels.csv"
    features.to_csv(f, index=False, encoding="utf-8-sig")
    labels.to_csv(l, index=False, encoding="utf-8-sig")
    with pytest.raises(ValueError, match="缺少必要列"):
        load_csv_snapshot(f, l)


def test_illegal_label_raises():
    snapshot = load_csv_snapshot(DEV / "features.csv", DEV / "labels.csv")
    frame = snapshot.frame.copy()
    frame.loc[0, "故障类型"] = "未知故障"
    with pytest.raises(ValueError, match="非法故障类型"):
        audit_training_frame(frame)


def test_split_no_overlap():
    snapshot = load_csv_snapshot(DEV / "features.csv", DEV / "labels.csv")
    audit_training_frame(snapshot.frame)
    split = make_splits(snapshot.frame, random_seed=42)
    train, calib, test = set(split.train_ids), set(split.calib_ids), set(split.test_ids)
    assert not (train & calib)
    assert not (train & test)
    assert not (calib & test)
    assert len(train) + len(calib) + len(test) == len(snapshot.frame)


def test_status_fault_four_cases_unit():
    status = pd.Series(["正常", "预警", "报警", "正常"])
    fault = pd.Series(["正常", "轴承磨损", "正常", "润滑故障"])
    result = compare_status_and_fault(status, fault)
    assert result.both_normal == 1
    assert result.both_abnormal == 1
    assert result.status_abnormal_fault_normal == 1
    assert result.status_normal_fault_abnormal == 1
    assert result.semantic_conflict_count == 2
    # 首条「正常/正常」字面相同；其余 3 条字面不同，但语义冲突仅 2 条
    assert result.raw_value_different_count == 3
    assert result.raw_value_different_count != result.semantic_conflict_count


def test_status_to_binary_mapping():
    assert status_to_binary("正常") == "正常"
    assert status_to_binary("预警") == "非正常"
    assert status_to_binary("报警") == "非正常"
    assert status_to_binary("危险") == "非正常"
    assert fault_to_binary("正常") == "正常"
    assert fault_to_binary("轴承磨损") == "非正常"


def test_main_mill_motor_status_fault_audit_counts():
    snapshot = load_csv_snapshot(DEV / "features.csv", DEV / "labels.csv")
    audit = audit_training_frame(snapshot.frame)
    assert audit.status_fault_audit is not None
    detail = audit.status_fault_audit
    assert detail.both_normal == 1713
    assert detail.both_abnormal == 1835
    assert detail.status_abnormal_fault_normal == 315
    assert detail.status_normal_fault_abnormal == 137
    assert detail.semantic_conflict_count == 452
    assert detail.raw_value_different_count == 2287
    assert audit.status_mismatch_count == 452
    assert any("语义冲突：452" in item for item in audit.warnings)
    assert any("原始字段值不同：2287" in item for item in audit.warnings)
