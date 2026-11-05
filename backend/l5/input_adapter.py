"""L5InputAdapter：按显式 run_id 只读聚合 L2–L4，禁止猜测最新结果。"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from l2.handoff.database import connect as l2_connect
from l2.handoff.database import default_db_path as l2_db_path
from l2.handoff.store import HANDOFF_STORE
from l3.knowledge.enrichment import ENRICHMENT_STORE
from l3.reasoning.jobs import ReasoningStore
from l3.runtime.store import ANALYSIS_STORE
from l4.reasoning.store import L4_REASONING_STORE
from l4.store import L4_STORE


class L5InputError(Exception):
    def __init__(self, message: str, *, code: str = "l5_input_error"):
        super().__init__(message)
        self.message = message
        self.code = code


def _stable_hash(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _load_l2_record(diagnosis_run_id: str, record_id: str) -> dict[str, Any] | None:
    connection = l2_connect(l2_db_path())
    try:
        row = connection.execute(
            """
            SELECT record_id, status, screening_result, result_json
            FROM diagnosis_records
            WHERE diagnosis_run_id = ? AND record_id = ?
            """,
            (diagnosis_run_id, record_id),
        ).fetchone()
        if not row:
            return None
        result = json.loads(row["result_json"] or "{}")
        return {
            "record_id": row["record_id"],
            "status": row["status"],
            "screening_result": row["screening_result"],
            "result": result if isinstance(result, dict) else {},
        }
    finally:
        connection.close()


def build_l5_input_context(
    *,
    record_id: str,
    diagnosis_run_id: str | None = None,
    analysis_run_id: str | None = None,
    l4_run_id: str | None = None,
    l3_reasoning_run_id: str | None = None,
    l4_reasoning_run_id: str | None = None,
    enrichment_run_id: str | None = None,
) -> dict[str, Any]:
    """构造 L5InputContext。缺少可选层记入 source_availability，不伪装完整。"""
    if not record_id:
        raise L5InputError("record_id 必填", code="missing_record_id")
    if not diagnosis_run_id and not analysis_run_id:
        raise L5InputError(
            "必须显式提供 diagnosis_run_id 或 analysis_run_id，禁止仅按 record_id 猜测跨层结果",
            code="missing_source_run",
        )

    availability: dict[str, Any] = {
        "l2": False,
        "l3_core": False,
        "l3_reasoning": False,
        "l3_knowledge": False,
        "l4_core": False,
        "l4_reasoning": False,
    }
    limitations: list[str] = []
    source_versions: dict[str, Any] = {}
    source_run_ids: dict[str, Any] = {
        "diagnosis_run_id": diagnosis_run_id,
        "analysis_run_id": analysis_run_id,
        "l4_run_id": l4_run_id,
        "l3_reasoning_run_id": l3_reasoning_run_id,
        "l4_reasoning_run_id": l4_reasoning_run_id,
        "enrichment_run_id": enrichment_run_id,
    }

    l2_run = None
    l2_record = None
    analysis_run = None
    analysis_record = None
    evidence_bundle: dict[str, Any] = {}
    l3_reasoning = None
    enrichment = None
    l4_run = None
    l4_assessment = None
    l4_reasoning = None

    # --- L3 Core（若给出 analysis_run_id）---
    if analysis_run_id:
        try:
            analysis_run = ANALYSIS_STORE.get_run(analysis_run_id)
        except KeyError as exc:
            raise L5InputError(f"AnalysisRun 不存在：{analysis_run_id}", code="analysis_run_not_found") from exc
        src_diag = analysis_run.get("source_diagnosis_run_id")
        if diagnosis_run_id and src_diag and str(src_diag) != str(diagnosis_run_id):
            raise L5InputError(
                f"版本不一致：analysis_run 绑定 diagnosis={src_diag}，请求 diagnosis={diagnosis_run_id}",
                code="diagnosis_analysis_mismatch",
            )
        if not diagnosis_run_id and src_diag:
            diagnosis_run_id = str(src_diag)
            source_run_ids["diagnosis_run_id"] = diagnosis_run_id
        try:
            analysis_record = ANALYSIS_STORE.get_record(analysis_run_id, record_id)
        except KeyError as exc:
            raise L5InputError(
                f"Analysis 记录不存在：{record_id} @ {analysis_run_id}",
                code="analysis_record_not_found",
            ) from exc
        evidence_bundle = analysis_record.get("evidence") or {}
        if not isinstance(evidence_bundle, dict):
            evidence_bundle = {}
        availability["l3_core"] = evidence_bundle.get("schema") == "L3EvidenceBundle"
        if not availability["l3_core"]:
            limitations.append("l3_evidence_bundle_missing_or_invalid")
        source_versions["model_bundle_id"] = analysis_run.get("model_bundle_id")
        source_versions["prepared_dataset_id"] = analysis_run.get("prepared_dataset_id")
        source_versions["dataset_revision_id"] = analysis_run.get("dataset_revision_id")
        source_versions["equipment_type"] = analysis_run.get("equipment_type")

    # --- L2 ---
    if diagnosis_run_id:
        try:
            l2_run = HANDOFF_STORE.get_diagnosis_run(diagnosis_run_id)
        except KeyError as exc:
            raise L5InputError(f"DiagnosisRun 不存在：{diagnosis_run_id}", code="diagnosis_run_not_found") from exc
        l2_record = _load_l2_record(diagnosis_run_id, record_id)
        if not l2_record:
            raise L5InputError(
                f"L2 记录不存在：{record_id} @ {diagnosis_run_id}",
                code="l2_record_not_found",
            )
        availability["l2"] = True
        source_versions["l2_model_bundle_id"] = l2_run.get("model_bundle_id")
        source_versions["l2_prepared_dataset_id"] = l2_run.get("prepared_dataset_id")
        source_versions["l2_dataset_revision_id"] = l2_run.get("dataset_revision_id")
        source_versions["l2_equipment_type"] = l2_run.get("equipment_type")
        # 与 L3 版本交叉校验
        if analysis_run:
            if (
                analysis_run.get("model_bundle_id")
                and l2_run.get("model_bundle_id")
                and str(analysis_run.get("model_bundle_id")) != str(l2_run.get("model_bundle_id"))
            ):
                raise L5InputError(
                    "model_bundle_id 跨层不一致",
                    code="model_bundle_mismatch",
                )
            if (
                analysis_run.get("prepared_dataset_id")
                and l2_run.get("prepared_dataset_id")
                and str(analysis_run.get("prepared_dataset_id")) != str(l2_run.get("prepared_dataset_id"))
            ):
                raise L5InputError(
                    "prepared_dataset_id 跨层不一致",
                    code="prepared_dataset_mismatch",
                )
            et_a = analysis_run.get("equipment_type")
            et_b = l2_run.get("equipment_type")
            if et_a and et_b and str(et_a) != str(et_b):
                raise L5InputError("equipment_type 跨层不一致", code="equipment_type_mismatch")
    else:
        limitations.append("l2_diagnosis_run_not_provided")

    # --- 可选 L3 Reasoning（仅显式 ID）---
    if l3_reasoning_run_id:
        rstore = ReasoningStore()
        try:
            rr = rstore.get(l3_reasoning_run_id)
            if analysis_run_id and rr.get("source_analysis_run_id") and str(rr.get("source_analysis_run_id")) != str(
                analysis_run_id
            ):
                raise L5InputError(
                    "L3 Reasoning 未绑定指定 AnalysisRun",
                    code="l3_reasoning_analysis_mismatch",
                )
            rec = rstore.get_record(l3_reasoning_run_id, record_id)
            l3_reasoning = rec.get("assessment") if rec else None
            availability["l3_reasoning"] = bool(l3_reasoning)
            source_versions["l3_reasoning_prompt_version"] = rr.get("prompt_version")
            source_versions["knowledge_snapshot_id"] = rr.get("knowledge_snapshot_id") or source_versions.get(
                "knowledge_snapshot_id"
            )
        except KeyError as exc:
            raise L5InputError(
                f"L3 ReasoningRun 不存在：{l3_reasoning_run_id}",
                code="l3_reasoning_not_found",
            ) from exc
        if not availability["l3_reasoning"]:
            limitations.append("l3_reasoning_record_missing")
    else:
        limitations.append("l3_reasoning_not_bound")

    # --- 可选 Knowledge enrichment ---
    if enrichment_run_id:
        try:
            er = ENRICHMENT_STORE.get(enrichment_run_id)
        except KeyError as exc:
            raise L5InputError(
                f"KnowledgeEnrichment 不存在：{enrichment_run_id}",
                code="enrichment_not_found",
            ) from exc
        if analysis_run_id and er.get("source_analysis_run_id") and str(er.get("source_analysis_run_id")) != str(
            analysis_run_id
        ):
            raise L5InputError("KnowledgeEnrichment 未绑定指定 AnalysisRun", code="enrichment_analysis_mismatch")
        try:
            enrichment = ENRICHMENT_STORE.get_record(enrichment_run_id, record_id)
        except KeyError:
            enrichment = None
        availability["l3_knowledge"] = bool(enrichment)
        source_versions["knowledge_snapshot_id"] = er.get("knowledge_snapshot_id") or source_versions.get(
            "knowledge_snapshot_id"
        )
        if not availability["l3_knowledge"]:
            limitations.append("knowledge_enrichment_record_missing")
    else:
        limitations.append("knowledge_enrichment_not_bound")

    # --- 可选 L4 Core ---
    if l4_run_id:
        try:
            l4_run = L4_STORE.get(l4_run_id)
        except KeyError as exc:
            raise L5InputError(f"L4Run 不存在：{l4_run_id}", code="l4_run_not_found") from exc
        if analysis_run_id and str(l4_run.get("source_analysis_run_id") or "") != str(analysis_run_id):
            raise L5InputError(
                f"L4Run 引用 analysis={l4_run.get('source_analysis_run_id')}，与请求 {analysis_run_id} 不一致",
                code="l4_analysis_mismatch",
            )
        if not analysis_run_id:
            analysis_run_id = str(l4_run.get("source_analysis_run_id") or "") or None
            source_run_ids["analysis_run_id"] = analysis_run_id
            # 若此前未加载 L3，补载（仍要求 analysis 存在）
            if analysis_run_id and analysis_run is None:
                return build_l5_input_context(
                    record_id=record_id,
                    diagnosis_run_id=diagnosis_run_id,
                    analysis_run_id=analysis_run_id,
                    l4_run_id=l4_run_id,
                    l3_reasoning_run_id=l3_reasoning_run_id,
                    l4_reasoning_run_id=l4_reasoning_run_id,
                    enrichment_run_id=enrichment_run_id,
                )
        try:
            l4_rec = L4_STORE.get_record(l4_run_id, record_id)
            l4_assessment = l4_rec.get("assessment") or {}
            availability["l4_core"] = bool(l4_assessment)
        except KeyError:
            limitations.append("l4_record_missing_for_record_id")
            availability["l4_core"] = False
        source_versions["causal_knowledge_version"] = l4_run.get("causal_knowledge_version")
        source_versions["equipment_mapping_version"] = l4_run.get("equipment_mapping_version")
        if isinstance(l4_assessment, dict):
            source_versions["knowledge_snapshot_id"] = l4_assessment.get("knowledge_snapshot_id") or source_versions.get(
                "knowledge_snapshot_id"
            )
            source_versions["applicability_version"] = l4_assessment.get("applicability_version")
            source_versions["evidence_match_version"] = l4_assessment.get("rules_version")
    else:
        limitations.append("l4_core_not_bound")

    # --- 可选 L4 Reasoning ---
    if l4_reasoning_run_id:
        try:
            l4rr = L4_REASONING_STORE.get(l4_reasoning_run_id)
            if l4_run_id and str(l4rr.get("source_l4_run_id") or "") != str(l4_run_id):
                raise L5InputError("L4 Reasoning 未绑定指定 L4Run", code="l4_reasoning_l4_mismatch")
            if analysis_run_id and l4rr.get("source_analysis_run_id") and str(l4rr.get("source_analysis_run_id")) != str(
                analysis_run_id
            ):
                raise L5InputError(
                    "L4 Reasoning 未绑定指定 AnalysisRun",
                    code="l4_reasoning_analysis_mismatch",
                )
            l4r_rec = L4_REASONING_STORE.get_record(l4_reasoning_run_id, record_id)
            l4_reasoning = (l4r_rec or {}).get("assessment")
            availability["l4_reasoning"] = bool(l4_reasoning)
            source_versions["l4_reasoning_prompt_version"] = l4rr.get("prompt_version")
        except KeyError as exc:
            raise L5InputError(
                f"L4 ReasoningRun 不存在：{l4_reasoning_run_id}",
                code="l4_reasoning_not_found",
            ) from exc
        if not availability["l4_reasoning"]:
            limitations.append("l4_reasoning_record_missing")
    else:
        limitations.append("l4_reasoning_not_bound")

    equipment_type = (
        (l4_assessment or {}).get("equipment_type")
        or (evidence_bundle.get("l2_context") or {}).get("equipment_type")
        or (analysis_run or {}).get("equipment_type")
        or (l2_run or {}).get("equipment_type")
    )
    equipment_id = (
        (l4_assessment or {}).get("equipment_id")
        or (evidence_bundle.get("l2_context") or {}).get("equipment_id")
        or (analysis_record or {}).get("equipment_id")
    )

    fingerprint_payload = {
        "record_id": record_id,
        "source_run_ids": source_run_ids,
        "source_versions": {
            k: source_versions.get(k)
            for k in (
                "model_bundle_id",
                "l2_model_bundle_id",
                "prepared_dataset_id",
                "dataset_revision_id",
                "causal_knowledge_version",
                "knowledge_snapshot_id",
            )
        },
        "availability": availability,
    }
    input_fingerprint = _stable_hash(fingerprint_payload)

    return {
        "schema": "L5InputContext",
        "record_id": record_id,
        "equipment_id": equipment_id,
        "equipment_type": equipment_type,
        "source_run_ids": source_run_ids,
        "source_versions": source_versions,
        "source_availability": availability,
        "input_fingerprint": input_fingerprint,
        "limitations": limitations,
        "l2_run": l2_run,
        "l2_record": l2_record,
        "analysis_run": analysis_run,
        "analysis_record": analysis_record,
        "evidence_bundle": evidence_bundle,
        "l3_reasoning_assessment": l3_reasoning,
        "knowledge_enrichment": enrichment,
        "l4_run": l4_run,
        "root_cause_assessment": l4_assessment if isinstance(l4_assessment, dict) else {},
        "causal_reasoning_assessment": l4_reasoning if isinstance(l4_reasoning, dict) else {},
    }
