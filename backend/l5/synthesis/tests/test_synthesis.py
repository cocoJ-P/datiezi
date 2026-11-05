"""L5-P3 综合对照与隔离测试。"""

from __future__ import annotations

import unittest

from l5.prediction.contract import FEATURE_WHITELIST, TARGET_COLUMNS
from l5.prediction.service import L5PredictionService
from l5.schema import empty_assessment
from l5.synthesis.comparison import run_deterministic_comparison
from l5.synthesis.mapping import normalize_equipment_category
from l5.synthesis.orchestrator import synthesize_record
from l5.synthesis.prediction_overlay import apply_supervised_predictions
from l5.synthesis.synthesis_agent import validate_synthesis_llm_output


class MappingTests(unittest.TestCase):
    def test_gearbox_category(self):
        self.assertEqual(normalize_equipment_category("主减速机_齿轮箱"), "主减速机/齿轮箱")
        self.assertEqual(normalize_equipment_category(None, "main_mill_motor"), "主轧机电机")


class OverlayTests(unittest.TestCase):
    def test_overlay_keeps_unconfirmed(self):
        a = empty_assessment(
            l5_run_id="L5R-x",
            record_id="R1",
            source_run_ids={},
            source_versions={},
            input_fingerprint="fp",
        )
        pred = {
            "schema": "L5PredictionAssessment",
            "predictions": {zh: ("正常" if zh != "诊断置信度" else 0.9) for zh in TARGET_COLUMNS},
            "prediction_status": {},
            "prediction_sources": {},
            "model_versions": {"model_id": "L5P-test"},
            "model_input_fingerprint": "abc",
            "equipment_category": "主轧机电机",
        }
        # 补齐非正常字段默认值
        pred["predictions"]["故障类型"] = "正常"
        pred["predictions"]["故障严重度"] = "1"
        pred["predictions"]["故障位置"] = "-"
        pred["predictions"]["频谱描述"] = "t"
        pred["predictions"]["根因分析"] = "r"
        pred["predictions"]["建议措施"] = "a"
        pred["predictions"]["优先级"] = "P3"
        pred["predictions"]["影响程度"] = "无影响"
        out = apply_supervised_predictions(a, pred)
        self.assertEqual(out["root_cause_confirmation"], "unconfirmed")
        self.assertEqual(out["fields"]["equipment_status"]["source_layer"], "l5_prediction")
        self.assertIn("upstream_aggregation", out["fields"]["equipment_status"]["extras"])


class ComparisonTests(unittest.TestCase):
    def test_l2_conflict_not_four_class(self):
        det = run_deterministic_comparison(
            prediction={"predictions": {"设备状态": "预警"}},
            l2_record={"result": {"decision": {"screening_result": "CONFLICT"}}},
        )
        status_row = next(r for r in det["field_comparisons"] if r["field"] == "设备状态")
        self.assertEqual(status_row["consistency"], "insufficient_evidence")

    def test_root_cause_does_not_confirm_l4(self):
        det = run_deterministic_comparison(
            prediction={"predictions": {"根因分析": "轴承磨损导致振动升高"}},
            l4_reasoning={"root_cause_confirmation": "unconfirmed", "ranked_candidates": [{"label": "其他"}]},
        )
        row = next(r for r in det["field_comparisons"] if r["field"] == "根因分析")
        self.assertEqual(row["l4_root_cause_confirmation"], "unconfirmed")
        # L5 模板 + L4 unconfirmed 且无反证 → 不得自动 conflicting
        self.assertEqual(row["consistency"], "insufficient_evidence")

    def test_root_cause_explicit_contradict_is_conflicting(self):
        det = run_deterministic_comparison(
            prediction={"predictions": {"根因分析": "轴承保持架断裂"}},
            l4_reasoning={
                "root_cause_confirmation": "unconfirmed",
                "candidate_reviews": [
                    {
                        "cause_name": "转子不平衡",
                        "contradicting_evidence": ["EV-x"],
                        "validity_status": "mechanism_only",
                    }
                ],
            },
        )
        row = next(r for r in det["field_comparisons"] if r["field"] == "根因分析")
        self.assertEqual(row["consistency"], "conflicting")


class SynthesisAgentValidationTests(unittest.TestCase):
    def test_reject_forged_refs(self):
        ok, why = validate_synthesis_llm_output(
            {
                "overall_consistency": "consistent",
                "evidence_references": ["FORGED-1"],
                "knowledge_references": [],
            }
        )
        self.assertFalse(ok)


