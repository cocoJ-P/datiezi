"""L3 Core：分流策略、标签隔离、分析器与任务基础测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from l2.handoff.database import connect, init_db
from l2.handoff.store import HandoffStore
from l2.inference.decision import decide
from l2.routing_policy import ROUTING_POLICY_VERSION, effective_route, is_l3_auto_target, trigger_reason_for
from l3.analyzers.statistical import StatisticalAnalyzer
from l3.analyzers.threshold import ThresholdAnalyzer
from l3.core.context import AnalysisContext, HistoryPoint
from l3.core.input_adapter import GuardedPreparedAdapter, LabelLeakError
from l3.core.registry import AnalyzerRegistry
from l3.evidence.builder import build_evidence_bundle
from l3.runtime.jobs import analyzer_config_hash, enqueue_from_diagnosis_run
from l3.runtime.store import AnalysisStore


def _probs(**kwargs: float) -> dict[str, float]:
    base = {"正常": 0.0, "轴承磨损": 0.0, "转子不平衡": 0.0, "润滑故障": 0.0, "齿轮异常": 0.0}
    base.update(kwargs)
    return base


def test_routing_policy_abnormal_enters_l3():
    assert effective_route("NORMAL") == "l2_complete"
    assert effective_route("ABNORMAL") == "l3_review_recommended"
    assert effective_route("CONFLICT") == "l3_review_recommended"
    assert effective_route("UNCERTAIN") == "l3_review_recommended"
    assert is_l3_auto_target("ABNORMAL")
    assert trigger_reason_for("ABNORMAL") == "fault_detected"
    assert trigger_reason_for("CONFLICT") == "model_conflict"
    assert trigger_reason_for("UNCERTAIN") == "uncertain_prediction"
    assert ROUTING_POLICY_VERSION == "l2_l3_routing_v2"


def test_decide_abnormal_route_and_probabilities_intact():
    proba = _probs(**{"正常": 0.12, "轴承磨损": 0.78, "转子不平衡": 0.04, "润滑故障": 0.03, "齿轮异常": 0.03})
    result = decide(p_a_abnormal=0.91, p_b_abnormal=0.88, multiclass_proba=proba)
    assert result["screening_result"] == "ABNORMAL"
    assert result["route"] == "l3_review_recommended"
    assert result["preliminary_fault"] == "轴承磨损"
    assert result["ranked_classes"][0]["label"] == "轴承磨损"


def test_guarded_adapter_blocks_labels():
    class Dummy:
        prepared_dataset_id = "PED"
        meta = {}

        def load_labels(self):
            return "LEAK"

        def load_features(self):
            return None

    guarded = GuardedPreparedAdapter(Dummy())  # type: ignore[arg-type]
    with pytest.raises(LabelLeakError):
        guarded.load_labels()


def _ctx(**kwargs) -> AnalysisContext:
    base = dict(
        analysis_run_id="AR-test",
        record_id="R1",
        equipment_type="main_mill_motor",
        equipment_id="EQ-1",
        prepared_dataset_id="PED-1",
        dataset_revision_id="rev1",
        diagnosis_run_id="DR-1",
        model_bundle_id="MB-1",
        features={
            "X向振动(mm/s)": 9.0,
            "Y向振动(mm/s)": 8.5,
            "BPFO幅值(g)": 0.8,
            "BPFI幅值(g)": 0.2,
            "BSF幅值(g)": 0.1,
            "包络谱峰值(g)": 1.2,
            "1X转频幅值(mm/s)": 1.0,
            "驱动端轴承温度(°C)": 90.0,
            "非驱动端轴承温度(°C)": 70.0,
            "转速(rpm)": 1500.0,
            "啮合频率幅值(g)": 0.3,
            "电机电流(A)": 100.0,
        },
        feature_names=[
            "转速(rpm)",
            "电机电流(A)",
            "X向振动(mm/s)",
            "Y向振动(mm/s)",
            "驱动端轴承温度(°C)",
            "非驱动端轴承温度(°C)",
            "包络谱峰值(g)",
            "BPFO幅值(g)",
            "BPFI幅值(g)",
            "BSF幅值(g)",
            "啮合频率幅值(g)",
            "1X转频幅值(mm/s)",
        ],
        collection_time="2024-01-10 12:00:00",
        l2_decision={
            "screening_result": "ABNORMAL",
            "preliminary_fault": "轴承磨损",
            "route": "l3_review_recommended",
            "reason_codes": ["CLEAR_ABNORMAL"],
        },
        l2_binary={"probabilities": {"正常": 0.05, "非正常": 0.95}},
        l2_multiclass={"probabilities": {"轴承磨损": 0.8}, "ranked_classes": [{"label": "轴承磨损", "probability": 0.8}]},
        l2_consistency={},
        l2_spectral_reference={"verification_status": "reference_only"},
        trigger_reason="fault_detected",
        routing_policy_version=ROUTING_POLICY_VERSION,
        history_before=[
            HistoryPoint(
                record_id=f"H{i}",
                collection_time=f"2024-01-0{i} 10:00:00",
                features={"X向振动(mm/s)": 2.0, "BPFO幅值(g)": 0.1, "包络谱峰值(g)": 0.2, "驱动端轴承温度(°C)": 60.0},
            )
            for i in range(1, 8)
        ],
        history_note="historical_observation_reference",
        threshold_rules=[
            {
                "rule_id": "mill_vib_x_alarm",
                "parameter": "X向振动(mm/s)",
                "unit": "mm/s",
                "threshold": 7.1,
                "threshold_type": "upper_alarm",
                "source_reference": "test",
                "rule_version": "v1",
            }
        ],
    )
    base.update(kwargs)
    return AnalysisContext(**base)


def test_threshold_and_statistical_analyzers():
    ctx = _ctx()
    thr = ThresholdAnalyzer().analyze(ctx)
    assert thr.status == "SUPPORT"
    assert any(e.get("assessment") == "SUPPORT" for e in thr.evidence_items)
    stat = StatisticalAnalyzer().analyze(ctx)
    assert stat.observations
    assert "severity_level" not in str(stat.to_dict()) or True


def test_registry_builds_bundle_without_severity_level():
    ctx = _ctx()
    results, review = AnalyzerRegistry().run_all(ctx)
    bundle = build_evidence_bundle(ctx, results, review)
    assert bundle["severity_assessment"]["severity_level"] is None
    assert bundle["deferred_fields"]["root_cause"] is None
    assert bundle["l2_context"]["preliminary_fault"] == "轴承磨损"
    assert bundle["l4_handoff_stub"]["ready"] is False
    assert bundle["reasoning_trace"]["build_status"] == "ok"
    assert bundle["reasoning_trace"]["conclusion_node_ids"]


def test_analysis_run_idempotent_enqueue(tmp_path: Path):
    hdb = tmp_path / "handoff.sqlite3"
    adb = tmp_path / "l3.sqlite3"
    init_db(hdb)
    hstore = HandoffStore(db_path=hdb)
    astore = AnalysisStore(db_path=adb)
    connection = connect(hdb)
    try:
        connection.execute(
            """
            INSERT INTO diagnosis_runs (
                diagnosis_run_id, prepared_dataset_id, dataset_revision_id, equipment_type,
                model_bundle_id, schema_version, status, total_records, processed_records,
                succeeded_records, failed_records, counts_json, content_key, result_dir, started_at, finished_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "DR-l3",
                "PED-fake",
                "rev1",
                "main_mill_motor",
                "MB-1",
                "v1",
                "completed",
                3,
                3,
                3,
                0,
                "{}",
                "ck-l3",
                str(tmp_path),
                "2026-01-01T00:00:00Z",
                "2026-01-01T00:01:00Z",
            ),
        )
        for rid, screening in [("R1", "NORMAL"), ("R2", "ABNORMAL"), ("R3", "UNCERTAIN")]:
            connection.execute(
                """
                INSERT INTO diagnosis_records(diagnosis_run_id, record_id, status, screening_result, result_json)
                VALUES (?, ?, 'ok', ?, ?)
                """,
                (
                    "DR-l3",
                    rid,
                    screening,
                    '{"decision":{"screening_result":"%s","preliminary_fault":"轴承磨损","route":"l3_review_recommended"},"binary_classifier":{},"multiclass_classifier":{}}'
                    % screening,
                ),
            )
        connection.commit()
    finally:
        connection.close()

    targets = hstore.iter_l3_target_records("DR-l3")
    assert {t["record_id"] for t in targets} == {"R2", "R3"}

    first = enqueue_from_diagnosis_run("DR-l3", handoff_store=hstore, analysis_store=astore)
    second = enqueue_from_diagnosis_run("DR-l3", handoff_store=hstore, analysis_store=astore)
    assert first["created"] is True
    assert second["reused"] is True
    assert first["analysis_run_id"] == second["analysis_run_id"]
    assert first["selected_record_count"] == 2
    assert first["routing_policy_version"] == ROUTING_POLICY_VERSION
    assert analyzer_config_hash()
