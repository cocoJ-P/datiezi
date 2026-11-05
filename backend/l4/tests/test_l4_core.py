"""L4 Core v1 单元与真实库只读验收。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from l4.candidate_builder import build_root_cause_candidates
from l4.causal_knowledge.loader import get_catalog
from l4.causal_trace import build_causal_trace
from l4.engine import analyze_record
from l4.impact_analyzer import analyze_impact
from l4.jobs import content_key_for, run_l4_for_analysis
from l4.mapping import resolve_equipment_mapping
from l4.path_validator import validate_candidates
from l4.schema import ROOT_CAUSE_CONFIRMATION, is_anomaly_only_evidence
from l4.store import L4Store
from l3.runtime.store import ANALYSIS_STORE


FORBIDDEN_ANSWER_KEYS = {"根因分析", "影响程度", "建议措施", "优先级"}


class CausalKnowledgeTests(unittest.TestCase):
    def test_catalog_has_sources_and_candidate_status(self):
        catalog = get_catalog()
        entries = catalog.get("entries") or []
        self.assertGreaterEqual(len(entries), 4)
        for entry in entries:
            self.assertEqual(entry.get("validation_status"), "candidate_mechanism")
            self.assertTrue(entry.get("source_document_locations"))
            self.assertNotEqual(entry.get("cause"), entry.get("mechanism"))
            self.assertNotEqual(entry.get("cause"), entry.get("resulting_fault"))

    def test_no_applicable_knowledge_returns_empty(self):
        ctx = {
            "record_id": "REC-X",
            "equipment_type": "main_mill_motor",
            "l3_fault_candidates": [
                {
                    "fault_type": "不存在的故障XYZ",
                    "source": "l3_fault_type_review",
                    "verdict": "SUPPORTED",
                }
            ],
        }
        built = build_root_cause_candidates(ctx)
        self.assertEqual(built["root_cause_candidates"], [])
        self.assertEqual(built["empty_reason"], "no_applicable_causal_knowledge")


class MappingAndImpactTests(unittest.TestCase):
    def test_mm01_not_exact_asset(self):
        mapping = resolve_equipment_mapping(equipment_id="MM-01", equipment_type="main_mill_motor")
        self.assertNotEqual(mapping["mapping_level"], "exact_asset")
        self.assertIn(mapping["mapping_level"], {"equipment_type", "unresolved"})

    def test_descaling_pump_unresolved_no_downstream(self):
        ctx = {
            "equipment_id": "DP-01",
            "equipment_type": "high_pressure_descaling_pump",
            "l2_context": {"screening_result": "UNCERTAIN"},
            "evidence_bundle": {"support_evidence": [], "evidence_sufficiency": "insufficient"},
        }
        impact = analyze_impact(ctx)
        self.assertEqual(impact["propagation_status"], "unknown")
        self.assertIsNone(impact.get("impact_severity_level"))
        self.assertEqual(impact["potential_impact"].get("status"), "unavailable")
        self.assertEqual(impact["related_equipment"], [])

    def test_type_mapping_potential_only(self):
        ctx = {
            "equipment_id": "MM-01",
            "equipment_type": "main_mill_motor",
            "l2_context": {"screening_result": "ABNORMAL", "preliminary_fault": "轴承磨损"},
            "evidence_bundle": {
                "support_evidence": [{"evidence_id": "thr:x", "feature_name": "X向振动(mm/s)"}],
                "evidence_sufficiency": "limited",
            },
        }
        impact = analyze_impact(ctx)
        self.assertEqual(impact["propagation_status"], "unknown")
        self.assertTrue(impact["potential_impact"].get("reference_topology_only"))
        self.assertIsNone(impact.get("impact_severity_level"))


class ValidatorTests(unittest.TestCase):
    def test_anomaly_only_not_confirmed_root_cause(self):
        self.assertTrue(is_anomaly_only_evidence("thr:REC:mill_vib_x_alarm", "X向振动"))
        ctx = {
            "record_id": "REC-T",
            "equipment_type": "main_mill_motor",
            "support_evidence": [
                {
                    "evidence_id": "thr:REC:mill_vib_x_alarm",
                    "feature_name": "X向振动(mm/s)",
                    "assessment": "SUPPORT",
                    "analyzer_id": "threshold",
                    "observed_value": 4.0,
                }
            ],
            "evidence_bundle": {
                "evidence_items": [
                    {
                        "evidence_id": "thr:REC:mill_vib_x_alarm",
                        "feature_name": "X向振动(mm/s)",
                        "assessment": "SUPPORT",
                        "analyzer_id": "threshold",
                        "observed_value": 4.0,
                    }
                ]
            },
            "l3_fault_candidates": [
                {"fault_type": "轴承磨损", "source": "l3_fault_type_review", "verdict": "SUPPORTED"}
            ],
        }
        built = build_root_cause_candidates(ctx)
        validated = validate_candidates(
            ctx,
            candidates=built["root_cause_candidates"],
            paths=built["causal_paths"],
        )
        for cand in validated["root_cause_candidates"]:
            self.assertEqual(cand["assessment_status"], ROOT_CAUSE_CONFIRMATION)
            # 一般越限不得进入 supporting 作为特异支持
            for eid in cand.get("supporting_evidence_ids") or []:
                self.assertFalse(is_anomaly_only_evidence(eid))


class TraceTests(unittest.TestCase):
    def test_trace_acyclic_and_refs(self):
        paths = [
            {
                "causal_path_id": "CP-test",
                "cause": "润滑不足",
                "mechanism": "油膜失效",
                "resulting_fault": "轴承磨损",
                "related_fault_type": "轴承磨损",
                "causal_knowledge_id": "CK-x",
                "source_knowledge_ids": ["K-1"],
                "supporting_evidence_ids": ["ev-1"],
                "contradicting_evidence_ids": [],
                "link_states": {"cause": "hypothesized", "mechanism": "knowledge_supported", "fault_to_evidence": "unknown"},
            }
        ]
        trace = build_causal_trace(candidates=[{"candidate_id": "c1"}], paths=paths, impact={"topology_paths": []}, evidence_refs=[])
        self.assertTrue(trace["acyclic"])
        types = {n["node_type"] for n in trace["nodes"]}
        self.assertIn("ROOT_CAUSE_CANDIDATE", types)
        self.assertIn("EVIDENCE_REFERENCE", types)


class IdempotentRunTests(unittest.TestCase):
    def test_content_key_stable_and_store_idempotent(self):
        key1 = content_key_for("AR-1", ["R1", "R2"], causal_version="v1", mapping_version_str="m1")
        key2 = content_key_for("AR-1", ["R2", "R1"], causal_version="v1", mapping_version_str="m1")
        self.assertEqual(key1, key2)
        with tempfile.TemporaryDirectory() as tmp:
            store = L4Store(Path(tmp) / "l4.sqlite3")
            run_a, created_a = store.create_or_get(
                content_key=key1,
                meta={
                    "source_analysis_run_id": "AR-1",
                    "causal_knowledge_version": "v1",
                    "equipment_mapping_version": "m1",
                    "selected_record_count": 2,
                    "selection": {"record_ids": ["R1", "R2"]},
                },
            )
            run_b, created_b = store.create_or_get(
                content_key=key1,
                meta={
                    "source_analysis_run_id": "AR-1",
                    "causal_knowledge_version": "v1",
                    "equipment_mapping_version": "m1",
                    "selected_record_count": 2,
                },
            )
            self.assertTrue(created_a)
            self.assertFalse(created_b)
            self.assertEqual(run_a["l4_run_id"], run_b["l4_run_id"])


@unittest.skipUnless(
    Path(__file__).resolve().parents[2].joinpath("data", "l3_analysis.sqlite3").exists()
    or Path(__file__).resolve().parents[3].joinpath("backend", "data", "l3_analysis.sqlite3").exists(),
    "需要本机 L3 analysis sqlite",
)
class LiveAnalysisReadOnlyTests(unittest.TestCase):
    """使用已有 AnalysisRun；不修改 L3 行内容。"""

    CASES = [
        ("AR-5c5c1ec454e0", "REC-MA-20260314-00008"),  # ABNORMAL / insufficient reasoning
        ("AR-82c631a14fd1", "REC-DP-20260314-03404"),  # CONFLICT
        ("AR-82c631a14fd1", "REC-DP-20260314-00162"),  # UNCERTAIN
    ]

    def test_read_bundle_without_mutating_l3(self):
        for analysis_run_id, record_id in self.CASES:
            before = ANALYSIS_STORE.get_record(analysis_run_id, record_id)
            before_json = json.dumps(before.get("evidence") or {}, sort_keys=True)
            assessment = analyze_record(
                l4_run_id="L4R-test",
                analysis_run_id=analysis_run_id,
                record_id=record_id,
            )
            after = ANALYSIS_STORE.get_record(analysis_run_id, record_id)
            after_json = json.dumps(after.get("evidence") or {}, sort_keys=True)
            self.assertEqual(before_json, after_json)
            self.assertEqual(assessment.get("root_cause_confirmation"), ROOT_CAUSE_CONFIRMATION)
            self.assertNotEqual(assessment.get("root_cause_confirmation"), "confirmed")
            # 不夹带答案列
            blob = json.dumps(assessment, ensure_ascii=False)
            for key in FORBIDDEN_ANSWER_KEYS:
                self.assertNotIn(f'"{key}"', blob)
            self.assertIsNone((assessment.get("impact_assessment") or {}).get("impact_severity_level"))
            # reasoning 可不存在仍能产出
            self.assertIn(assessment.get("outcome"), {
                "candidates_generated",
                "insufficient_evidence",
                "no_applicable_causal_knowledge",
                "mapping_unresolved",
                "analysis_failed",
            })

    def test_run_idempotent_on_real_analysis(self):
        analysis_run_id, record_id = self.CASES[0]
        first = run_l4_for_analysis(analysis_run_id, record_ids=[record_id], max_records=1)
        second = run_l4_for_analysis(analysis_run_id, record_ids=[record_id], max_records=1)
        self.assertEqual(first["l4_run"]["l4_run_id"], second["l4_run"]["l4_run_id"])
        self.assertFalse(second["created"])


if __name__ == "__main__":
    unittest.main()
