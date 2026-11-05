"""L5a 单元与契约测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from l5.aggregator import build_final_diagnosis
from l5.recommendations import build_verification_recommendations
from l5.schema import BUSINESS_FIELD_ORDER


def _fake_context(**over: object) -> dict:
    base = {
        "schema": "L5InputContext",
        "record_id": "REC-T-001",
        "equipment_id": "MM-01",
        "equipment_type": "main_mill_motor",
        "source_run_ids": {
            "diagnosis_run_id": "DR-1",
            "analysis_run_id": "AR-1",
            "l4_run_id": "L4R-1",
            "l3_reasoning_run_id": None,
            "l4_reasoning_run_id": None,
            "enrichment_run_id": None,
        },
        "source_versions": {"model_bundle_id": "MB-1", "prepared_dataset_id": "PED-1"},
        "source_availability": {
            "l2": True,
            "l3_core": True,
            "l3_reasoning": False,
            "l3_knowledge": False,
            "l4_core": True,
            "l4_reasoning": False,
        },
        "input_fingerprint": "fp",
        "limitations": ["l3_reasoning_not_bound", "l4_reasoning_not_bound"],
        "l2_record": {
            "screening_result": "ABNORMAL",
            "result": {
                "decision": {"screening_result": "ABNORMAL", "preliminary_fault": "轴承磨损"},
                "binary_classifier": {"probabilities": {"abnormal": 0.9}},
                "multiclass_classifier": {"probabilities": {"轴承磨损": 0.7}},
                "consistency": {"probability_gap": 0.1},
            },
        },
        "evidence_bundle": {
            "schema": "L3EvidenceBundle",
            "fault_type_review": [
                {"fault_mode": "轴承磨损", "verdict": "SUPPORTED", "support_count": 2, "contradict_count": 0}
            ],
            "severity_assessment": {"severity_level": None, "notes": ["振动越限征兆"]},
            "location_candidates": {"candidates": []},
            "spectral_feature_assessment": {
                "summary": "BPFO 标量升高",
                "unverified": ["完整频谱", "相位"],
            },
            "evidence_sufficiency": "partial",
            "evidence_items": [
                {
                    "evidence_id": "mech:REC-T-001:bearing:BPFO幅值(g)",
                    "feature_name": "BPFO幅值(g)",
                    "observed_value": 0.4,
                    "assessment": "SUPPORT",
                }
            ],
            "missing_evidence": ["相位测量", "missing_feature:oil_water_content"],
        },
        "root_cause_assessment": {
            "root_cause_confirmation": "unconfirmed",
            "root_cause_candidates": [
                {
                    "candidate_id": "RC-1",
                    "cause_name": "润滑不足",
                    "related_fault_type": "轴承磨损",
                    "causal_knowledge_id": "CK-lube-film-loss-bearing",
                    "causal_path_id": "CP-1",
                    "supporting_evidence_ids": [],
                    "fault_manifestation_evidence_ids": ["mech:REC-T-001:bearing:BPFO幅值(g)"],
                    "missing_evidence": ["missing_feature:oil_water_content", "油品检测"],
                },
                {
                    "candidate_id": "RC-2",
                    "cause_name": "接触疲劳",
                    "related_fault_type": "轴承磨损",
                    "causal_knowledge_id": "CK-bearing-contact-fatigue",
                    "same_layer_as_resulting_fault": True,
                    "supporting_evidence_ids": [],
                    "fault_manifestation_evidence_ids": ["mech:REC-T-001:bearing:BPFO幅值(g)"],
                    "missing_evidence": [],
                },
            ],
            "impact_assessment": {
                "propagation_status": "unknown",
                "observed_impact": {"local": True},
                "potential_impact": {"reference_topology_only": True},
                "upstream_dependencies": [],
                "downstream_connections": [{"graph_label": "齿轮箱"}],
                "equipment_mapping": {"mapping_level": "equipment_type"},
            },
        },
        "causal_reasoning_assessment": {},
        "l3_reasoning_assessment": {},
    }
    base.update(over)
    return base


class AggregatorTests(unittest.TestCase):
    def test_ten_fields_and_nulls(self):
        assessment = build_final_diagnosis(_fake_context(), l5_run_id="L5R-t")
        self.assertEqual(list(assessment["fields"].keys()), BUSINESS_FIELD_ORDER)
        self.assertIsNone(assessment["fields"]["fault_severity"]["value"])
        self.assertEqual(assessment["fields"]["fault_severity"]["null_reason"], "no_validated_severity_scale")
        self.assertIsNone(assessment["fields"]["priority"]["value"])
        self.assertIsNone(assessment["fields"]["impact_degree"]["value"])
        self.assertIsNone(assessment["fields"]["diagnosis_confidence"]["value"])
        self.assertEqual(assessment["root_cause_confirmation"], "unconfirmed")
        self.assertEqual(assessment["fields"]["equipment_status"]["value"], "ABNORMAL")
        self.assertEqual(assessment["review_status"], "draft")
        self.assertNotIn("risk_level", assessment["fields"])

    def test_conflict_not_coerced(self):
        ctx = _fake_context()
        ctx["l2_record"]["result"]["decision"]["screening_result"] = "CONFLICT"
        ctx["l2_record"]["screening_result"] = "CONFLICT"
        assessment = build_final_diagnosis(ctx, l5_run_id="L5R-c")
        self.assertEqual(assessment["fields"]["equipment_status"]["value"], "CONFLICT")
        self.assertIn("待确认", assessment["fields"]["equipment_status"]["display"] or "")

    def test_verification_dedupe(self):
        recs = build_verification_recommendations(_fake_context())
        self.assertTrue(recs)
        self.assertTrue(all(r["recommendation_type"] == "verification" for r in recs))
        self.assertTrue(all(r["requires_human_review"] for r in recs))
        # oil related should merge
        oil = [r for r in recs if "oil" in r["condition_id"] or "oil" in r["description"]]
        self.assertTrue(oil)


class InputBindingTests(unittest.TestCase):
    def test_reject_without_source_run(self):
        from l5.input_adapter import L5InputError, build_l5_input_context

        with self.assertRaises(L5InputError):
            build_l5_input_context(record_id="REC-X")

    def test_mismatch_l4_analysis(self):
        from l5.input_adapter import L5InputError, build_l5_input_context

        with mock.patch("l5.input_adapter.L4_STORE") as store:
            store.get.return_value = {"source_analysis_run_id": "AR-OTHER", "l4_run_id": "L4R-1"}
            with mock.patch("l5.input_adapter.ANALYSIS_STORE") as astore:
                astore.get_run.return_value = {
                    "source_diagnosis_run_id": "DR-1",
                    "model_bundle_id": "MB",
                    "prepared_dataset_id": "PED",
                    "equipment_type": "main_mill_motor",
                }
                astore.get_record.return_value = {"evidence": {"schema": "L3EvidenceBundle"}}
                with mock.patch("l5.input_adapter.HANDOFF_STORE") as h:
                    h.get_diagnosis_run.return_value = {
                        "model_bundle_id": "MB",
                        "prepared_dataset_id": "PED",
                        "equipment_type": "main_mill_motor",
                    }
                    with mock.patch("l5.input_adapter._load_l2_record", return_value={"result": {}, "screening_result": "ABNORMAL"}):
                        with self.assertRaises(L5InputError) as cm:
                            build_l5_input_context(
                                record_id="REC-1",
                                diagnosis_run_id="DR-1",
                                analysis_run_id="AR-1",
                                l4_run_id="L4R-1",
                            )
                        self.assertEqual(cm.exception.code, "l4_analysis_mismatch")


class StoreIdempotencyTests(unittest.TestCase):
    def test_content_key_reuse(self):
        from l5.store import L5Store

        with tempfile.TemporaryDirectory() as td:
            store = L5Store(Path(td) / "l5.sqlite3")
            meta = {
                "diagnosis_run_id": "DR",
                "analysis_run_id": "AR",
                "l4_run_id": "L4",
                "aggregation_schema_version": "v",
                "recommendation_rule_version": "r",
                "selected_record_count": 1,
                "selection": {"record_ids": ["R1"]},
            }
            a, created1 = store.create_or_get(content_key="k1", meta=meta)
            b, created2 = store.create_or_get(content_key="k1", meta=meta)
            self.assertTrue(created1)
            self.assertFalse(created2)
            self.assertEqual(a["l5_run_id"], b["l5_run_id"])


if __name__ == "__main__":
    unittest.main()
