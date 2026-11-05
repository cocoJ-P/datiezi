"""L3 Reasoning FastAPI 路由。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from l3.reasoning import PROMPT_VERSION, REASONING_POLICY_VERSION, TOOL_REGISTRY_VERSION, __version__
from l3.reasoning.jobs import REASONING_STORE, run_reasoning_for_analysis
from l3.reasoning.policy import policy_config
from l3.reasoning.tools import REGISTERED, TOOLS

router = APIRouter(prefix="/l3/reasoning", tags=["l3-reasoning"])


class CreateReasoningBody(BaseModel):
    analysis_run_id: str
    record_ids: list[str] | None = None
    automatic_selection: bool = True
    manual_request: bool = False
    force_new: bool = False
    dry_run: bool = False
    limit_scan: int = Field(200, ge=1, le=2000)


@router.get("/status")
def reasoning_status() -> dict[str, Any]:
    return {
        "module_version": __version__,
        "prompt_version": PROMPT_VERSION,
        "tool_registry_version": TOOL_REGISTRY_VERSION,
        "reasoning_policy_version": REASONING_POLICY_VERSION,
        "policy": policy_config(),
        "tools": sorted(REGISTERED),
        "tool_count": len(TOOLS),
        "note": "Core 全量；Reasoning 按需。不覆盖 EvidenceBundle。",
    }


@router.post("/runs")
def create_reasoning_run(body: CreateReasoningBody) -> dict[str, Any]:
    try:
        return run_reasoning_for_analysis(
            body.analysis_run_id,
            record_ids=body.record_ids,
            automatic_selection=body.automatic_selection,
            manual_request=body.manual_request or bool(body.record_ids),
            force_new=body.force_new,
            dry_run=body.dry_run,
            limit_scan=body.limit_scan,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail={"error_code": "NOT_FOUND", "message": str(exc)}) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail={"error_code": "REASONING_FAILED", "message": str(exc)}) from exc


@router.get("/runs/{reasoning_run_id}")
def get_reasoning_run(reasoning_run_id: str) -> dict[str, Any]:
    try:
        return REASONING_STORE.get(reasoning_run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail={"error_code": "NOT_FOUND", "message": str(exc)}) from exc


@router.get("/runs/{reasoning_run_id}/results")
def list_reasoning_results(
    reasoning_run_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    try:
        REASONING_STORE.get(reasoning_run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail={"error_code": "NOT_FOUND", "message": str(exc)}) from exc
    return REASONING_STORE.list_records(reasoning_run_id, offset=offset, limit=limit)


@router.get("/runs/{reasoning_run_id}/records/{record_id}")
def get_reasoning_record(reasoning_run_id: str, record_id: str) -> dict[str, Any]:
    try:
        return REASONING_STORE.get_record(reasoning_run_id, record_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail={"error_code": "NOT_FOUND", "message": str(exc)}) from exc


@router.get("/by-analysis/{analysis_run_id}")
def by_analysis(analysis_run_id: str) -> dict[str, Any]:
    run = REASONING_STORE.get_by_analysis(analysis_run_id)
    return {"analysis_run_id": analysis_run_id, "reasoning_run": run}
