"""L5Run 创建与执行（确定性聚合，无 LLM）。"""

from __future__ import annotations

import hashlib
import logging
import os
from typing import Any

from l3.runtime.store import ANALYSIS_STORE
from l4.store import L4_STORE
from l5 import AGGREGATION_SCHEMA_VERSION, RECOMMENDATION_RULE_VERSION, __version__
from l5.aggregator import build_final_diagnosis
from l5.input_adapter import L5InputError, build_l5_input_context
from l5.provenance import build_full_provenance
from l5.store import L5_STORE, L5Store

LOG = logging.getLogger("l5.jobs")
DEFAULT_MAX_RECORDS = int(os.getenv("L5_MAX_RECORDS_PER_RUN", "200"))


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def content_key_for(
    *,
    diagnosis_run_id: str | None,
    analysis_run_id: str | None,
    l4_run_id: str | None,
    l3_reasoning_run_id: str | None,
    l4_reasoning_run_id: str | None,
    enrichment_run_id: str | None,
    record_ids: list[str],
) -> str:
    ids = ",".join(sorted(record_ids))
    # prediction_overlay_v1：十字段主值叠加 L5P 监督预测（不改 FieldSlot 契约）
    raw = (
        f"{diagnosis_run_id}|{analysis_run_id}|{l4_run_id}|{l3_reasoning_run_id}|"
        f"{l4_reasoning_run_id}|{enrichment_run_id}|{ids}|{AGGREGATION_SCHEMA_VERSION}|"
        f"{RECOMMENDATION_RULE_VERSION}|{__version__}|prediction_overlay_v1"
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def enqueue_l5_run(
    *,
    diagnosis_run_id: str | None = None,
    analysis_run_id: str | None = None,
    l4_run_id: str | None = None,
    l3_reasoning_run_id: str | None = None,
    l4_reasoning_run_id: str | None = None,
    enrichment_run_id: str | None = None,
    record_ids: list[str] | None = None,
    force_new: bool = False,
    max_records: int | None = None,
    store: L5Store | None = None,
) -> dict[str, Any]:
    if not diagnosis_run_id and not analysis_run_id and not l4_run_id:
        raise RuntimeError("必须显式提供 diagnosis_run_id / analysis_run_id / l4_run_id 之一")

    # 显式来源校验（禁止猜测；无效 ID 在入队阶段失败）
    l4_meta = None
    if l4_run_id:
        try:
            l4_meta = L4_STORE.get(l4_run_id)
        except KeyError as exc:
            raise RuntimeError(f"L4Run 不存在：{l4_run_id}") from exc
        if analysis_run_id and str(l4_meta.get("source_analysis_run_id") or "") != str(analysis_run_id):
            raise RuntimeError(
                f"L4Run 与 AnalysisRun 不一致：l4→{l4_meta.get('source_analysis_run_id')} vs {analysis_run_id}"
            )
        if not analysis_run_id:
            analysis_run_id = l4_meta.get("source_analysis_run_id")

    if analysis_run_id:
        try:
            ar = ANALYSIS_STORE.get_run(analysis_run_id)
        except KeyError as exc:
            raise RuntimeError(f"AnalysisRun 不存在：{analysis_run_id}") from exc
        if not diagnosis_run_id:
            diagnosis_run_id = ar.get("source_diagnosis_run_id")
        elif ar.get("source_diagnosis_run_id") and str(ar.get("source_diagnosis_run_id")) != str(diagnosis_run_id):
            raise RuntimeError(
                f"DiagnosisRun 与 AnalysisRun 不一致：analysis→{ar.get('source_diagnosis_run_id')} vs {diagnosis_run_id}"
            )
    elif diagnosis_run_id and not analysis_run_id and not l4_run_id:
        # 允许仅 L2 + 显式 record_ids（不完整报告路径）
        pass

    limit = max_records if max_records is not None else DEFAULT_MAX_RECORDS
    limit = max(1, min(int(limit), 200))

    if record_ids:
        targets = [str(r) for r in record_ids][:limit]
    elif analysis_run_id:
        page = ANALYSIS_STORE.list_results(analysis_run_id, offset=0, limit=limit)
        targets = [str(i["record_id"]) for i in page.get("items") or []]
    elif l4_run_id:
        page = L4_STORE.list_records(l4_run_id, offset=0, limit=limit)
        targets = [str(i["record_id"]) for i in page.get("items") or []]
    else:
        raise RuntimeError("仅有 diagnosis_run_id 时必须显式传入 record_ids（避免无界猜测）")

    if not targets:
        raise RuntimeError("没有可聚合的记录")

    key = content_key_for(
        diagnosis_run_id=diagnosis_run_id,
        analysis_run_id=analysis_run_id,
        l4_run_id=l4_run_id,
        l3_reasoning_run_id=l3_reasoning_run_id,
        l4_reasoning_run_id=l4_reasoning_run_id,
        enrichment_run_id=enrichment_run_id,
        record_ids=targets,
    )
    meta = {
        "diagnosis_run_id": diagnosis_run_id,
        "analysis_run_id": analysis_run_id,
        "l4_run_id": l4_run_id,
        "l3_reasoning_run_id": l3_reasoning_run_id,
        "l4_reasoning_run_id": l4_reasoning_run_id,
        "enrichment_run_id": enrichment_run_id,
        "aggregation_schema_version": AGGREGATION_SCHEMA_VERSION,
        "recommendation_rule_version": RECOMMENDATION_RULE_VERSION,
        "selected_record_count": len(targets),
        "selection": {"record_ids": targets, "max_records": limit},
        "source_versions": {},
    }
    rstore = store or L5_STORE
    run, created = rstore.create_or_get(content_key=key, meta=meta, force_new=force_new)
    return {"l5_run": run, "created": created, "selected_record_ids": targets}


def execute_l5_run(l5_run_id: str, *, store: L5Store | None = None) -> dict[str, Any]:
    rstore = store or L5_STORE
    run = rstore.get(l5_run_id)
    if run.get("status") == "completed":
        return {"l5_run": run, "reused": True}

    selection = run.get("selection") or {}
    targets = list(selection.get("record_ids") or [])
    rstore.update(l5_run_id, status="running", started_at=_now(), processed_record_count=0)

    ok = partial = failed = 0
    errors: list[str] = []
    for rid in targets:
        try:
            ctx = build_l5_input_context(
                record_id=rid,
                diagnosis_run_id=run.get("diagnosis_run_id"),
                analysis_run_id=run.get("analysis_run_id"),
                l4_run_id=run.get("l4_run_id"),
                l3_reasoning_run_id=run.get("l3_reasoning_run_id"),
                l4_reasoning_run_id=run.get("l4_reasoning_run_id"),
                enrichment_run_id=run.get("enrichment_run_id"),
            )
            assessment = build_final_diagnosis(ctx, l5_run_id=l5_run_id)
            # L5-P3：十字段主值叠加监督预测（失败则保留 L5a 聚合）
            try:
                from l5.synthesis.prediction_overlay import (
                    apply_supervised_predictions,
                    try_predict_for_context,
                )

                pred, pred_err = try_predict_for_context(ctx)
                if pred:
                    assessment = apply_supervised_predictions(assessment, pred)
                elif pred_err:
                    assessment.setdefault("limitations", []).append(
                        f"supervised_prediction_unavailable:{pred_err}"
                    )
            except Exception as pred_exc:  # noqa: BLE001
                assessment.setdefault("limitations", []).append(
                    f"supervised_prediction_error:{pred_exc}"
                )
            provenance = build_full_provenance(assessment, ctx)
            outcome = assessment.get("outcome") or "partial"
            status = "failed" if outcome == "failed" else "succeeded"
            if outcome == "failed":
                failed += 1
            elif outcome == "partial":
                partial += 1
            else:
                ok += 1
            rstore.save_record(
                l5_run_id,
                {
                    "record_id": rid,
                    "status": status,
                    "outcome": outcome,
                    "review_status": assessment.get("review_status"),
                    "completeness": assessment.get("completeness"),
                    "input_fingerprint": assessment.get("input_fingerprint"),
                    "assessment": assessment,
                    "provenance": provenance,
                },
            )
            # 记录版本摘要（取首条）
            if not run.get("source_versions"):
                rstore.update(l5_run_id, source_versions_json=ctx.get("source_versions") or {})
        except L5InputError as exc:
            failed += 1
            errors.append(f"{rid}:{exc.code}:{exc.message}")
            rstore.save_record(
                l5_run_id,
                {
                    "record_id": rid,
                    "status": "failed",
                    "outcome": "failed",
                    "review_status": "incomplete",
                    "completeness": "incomplete",
                    "error_message": f"{exc.code}: {exc.message}",
                    "assessment": {
                        "schema": "FinalDiagnosisAssessment",
                        "record_id": rid,
                        "outcome": "failed",
                        "error": exc.message,
                        "error_code": exc.code,
                    },
                    "provenance": {},
                },
            )
        except Exception as exc:  # noqa: BLE001
            LOG.exception("l5 record failed %s", rid)
            failed += 1
            errors.append(f"{rid}:unexpected:{exc}")
            rstore.save_record(
                l5_run_id,
                {
                    "record_id": rid,
                    "status": "failed",
                    "outcome": "failed",
                    "review_status": "incomplete",
                    "completeness": "incomplete",
                    "error_message": str(exc),
                    "assessment": {"schema": "FinalDiagnosisAssessment", "record_id": rid, "outcome": "failed"},
                    "provenance": {},
                },
            )
        rstore.update(
            l5_run_id,
            processed_record_count=ok + partial + failed,
            succeeded_record_count=ok,
            partial_record_count=partial,
            failed_record_count=failed,
        )

    final_status = "completed"
    if failed and not ok and not partial:
        final_status = "failed"
    elif failed or partial:
        final_status = "partial"

    run = rstore.update(
        l5_run_id,
        status=final_status,
        finished_at=_now(),
        error_summary="; ".join(errors[:8]) if errors else None,
        succeeded_record_count=ok,
        partial_record_count=partial,
        failed_record_count=failed,
        processed_record_count=len(targets),
    )
    return {"l5_run": run, "reused": False}


def run_l5(
    *,
    diagnosis_run_id: str | None = None,
    analysis_run_id: str | None = None,
    l4_run_id: str | None = None,
    l3_reasoning_run_id: str | None = None,
    l4_reasoning_run_id: str | None = None,
    enrichment_run_id: str | None = None,
    record_ids: list[str] | None = None,
    force_new: bool = False,
    max_records: int | None = None,
) -> dict[str, Any]:
    enq = enqueue_l5_run(
        diagnosis_run_id=diagnosis_run_id,
        analysis_run_id=analysis_run_id,
        l4_run_id=l4_run_id,
        l3_reasoning_run_id=l3_reasoning_run_id,
        l4_reasoning_run_id=l4_reasoning_run_id,
        enrichment_run_id=enrichment_run_id,
        record_ids=record_ids,
        force_new=force_new,
        max_records=max_records,
    )
    result = execute_l5_run(enq["l5_run"]["l5_run_id"])
    return {**result, "created": enq["created"], "selected_record_ids": enq["selected_record_ids"]}
