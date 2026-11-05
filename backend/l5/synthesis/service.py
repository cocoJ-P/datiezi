"""L5-P3 综合服务门面。"""

from __future__ import annotations

from typing import Any

from l5.input_adapter import L5InputError, build_l5_input_context
from l5.prediction.service import L5PredictionService
from l5.store import L5_STORE
from l5.synthesis.orchestrator import content_key_for, synthesize_record
from l5.synthesis.prediction_overlay import try_predict_for_context
from l5.synthesis.store import SYNTHESIS_STORE, SynthesisStore


class SynthesisServiceError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def status() -> dict[str, Any]:
    from l5.prediction.paths import DEFAULT_BUNDLE

    return {
        "module": "l5_synthesis",
        "prediction_bundle_exists": DEFAULT_BUNDLE.is_dir(),
        "default_llm_on_report_view": False,
        "synthesis_agent_on_demand_only": True,
    }


def run_synthesis_for_l5(
    *,
    l5_run_id: str,
    record_ids: list[str] | None = None,
    allow_llm_synthesis: bool = False,
    force_new: bool = False,
    max_records: int = 5,
    store: SynthesisStore | None = None,
) -> dict[str, Any]:
    """对已有 L5Run 记录做综合对照；默认不调用 LLM。"""
    if allow_llm_synthesis is True and max_records > 5:
        raise SynthesisServiceError("budget", "开启 LLM 时 max_records 不得超过 5")
    l5 = L5_STORE.get(l5_run_id)
    page = L5_STORE.list_records(l5_run_id, offset=0, limit=200)
    items = page.get("items") or []
    if record_ids:
        want = set(record_ids)
        targets = [i["record_id"] for i in items if i["record_id"] in want]
    else:
        targets = [i["record_id"] for i in items][: max(1, min(max_records, 20))]
    if not targets:
        raise SynthesisServiceError("no_records", "没有可综合的记录")

    key = content_key_for(targets, l5_run_id=l5_run_id, allow_llm=allow_llm_synthesis)
    astore = store or SYNTHESIS_STORE
    run, created = astore.create_or_get(
        content_key=key,
        meta={
            "source_l5_run_id": l5_run_id,
            "diagnosis_run_id": l5.get("diagnosis_run_id"),
            "analysis_run_id": l5.get("analysis_run_id"),
            "l4_run_id": l5.get("l4_run_id"),
            "l3_reasoning_run_id": l5.get("l3_reasoning_run_id"),
            "l4_reasoning_run_id": l5.get("l4_reasoning_run_id"),
            "record_ids": targets,
            "allow_llm_synthesis": allow_llm_synthesis,
            "prediction_run_ref": "L5P-v1",
        },
        force_new=force_new,
    )
    if run.get("status") == "completed" and not force_new and not created:
        return {"synthesis_run": run, "reused": True, "created": False}

    sid = run["synthesis_run_id"]
    astore.update(sid, status="running")
    # 复用已有 ReasoningRun（不默认重跑 LLM）
    l3_rid = l5.get("l3_reasoning_run_id")
    l4_rid = l5.get("l4_reasoning_run_id")
    if not l3_rid and l5.get("analysis_run_id"):
        try:
            from l3.reasoning.jobs import REASONING_STORE

            hit = REASONING_STORE.get_by_analysis(str(l5.get("analysis_run_id")))
            if hit:
                l3_rid = hit.get("reasoning_run_id")
        except Exception:  # noqa: BLE001
            l3_rid = None
    if not l4_rid and l5.get("l4_run_id"):
        try:
            from l4.reasoning.store import L4_REASONING_STORE

            hit = L4_REASONING_STORE.get_by_l4_run(str(l5.get("l4_run_id")))
            if hit:
                l4_rid = hit.get("reasoning_run_id") or hit.get("l4_reasoning_run_id")
        except Exception:  # noqa: BLE001
            l4_rid = None

    errors = []
    for rid in targets:
        try:
            def _build(with_l3: bool, with_l4: bool) -> dict[str, Any]:
                return build_l5_input_context(
                    record_id=rid,
                    diagnosis_run_id=l5.get("diagnosis_run_id"),
                    analysis_run_id=l5.get("analysis_run_id"),
                    l4_run_id=l5.get("l4_run_id"),
                    l3_reasoning_run_id=l3_rid if with_l3 else None,
                    l4_reasoning_run_id=l4_rid if with_l4 else None,
                )

            try:
                ctx = _build(True, True)
            except L5InputError as exc:
                # 单条缺少 Reasoning 记录时降级，不伪造 Agent 结论
                if exc.code == "l3_reasoning_not_found":
                    try:
                        ctx = _build(False, True)
                    except L5InputError as exc2:
                        if exc2.code == "l4_reasoning_not_found":
                            ctx = _build(False, False)
                        else:
                            raise
                elif exc.code == "l4_reasoning_not_found":
                    try:
                        ctx = _build(True, False)
                    except L5InputError as exc2:
                        if exc2.code == "l3_reasoning_not_found":
                            ctx = _build(False, False)
                        else:
                            raise
                else:
                    raise
            pred, pred_err = try_predict_for_context(ctx)
            # 若 L5 记录已有 supervised_prediction，优先使用
            l5rec = next((i for i in items if i["record_id"] == rid), None)
            existing = ((l5rec or {}).get("assessment") or {}).get("supervised_prediction")
            if existing and not pred:
                # 重建最小 prediction 形态
                fields = ((l5rec or {}).get("assessment") or {}).get("fields") or {}
                preds = {}
                from l5.schema import BUSINESS_FIELD_LABELS_ZH

                for en, zh in BUSINESS_FIELD_LABELS_ZH.items():
                    slot = fields.get(en) or {}
                    if slot.get("source_layer") == "l5_prediction" and slot.get("value") is not None:
                        preds[zh] = slot.get("value")
                if preds:
                    pred = {
                        "predictions": preds,
                        "model_versions": {"model_id": existing.get("model_id")},
                        "model_input_fingerprint": existing.get("model_input_fingerprint"),
                    }
            assessment = synthesize_record(
                record_id=rid,
                synthesis_run_id=sid,
                prediction=pred,
                l2_record=ctx.get("l2_record"),
                evidence_bundle=ctx.get("evidence_bundle"),
                l3_reasoning=ctx.get("l3_reasoning_assessment"),
                l4_reasoning=ctx.get("causal_reasoning_assessment"),
                source_run_ids=ctx.get("source_run_ids"),
                allow_llm_synthesis=allow_llm_synthesis,
            )
            if pred_err:
                assessment.setdefault("limitations", []).append(f"prediction_note:{pred_err}")
            astore.save_record(sid, {"record_id": rid, "status": "succeeded", "assessment": assessment})
        except L5InputError as exc:
            errors.append(f"{rid}:{exc.code}")
            astore.save_record(
                sid,
                {
                    "record_id": rid,
                    "status": "failed",
                    "assessment": {"schema": "SynthesisAssessment", "record_id": rid, "error": exc.message},
                },
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{rid}:{exc}")
            astore.save_record(
                sid,
                {
                    "record_id": rid,
                    "status": "failed",
                    "assessment": {"schema": "SynthesisAssessment", "record_id": rid, "error": str(exc)},
                },
            )

    from datetime import datetime, timezone

    finished = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    status_final = "completed" if not errors else ("partial" if len(errors) < len(targets) else "failed")
    run = astore.update(
        sid,
        status=status_final,
        finished_at=finished,
        error_summary="; ".join(errors[:6]) if errors else None,
    )
    return {"synthesis_run": run, "reused": False, "created": created, "errors": errors}


def get_synthesis(synthesis_run_id: str) -> dict[str, Any]:
    try:
        return {"synthesis_run": SYNTHESIS_STORE.get(synthesis_run_id)}
    except KeyError as exc:
        raise SynthesisServiceError("not_found", str(exc), 404) from exc


def list_synthesis_records(synthesis_run_id: str, limit: int = 50) -> dict[str, Any]:
    return SYNTHESIS_STORE.list_records(synthesis_run_id, limit=limit)


def prediction_status() -> dict[str, Any]:
    svc = L5PredictionService()
    return {
        "model_id": svc.manifest.get("model_id"),
        "n_models": svc.manifest.get("n_models"),
        "equipment_categories": svc.manifest.get("equipment_categories"),
        "bundle_dir": str(svc.bundle_dir),
    }
