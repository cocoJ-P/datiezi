"""L4 Core v1.1 语义可靠性单元测试。"""

from __future__ import annotations

import unittest

from l4.applicability import evaluate_applicability
from l4.candidate_builder import build_root_cause_candidates
from l4.causal_knowledge.loader import catalog_version, get_catalog, rules_version
from l4.engine import analyze_record
from l4.fault_eligibility import evaluate_fault_eligibility, filter_faults_for_root_cause
from l4.impact_analyzer import analyze_impact
from l4.path_validator import validate_candidates
from l4.schema import ROOT_CAUSE_CONFIRMATION, is_anomaly_only_evidence


class VersionTests(unittest.TestCase):
    def test_catalog_current(self):
        # v1.2 接替默认目录；本文件保留 v1.1 语义回归断言
        self.assertEqual(catalog_version(), "l4_causal_knowledge_v1_2")
        self.assertEqual(rules_version(), "l4_evidence_match_v1_2")
        self.assertEqual(len(get_catalog().get("entries") or []), 8)


class FaultEligibilityTests(unittest.TestCase):
    def test_contradicted_not_expanded(self):
        ctx = {
            "l3_fault_candidates": [
                {"fault_type": "轴承磨损", "verdict": "CONTRADICTED", "source": "l3_fault_type_review"},
                {"fault_type": "齿轮异常", "verdict": "NOT_APPLICABLE", "source": "l3_fault_type_review"},
            ]
        }
        filtered = filter_faults_for_root_cause(ctx)
        self.assertEqual(filtered["eligible_count"], 0)
        self.assertEqual(filtered["excluded_count"], 2)
        built = build_root_cause_candidates({**ctx, "record_id": "R1", "equipment_type": "high_pressure_descaling_pump"})
        self.assertEqual(built["root_cause_candidates"], [])
        self.assertEqual(built["empty_reason"], "all_faults_excluded_by_eligibility")

    def test_conflict_conservative_exclude(self):
        ctx = {
            "l3_fault_candidates": [
                {"fault_type": "轴承磨损", "verdict": "SUPPORTED", "source": "l3_fault_type_review"},
            ],
            "reasoning_assessment": {
                "candidate_hypotheses": [
                    {"fault_type": "轴承磨损", "assessment": "CONTRADICTED"},
                ]
            },
        }
        decision = evaluate_fault_eligibility(ctx["l3_fault_candidates"][0], ctx)
        self.assertEqual(decision["eligibility"], "excluded_core_reasoning_conflict")
        self.assertFalse(decision["expand_root_cause"])

    def test_inconclusive_kept(self):
        ctx = {
            "record_id": "R2",
            "equipment_type": "high_pressure_descaling_pump",
            "l3_fault_candidates": [
                {"fault_type": "轴承磨损", "verdict": "INCONCLUSIVE", "source": "l3_fault_type_review"},
                {"fault_type": "齿轮异常", "verdict": "CONTRADICTED", "source": "l3_fault_type_review"},
            ],
        }
        built = build_root_cause_candidates(ctx)
        faults = {c["related_fault_type"] for c in built["root_cause_candidates"]}
        self.assertIn("轴承磨损", faults)
        self.assertNotIn("齿轮异常", faults)
        self.assertTrue(any(c.get("evidence_insufficient_from_l3") for c in built["root_cause_candidates"]))


