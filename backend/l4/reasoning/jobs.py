"""L4ReasoningRun 创建与执行。"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

from l4.reasoning import POLICY_VERSION, PROMPT_VERSION, TOOL_REGISTRY_VERSION
from l4.reasoning.input_adapter import L4ReasoningInputError, build_l4_reasoning_input
from l4.reasoning.orchestrator import run_llm_tool_loop
from l4.reasoning.policy import policy_config, select_records
from l4.reasoning.store import L4_REASONING_STORE, L4ReasoningStore
from l4.store import L4_STORE, L4Store

LOG = logging.getLogger("l4.reasoning.jobs")


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def content_key_for(
    l4_run_id: str,
    record_ids: list[str],
    *,
    prompt_version: str,
    causal_knowledge_version: str,
    evidence_match_version: str,
) -> str:
    ids = ",".join(sorted(record_ids))
    raw = f"{l4_run_id}|{ids}|{prompt_version}|{causal_knowledge_version}|{evidence_match_version}|{TOOL_REGISTRY_VERSION}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def enqueue_from_l4(
    l4_run_id: str,
    *,
    record_ids: list[str] | None = None,
    force_new: bool = False,
    max_records: int | None = None,
    dry_run: bool = False,
    store: L4ReasoningStore | None = None,
    l4_store: L4Store | None = None,
) -> dict[str, Any]:
    cfg = policy_config()
    rstore = store or L4_REASONING_STORE
    lstore = l4_store or L4_STORE
    try:
        l4_run = lstore.get(l4_run_id)
    except KeyError as exc:
        raise RuntimeError(f"L4Run 不存在：{l4_run_id}") from exc

    limit = max_records if max_records is not None else cfg["max_records"]
    limit = max(1, min(int(limit), 20))

    if record_ids:
        targets = [str(r) for r in record_ids][:limit]
    else:
        page = lstore.list_records(l4_run_id, offset=0, limit=200)
        if cfg["auto_select_enabled"]:
            targets = select_records(l4_run, page.get("items") or [], max_records=limit)
        else:
            targets = [str(i["record_id"]) for i in (page.get("items") or [])][:limit]

    if not targets:
        raise RuntimeError("没有可推理的记录（请显式传入 record_ids）")

    # 取首条评估绑定版本
    sample = lstore.get_record(l4_run_id, targets[0])
    assessment = sample.get("assessment") or {}
    causal_v = assessment.get("causal_knowledge_version") or l4_run.get("causal_knowledge_version") or ""
    rules_v = assessment.get("rules_version") or ""
    key = content_key_for(
        l4_run_id,
        targets,
        prompt_version=PROMPT_VERSION,
        causal_knowledge_version=str(causal_v),
        evidence_match_version=str(rules_v),
    )
    meta = {
        "source_l4_run_id": l4_run_id,
        "source_analysis_run_id": l4_run.get("source_analysis_run_id"),
        "llm_model": "deepseek-flash",
        "prompt_version": PROMPT_VERSION,
        "tool_registry_version": TOOL_REGISTRY_VERSION,
        "policy_version": POLICY_VERSION,
        "selected_record_count": len(targets),
        "selection": {"record_ids": targets, "max_records": limit, "dry_run": dry_run},
    }
    run, created = rstore.create_or_get(content_key=key, meta=meta, force_new=force_new)
    return {"reasoning_run": run, "created": created, "selected_record_ids": targets, "dry_run": dry_run}


def execute_reasoning_run(
    reasoning_run_id: str,
    *,
    dry_run: bool | None = None,
    store: L4ReasoningStore | None = None,
) -> dict[str, Any]:
    rstore = store or L4_REASONING_STORE
    run = rstore.get(reasoning_run_id)
    if run.get("status") == "completed":
        return run

    selection = run.get("selection") or {}
    record_ids = list(selection.get("record_ids") or [])
    use_dry = dry_run if dry_run is not None else bool(selection.get("dry_run"))
    l4_run_id = run["source_l4_run_id"]
    rstore.update(reasoning_run_id, status="running", started_at=_now())

    succeeded = 0
    failed = 0
    validation_failed = 0
    token_acc = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "llm_call_count": 0}
    errors: list[str] = []

    for record_id in record_ids:
        try:
            ctx = build_l4_reasoning_input(l4_run_id=l4_run_id, record_id=record_id)
            result = run_llm_tool_loop(ctx, reasoning_run_id=reasoning_run_id, dry_run=use_dry)
            assessment = result.get("assessment") or {}
            audit = result.get("audit") or assessment.get("audit") or {}
            for k in ("prompt_tokens", "completion_tokens", "total_tokens", "llm_call_count"):
                token_acc[k] = int(token_acc.get(k) or 0) + int(audit.get(k) or 0)

            outcome = assessment.get("outcome")
            status = "succeeded"
            if outcome == "validation_failed":
                status = "validation_failed"
                validation_failed += 1
            elif assessment.get("status") == "failed":
                status = "failed"
                failed += 1
            else:
                succeeded += 1

            rstore.save_record(
                reasoning_run_id,
                {
                    "record_id": record_id,
                    "status": status,
                    "outcome": outcome,
                    "input_fingerprint": ctx.get("input_fingerprint"),
                    "assessment": assessment,
                    "tool_history": result.get("tool_history") or [],
                },
            )
        except L4ReasoningInputError as exc:
            failed += 1
            errors.append(f"{record_id}: {exc.message}")
            rstore.save_record(
                reasoning_run_id,
                {
                    "record_id": record_id,
                    "status": "failed",
                    "outcome": "llm_failed_fallback",
                    "assessment": {"schema": "CausalReasoningAssessment", "outcome": "llm_failed_fallback", "error": exc.message},
                    "error_message": exc.message,
                },
            )
        except Exception as exc:  # noqa: BLE001
            failed += 1
            errors.append(f"{record_id}: {exc}")
            LOG.exception("L4 reasoning failed %s", record_id)
            rstore.save_record(
                reasoning_run_id,
                {
                    "record_id": record_id,
                    "status": "failed",
                    "outcome": "llm_failed_fallback",
                    "assessment": {"schema": "CausalReasoningAssessment", "outcome": "llm_failed_fallback", "error": str(exc)},
                    "error_message": str(exc),
                },
            )

    return rstore.update(
        reasoning_run_id,
        status="completed",
        processed_record_count=len(record_ids),
        succeeded_record_count=succeeded,
        failed_record_count=failed,
        validation_failed_record_count=validation_failed,
        token_usage_json=token_acc,
        error_summary="; ".join(errors[:5]) if errors else None,
        finished_at=_now(),
    )


def run_l4_reasoning(
    l4_run_id: str,
    *,
    record_ids: list[str] | None = None,
    force_new: bool = False,
    max_records: int | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    enqueued = enqueue_from_l4(
        l4_run_id,
        record_ids=record_ids,
        force_new=force_new,
        max_records=max_records,
        dry_run=dry_run,
    )
    run = enqueued["reasoning_run"]
    if enqueued["created"] or run.get("status") in {"queued", "interrupted"}:
        run = execute_reasoning_run(run["reasoning_run_id"], dry_run=dry_run)
    return {
        "reasoning_run": run,
        "created": enqueued["created"],
        "selected_record_ids": enqueued["selected_record_ids"],
    }
