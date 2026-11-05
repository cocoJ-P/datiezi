"""L2 → L3 交接数据契约定稿（L2 v1.1）。

本模块仅整理交接载荷结构，不执行 L3 算法。
"""

from __future__ import annotations

from typing import Any, Literal

TriggerReason = Literal["auto_review", "manual_request"]

CONTRACT_VERSION = "l2_l3_handoff_v1"

# auto_review：ABNORMAL / CONFLICT / UNCERTAIN 自动建议进入 L3
# manual_request：用户对 NORMAL 等筛查完成记录主动请求深入分析，不改写原始 decision.route
TRIGGER_AUTO_REVIEW: TriggerReason = "auto_review"
TRIGGER_MANUAL_REQUEST: TriggerReason = "manual_request"


def infer_trigger_reason(*, screening_result: str | None, route: str | None) -> TriggerReason:
    screening = str(screening_result or "").upper()
    if screening in {"ABNORMAL", "CONFLICT", "UNCERTAIN"} or str(route or "") == "l3_review_recommended":
        return TRIGGER_AUTO_REVIEW
    return TRIGGER_MANUAL_REQUEST


def build_l2_result_slice(result: dict[str, Any]) -> dict[str, Any]:
    """从单条 L2 诊断结果抽取交接用切片（不含观测特征全文）。"""
    decision = result.get("decision") or {}
    return {
        "record_id": result.get("record_id"),
        "screening_result": decision.get("screening_result"),
        "preliminary_fault": decision.get("preliminary_fault"),
        "route": decision.get("route"),
        "reason_codes": list(decision.get("reason_codes") or []),
        "binary": result.get("binary_classifier") or {},
        "multiclass": result.get("multiclass_classifier") or {},
        "consistency": result.get("consistency") or {},
        # 参考说明可附带，但不得视为已验证频谱证据。
        "spectral_reference": result.get("spectral_reference"),
    }


def build_l3_handoff_payload(
    *,
    equipment_type: str,
    record_ids: list[str],
    l2_results: list[dict[str, Any]],
    trigger_reason: TriggerReason,
    equipment_id: str | None = None,
    equipment_group_id: str | None = None,
    prepared_dataset_id: str | None = None,
    dataset_revision_id: str | None = None,
    diagnosis_run_id: str | None = None,
    model_bundle_id: str | None = None,
    display_name: str | None = None,
) -> dict[str, Any]:
    """构造向后兼容的 L2→L3 交接 JSON。"""
    return {
        "contract_version": CONTRACT_VERSION,
        "equipment_type": equipment_type,
        "equipment_id": equipment_id,
        "equipment_group_id": equipment_group_id,
        "display_name": display_name,
        "record_ids": list(record_ids),
        "prepared_dataset_id": prepared_dataset_id,
        "dataset_revision_id": dataset_revision_id,
        "diagnosis_run_id": diagnosis_run_id,
        "model_bundle_id": model_bundle_id,
        "trigger_reason": trigger_reason,
        "l2_results": [build_l2_result_slice(item) if "decision" in item else item for item in l2_results],
        "notes": [
            "L3 通过 prepared_dataset_id + record_id 自行读取已发布观测特征。",
            "不得将 spectral_reference 视为已验证频谱证据。",
            "不得传入原始故障标签、根因或建议措施作为推理真值。",
            "manual_request 不得改写原始 L2 decision.route。",
        ],
    }