class EvidenceSemanticsTests(unittest.TestCase):
    def test_bpfo_not_cause_specific_for_seal(self):
        ctx = {
            "record_id": "R-seal",
            "equipment_type": "high_pressure_descaling_pump",
            "support_evidence": [
                {
                    "evidence_id": "mech:R:bearing:BPFO幅值(g)",
                    "feature_name": "BPFO幅值(g)",
                    "assessment": "SUPPORT",
                    "analyzer_id": "mechanical_feature",
                    "observed_value": 0.5,
                }
            ],
            "evidence_bundle": {
                "evidence_items": [
                    {
                        "evidence_id": "mech:R:bearing:BPFO幅值(g)",
                        "feature_name": "BPFO幅值(g)",
                        "assessment": "SUPPORT",
                        "analyzer_id": "mechanical_feature",
                        "observed_value": 0.5,
                    }
                ]
            },
            "l3_fault_candidates": [
                {"fault_type": "轴承磨损", "verdict": "SUPPORTED", "source": "l3_fault_type_review"}
            ],
        }
        built = build_root_cause_candidates(ctx)
        seal = [c for c in built["root_cause_candidates"] if c["causal_knowledge_id"] == "CK-seal-leak-bearing-lube-fail"]
        self.assertTrue(seal)
        validated = validate_candidates(ctx, candidates=seal, paths=built["causal_paths"])
        for cand in validated["root_cause_candidates"]:
            if cand["causal_knowledge_id"] != "CK-seal-leak-bearing-lube-fail":
                continue
            self.assertNotIn("mech:R:bearing:BPFO幅值(g)", cand.get("supporting_evidence_ids") or [])
            self.assertIn(
                "mech:R:bearing:BPFO幅值(g)",
                cand.get("fault_manifestation_evidence_ids") or [],
            )

    def test_mesh_not_cause_specific_for_oil_water(self):
        ctx = {
            "record_id": "R-mesh",
            "equipment_type": "main_mill_motor",
            "support_evidence": [
                {
                    "evidence_id": "mech:R:gear_mesh:啮合频率幅值(g)",
                    "feature_name": "啮合频率幅值(g)",
                    "assessment": "SUPPORT",
                    "analyzer_id": "mechanical_feature",
                    "observed_value": 1.0,
                }
            ],
            "evidence_bundle": {
                "evidence_items": [
                    {
                        "evidence_id": "mech:R:gear_mesh:啮合频率幅值(g)",
                        "feature_name": "啮合频率幅值(g)",
                        "assessment": "SUPPORT",
                        "analyzer_id": "mechanical_feature",
                        "observed_value": 1.0,
                    }
                ]
            },
            "l3_fault_candidates": [
                {"fault_type": "齿轮异常", "verdict": "SUPPORTED", "source": "l3_fault_type_review"}
            ],
        }
        built = build_root_cause_candidates(ctx)
        # oil-water 对电机为 reference_only（案例设备不一致）
        water_active = [
            c for c in built["root_cause_candidates"] if c["causal_knowledge_id"] == "CK-lube-water-gear-pitting"
        ]
        self.assertEqual(water_active, [])
        water_ref = [
            c for c in built["reference_candidates"] if c["causal_knowledge_id"] == "CK-lube-water-gear-pitting"
        ]
        if water_ref:
            validated = validate_candidates(ctx, candidates=water_ref, paths=built["causal_paths"])
            for cand in validated["root_cause_candidates"]:
                self.assertNotIn(
                    "mech:R:gear_mesh:啮合频率幅值(g)", cand.get("supporting_evidence_ids") or []
                )

    def test_anomaly_only_no_cause_specific(self):
        self.assertTrue(is_anomaly_only_evidence("thr:R:mill_vib_x_alarm", "X向振动"))
        ctx = {
            "record_id": "R-anom",
            "equipment_type": "main_mill_motor",
            "support_evidence": [
                {
                    "evidence_id": "thr:R:mill_vib_x_alarm",
                    "feature_name": "X向振动(mm/s)",
                    "assessment": "SUPPORT",
                    "analyzer_id": "threshold",
                    "observed_value": 5.0,
                }
            ],
            "evidence_bundle": {
                "evidence_items": [
                    {
                        "evidence_id": "thr:R:mill_vib_x_alarm",
                        "feature_name": "X向振动(mm/s)",
                        "assessment": "SUPPORT",
                        "analyzer_id": "threshold",
                        "observed_value": 5.0,
                    }
                ]
            },
            "l3_fault_candidates": [
                {"fault_type": "润滑故障", "verdict": "SUPPORTED", "source": "l3_fault_type_review"}
            ],
        }
        built = build_root_cause_candidates(ctx)
        validated = validate_candidates(
            ctx, candidates=built["root_cause_candidates"], paths=built["causal_paths"]
        )
        for cand in validated["root_cause_candidates"]:
            self.assertEqual(cand["assessment_status"], ROOT_CAUSE_CONFIRMATION)
            self.assertEqual(cand.get("supporting_evidence_ids") or [], [])

    def test_one_x_as_fault_manifestation_not_auto_cause(self):
        ctx = {
            "record_id": "R-1x",
            "equipment_type": "main_mill_motor",
            "support_evidence": [
                {
                    "evidence_id": "mech:R:unbalance_1x:1X转频幅值(mm/s)",
                    "feature_name": "1X转频幅值(mm/s)",
                    "assessment": "SUPPORT",
                    "analyzer_id": "mechanical_feature",
                    "observed_value": 2.0,
                }
            ],
            "evidence_bundle": {
                "evidence_items": [
                    {
                        "evidence_id": "mech:R:unbalance_1x:1X转频幅值(mm/s)",
                        "feature_name": "1X转频幅值(mm/s)",
                        "assessment": "SUPPORT",
                        "analyzer_id": "mechanical_feature",
                        "observed_value": 2.0,
                    }
                ]
            },
            "l3_fault_candidates": [
                {"fault_type": "转子不平衡", "verdict": "SUPPORTED", "source": "l3_fault_type_review"}
            ],
        }
        built = build_root_cause_candidates(ctx)
        mass = [c for c in built["root_cause_candidates"] if c["causal_knowledge_id"] == "CK-rotor-mass-imbalance"]
        validated = validate_candidates(ctx, candidates=mass, paths=built["causal_paths"])
        for cand in validated["root_cause_candidates"]:
            self.assertEqual(cand.get("supporting_evidence_ids") or [], [])
            self.assertIn(
                "mech:R:unbalance_1x:1X转频幅值(mm/s)",
                cand.get("fault_manifestation_evidence_ids") or [],
            )


