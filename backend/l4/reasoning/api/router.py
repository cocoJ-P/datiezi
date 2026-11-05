"""L4 Reasoning FastAPI 路由。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from l4.reasoning import POLICY_VERSION, PROMPT_VERSION, TOOL_REGISTRY_VERSION, __version__
from l4.reasoning.jobs import run_l4_reasoning
from l4.reasoning.store import L4_REASONING_STORE

router = APIRouter(prefix="/l4/reasoning", tags=["l4-reasoning"])


class CreateReasoningBody(BaseModel):
    l4_run_id: str
    record_ids: list[str] | None = None
    force_new: bool = False
    max_records: int | None = Field(default=None, ge=1, le=20)
    dry_run: bool = False


@router.get("/status")
def status() -> dict[str, Any]:
    return {
        "module": __version__,
        "prompt_version": PROMPT_VERSION,
        "tool_registry_version": TOOL_REGISTRY_VERSION,
        "policy_version": POLICY_VERSION,
        "llm_enabled": True,
        "notes": [
            "L4 Reasoning 消费 L4 Core，不覆盖 Core 结果。",
            "root_cause_confirmation 恒为 unconfirmed。",
            "按需运行，默认限量记录。",
        ],
    }


@router.post("/runs")
def create_run(body: CreateReasoningBody) -> dict[str, Any]:
    try:
        return run_l4_reasoning(
            body.l4_run_id,
            record_ids=body.record_ids,
            force_new=body.force_new,
            max_records=body.max_records,
            dry_run=body.dry_run,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail={"error_code": "l4_reasoning_error", "message": str(exc)}) from exc


@router.get("/runs/{reasoning_run_id}")
def get_run(reasoning_run_id: str) -> dict[str, Any]:
    try:
        return {"reasoning_run": L4_REASONING_STORE.get(reasoning_run_id)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail={"error_code": "not_found", "message": str(exc)}) from exc


@router.get("/by-l4/{l4_run_id}")
def by_l4(l4_run_id: str) -> dict[str, Any]:
    return {"reasoning_run": L4_REASONING_STORE.get_by_l4_run(l4_run_id), "l4_run_id": l4_run_id}


@router.get("/runs/{reasoning_run_id}/results")
def list_results(
    reasoning_run_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    record_id: str | None = None,
) -> dict[str, Any]:
    try:
        L4_REASONING_STORE.get(reasoning_run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail={"error_code": "not_found", "message": str(exc)}) from exc
    return L4_REASONING_STORE.list_records(
        reasoning_run_id, offset=offset, limit=limit, record_id=record_id
    )


@router.get("/runs/{reasoning_run_id}/records/{record_id}")
def get_record(reasoning_run_id: str, record_id: str) -> dict[str, Any]:
    try:
        return L4_REASONING_STORE.get_record(reasoning_run_id, record_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail={"error_code": "not_found", "message": str(exc)}) from exc