class OrchestratorDegradeTests(unittest.TestCase):
    def test_unavailable_without_l3_l4(self):
        svc = L5PredictionService()
        # minimal fake prediction
        pred = {
            "predictions": {t: ("正常" if t != "诊断置信度" else 0.8) for t in TARGET_COLUMNS},
            "model_versions": {"model_id": svc.manifest.get("model_id")},
        }
        pred["predictions"].update(
            {
                "故障类型": "正常",
                "故障严重度": "1",
                "故障位置": "-",
                "频谱描述": "x",
                "根因分析": "y",
                "建议措施": "z",
                "优先级": "P3",
                "影响程度": "无影响",
            }
        )
        a = synthesize_record(
            record_id="R",
            synthesis_run_id="L5SYN-t",
            prediction=pred,
            allow_llm_synthesis=False,
        )
        self.assertEqual(a["roles"]["diagnosis_agent"]["status"], "unavailable")
        self.assertEqual(a["roles"]["causal_agent"]["status"], "unavailable")
        self.assertTrue(a["l5_predictions_unchanged"])
        self.assertFalse(a["roles"]["synthesis_agent"]["invoked"])


class FeatureIndexTests(unittest.TestCase):
    def test_observation_index_excludes_targets(self):
        from l5.synthesis.features import lookup_observation_features

        hit = lookup_observation_features("REC-DP-20260314-00162")
        self.assertIsNotNone(hit)
        self.assertEqual(len(hit["features"]), 19)
        for t in TARGET_COLUMNS:
            self.assertNotIn(t, hit["features"])
            self.assertNotIn(t, hit)
        self.assertFalse((hit.get("provenance") or {}).get("exposes_targets"))


class FeaturePerturbationTests(unittest.TestCase):
    def test_same_features_different_record_id_same_prediction(self):
        from l5.synthesis.features import lookup_observation_features
        from l5.synthesis.prediction_overlay import try_predict_for_context

        hit = lookup_observation_features("REC-DP-20260314-00162")
        self.assertIsNotNone(hit)
        feats = dict(hit["features"])
        cat = hit["equipment_category"]
        ctx_a = {
            "record_id": "FAKE-ID-A",
            "equipment_category": cat,
            "observation_features": feats,
        }
        ctx_b = {
            "record_id": "FAKE-ID-B",
            "equipment_category": cat,
            "observation_features": feats,
        }
        pa, ea = try_predict_for_context(ctx_a, allow_dataset_observation_backfill=False)
        pb, eb = try_predict_for_context(ctx_b, allow_dataset_observation_backfill=False)
        self.assertIsNone(ea)
        self.assertIsNone(eb)
        self.assertEqual(pa["predictions"], pb["predictions"])
        self.assertEqual(pa["model_input_fingerprint"], pb["model_input_fingerprint"])

    def test_same_record_id_different_obs_uses_live_not_history(self):
        from l5.synthesis.features import lookup_observation_features
        from l5.synthesis.prediction_overlay import try_predict_for_context

        hit = lookup_observation_features("REC-DP-20260314-00162")
        self.assertIsNotNone(hit)
        # 完整 live 扰动：不走回填，必须用本次输入
        live_full = dict(hit["features"])
        live_full["X向振动(mm/s)"] = float(live_full["X向振动(mm/s)"]) + 3.5
        ctx_live = {
            "record_id": "REC-DP-20260314-00162",
            "equipment_category": hit["equipment_category"],
            "observation_features": live_full,
        }
        pred_live, err_live = try_predict_for_context(ctx_live, allow_dataset_observation_backfill=True)
        self.assertIsNone(err_live)
        ctx_hist = {
            "record_id": "REC-DP-20260314-00162",
            "equipment_category": hit["equipment_category"],
            "observation_features": dict(hit["features"]),
        }
        pred_hist, _ = try_predict_for_context(ctx_hist, allow_dataset_observation_backfill=True)
        self.assertNotEqual(pred_live["model_input_fingerprint"], pred_hist["model_input_fingerprint"])

        # 部分 live 与历史共同特征冲突 → 拒绝静默合并
        partial = {
            "转速(rpm)": float(hit["features"]["转速(rpm)"]) + 100.0,
            "转频(Hz)": hit["features"]["转频(Hz)"],
        }
        ctx_partial = {
            "record_id": "REC-DP-20260314-00162",
            "equipment_category": hit["equipment_category"],
            "observation_features": partial,
        }
        pred_conflict, err = try_predict_for_context(ctx_partial, allow_dataset_observation_backfill=True)
        self.assertIsNone(pred_conflict)
        self.assertIn("feature_conflict", err or "")

    def test_missing_features_without_legal_source_fails(self):
        from l5.synthesis.prediction_overlay import try_predict_for_context

        ctx = {
            "record_id": "REC-DOES-NOT-EXIST-999",
            "equipment_category": "高压除鳞泵",
            "observation_features": {"转速(rpm)": 3000},
        }
        pred, err = try_predict_for_context(ctx, allow_dataset_observation_backfill=True)
        self.assertIsNone(pred)
        self.assertTrue(err)


class BundleLoadTests(unittest.TestCase):
    def test_sixty_heads_load(self):
        svc = L5PredictionService()
        self.assertEqual(len(svc.models), 60)
        self.assertEqual(len(FEATURE_WHITELIST), 19)


if __name__ == "__main__":
    unittest.main()