class ApplicabilityTests(unittest.TestCase):
    def test_star_not_auto_applicable(self):
        entry = {
            "equipment_scope": ["*"],
            "required_components": ["齿轮"],
            "causal_knowledge_id": "CK-x",
        }
        appl = evaluate_applicability(entry, equipment_type="high_pressure_descaling_pump")
        # v1.2：* → reference_only；齿轮对泵为显式缺席时走 not_applicable（若 required 命中 absent）
        self.assertIn(appl["applicability_status"], {"unresolved", "reference_only", "not_applicable"})
        self.assertFalse(appl["expand_as_active_candidate"])

    def test_unknown_equipment_unresolved(self):
        entry = {
            "equipment_scope": ["main_mill_motor"],
            "required_components": ["轴承"],
            "causal_knowledge_id": "CK-x",
        }
        appl = evaluate_applicability(entry, equipment_type="brand_new_unknown_type")
        self.assertEqual(appl["applicability_status"], "unresolved")

    def test_gear_not_applicable_on_descaling_pump(self):
        ctx = {
            "record_id": "R-dp",
            "equipment_type": "high_pressure_descaling_pump",
            "l3_fault_candidates": [
                {"fault_type": "齿轮异常", "verdict": "SUPPORTED", "source": "l3_fault_type_review"}
            ],
        }
        built = build_root_cause_candidates(ctx)
        active_gear = [
            c
            for c in built["root_cause_candidates"]
            if c["causal_knowledge_id"] in {"CK-gear-mesh-abnormality", "CK-lube-water-gear-pitting"}
        ]
        self.assertEqual(active_gear, [])

    def test_seal_not_applicable_on_main_motor(self):
        ctx = {
            "record_id": "R-mm",
            "equipment_type": "main_mill_motor",
            "l3_fault_candidates": [
                {"fault_type": "轴承磨损", "verdict": "SUPPORTED", "source": "l3_fault_type_review"}
            ],
        }
        built = build_root_cause_candidates(ctx)
        seal_active = [
            c for c in built["root_cause_candidates"] if c["causal_knowledge_id"] == "CK-seal-leak-bearing-lube-fail"
        ]
        self.assertEqual(seal_active, [])
        self.assertTrue(
            any(
                x.get("causal_knowledge_id") == "CK-seal-leak-bearing-lube-fail"
                for x in built.get("excluded_by_applicability") or []
            )
        )

    def test_no_knowledge_empty(self):
        built = build_root_cause_candidates(
            {
                "record_id": "R0",
                "equipment_type": "main_mill_motor",
                "l3_fault_candidates": [{"fault_type": "不存在XYZ", "verdict": "SUPPORTED", "source": "test"}],
            }
        )
        self.assertEqual(built["root_cause_candidates"], [])
        self.assertEqual(built["empty_reason"], "no_applicable_causal_knowledge")


class ImpactDirectionTests(unittest.TestCase):
    def test_upstream_downstream_separated(self):
        ctx = {
            "equipment_id": "MM-01",
            "equipment_type": "main_mill_motor",
            "l2_context": {"screening_result": "ABNORMAL"},
            "evidence_bundle": {"support_evidence": [], "evidence_sufficiency": "limited"},
        }
        impact = analyze_impact(ctx)
        self.assertEqual(impact["propagation_status"], "unknown")
        pot = impact["potential_impact"]
        self.assertEqual(pot.get("title"), "潜在关联范围")
        self.assertIn("upstream_dependencies", pot)
        self.assertIn("downstream_connections", pot)
        for item in pot.get("upstream_dependencies") or []:
            self.assertEqual(item.get("relation_direction"), "in")
            self.assertEqual(item.get("category"), "upstream_dependencies")
        for item in pot.get("downstream_connections") or []:
            self.assertEqual(item.get("relation_direction"), "out")
            self.assertEqual(item.get("category"), "downstream_connections")


class NormalContextTests(unittest.TestCase):
    def test_normal_context_no_spurious_root_cause(self):
        """固定夹具：无异常证据 + 无有效故障时不产生无依据根因。"""
        ctx = {
            "record_id": "R-normal",
            "equipment_type": "main_mill_motor",
            "l2_context": {"screening_result": "NORMAL", "preliminary_fault": None},
            "l3_fault_candidates": [],
            "support_evidence": [],
            "evidence_bundle": {"evidence_items": [], "support_evidence": [], "evidence_sufficiency": "sufficient"},
        }
        assessment = analyze_record(
            l4_run_id="L4R-n",
            analysis_run_id="AR-n",
            record_id="R-normal",
            ctx=ctx,
        )
        self.assertEqual(assessment["root_cause_candidates"], [])
        self.assertEqual(assessment["root_cause_confirmation"], ROOT_CAUSE_CONFIRMATION)


if __name__ == "__main__":
    unittest.main()
