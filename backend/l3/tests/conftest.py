"""L3 测试共享夹具。"""

from __future__ import annotations

from l3.core.context import AnalysisContext, HistoryPoint
from l2.routing_policy import ROUTING_POLICY_VERSION


def make_analysis_context(**kwargs) -> AnalysisContext:
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
        l2_multiclass={
            "probabilities": {"轴承磨损": 0.8},
            "ranked_classes": [{"label": "轴承磨损", "probability": 0.8}],
        },
        l2_consistency={},
        l2_spectral_reference={"verification_status": "reference_only"},
        trigger_reason="fault_detected",
        routing_policy_version=ROUTING_POLICY_VERSION,
        history_before=[
            HistoryPoint(
                record_id=f"H{i}",
                collection_time=f"2024-01-0{i} 10:00:00",
                features={
                    "X向振动(mm/s)": 2.0,
                    "BPFO幅值(g)": 0.1,
                    "包络谱峰值(g)": 0.2,
                    "驱动端轴承温度(°C)": 60.0,
                },
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
