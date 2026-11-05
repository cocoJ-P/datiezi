"""L5-P2 泄漏与契约测试。"""

from __future__ import annotations

import unittest
from pathlib import Path

from l5.prediction.contract import (
    EXPECTED_TRAIN_COLUMNS,
    FEATURE_WHITELIST,
    TARGET_COLUMNS,
    verify_columns,
)
from l5.prediction.dataset import load_test_tables, load_train_tables, project_features
from l5.prediction.paths import DEFAULT_BUNDLE, OFFICIAL_DEV
from l5.prediction.split import assert_no_target_in_features, time_split_equipment


class ColumnContractTests(unittest.TestCase):
    def test_train_is_33_not_34(self):
        loaded = load_train_tables()
        for stem, info in zip(
            [f["stem"] for f in loaded["meta"]["files"]],
            loaded["meta"]["files"],
        ):
            self.assertEqual(info["ncols"], 33, stem)
        self.assertEqual(len(EXPECTED_TRAIN_COLUMNS), 33)

    def test_official_34th_is_split_column(self):
        import pandas as pd

        if not OFFICIAL_DEV.is_file():
            self.skipTest("official missing")
        df = pd.read_excel(OFFICIAL_DEV, sheet_name=0)
        audit = verify_columns(list(map(str, df.columns)), mode="official")
        self.assertTrue(audit["has_dataset_split"])
        self.assertEqual(audit["n_columns"], 34)

    def test_feature_whitelist_excludes_targets_and_ids(self):
        feats = project_features(next(iter(load_train_tables()["tables"].values())))
        assert_no_target_in_features(list(feats.columns))
        self.assertNotIn("记录编号", feats.columns)
        self.assertEqual(len(FEATURE_WHITELIST), 19)


class SplitLeakageTests(unittest.TestCase):
    def test_time_split_no_id_overlap(self):
        loaded = load_train_tables()
        stem, df = next(iter(loaded["tables"].items()))
        split = time_split_equipment(
            df,
            equipment_type="test",
            equipment_category=str(df["设备类别"].iloc[0]),
            dataset_fingerprint=loaded["meta"]["dataset_fingerprint"],
        )
        m = split["manifest"]
        self.assertEqual(m["leakage_checks"]["record_id_overlap"], 0)
        self.assertTrue(m["leakage_checks"]["passed_obs_fingerprint"])
        self.assertEqual(len(set(m["train_record_ids"]) & set(m["valid_record_ids"])), 0)


class InferenceSmokeTests(unittest.TestCase):
    def test_bundle_predicts_without_answers(self):
        if not DEFAULT_BUNDLE.is_dir():
            self.skipTest("bundle not trained yet")
        from l5.prediction.service import L5PredictionService

        svc = L5PredictionService(DEFAULT_BUNDLE)
        test = load_test_tables()
        stem, df = next(iter(test["tables"].items()))
        row = df.iloc[0]
        feats = {c: row[c] for c in FEATURE_WHITELIST}
        # 故意塞入答案字段名，应被忽略
        feats_with_poison = dict(feats)
        feats_with_poison["设备状态"] = "危险"
        feats_with_poison["根因分析"] = "SHOULD_NOT_BE_USED"
        a = svc.predict_record(
            record_id=str(row["记录编号"]),
            equipment_category=str(row["设备类别"]),
            features=feats_with_poison,
        )
        self.assertEqual(set(a["predictions"].keys()), set(TARGET_COLUMNS))
        self.assertIn("predictions", a)


if __name__ == "__main__":
    unittest.main()
