"""Handoff / DiagnosisRun / Workflow API。"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from l2.handoff.orchestrator import ORCHESTRATOR
from l2.handoff.store import HANDOFF_STORE
from l2.management.errors import ManagementError

router = APIRouter(tags=["l1-l2-handoff"])


class HandoffActionBody(BaseModel):
    item_id: str | None = Field(default=None)


def _request_id(request: Request) -> str:
    incoming = request.headers.get("x-request-id")
    return incoming.strip() if incoming and incoming.strip() else uuid.uuid4().hex


def _error(
    code: str,
    message: str,
    status_code: int,
    request_id: str,
    details: dict | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error_code": code,
            "message": message,
            "details": details or {},
            "request_id": request_id,
        },
    )


@router.get("/api/diagnosis/workflows/{dataset_id}")
def get_workflow(dataset_id: str, request: Request):
    request_id = _request_id(request)
    try:
        body = ORCHESTRATOR.workflow_for_dataset(dataset_id)
        body["request_id"] = request_id
        return body
    except Exception as exc:  # noqa: BLE001
        return _error("WORKFLOW_ERROR", str(exc), 500, request_id)


@router.post("/api/diagnosis/workflows/{dataset_id}/resume-l2")
def resume_l2(dataset_id: str, request: Request):
    request_id = _request_id(request)
    try:
        job = ORCHESTRATOR.resume_dataset(dataset_id)
        return {"request_id": request_id, "handoff": job}
    except KeyError as exc:
        return _error("DATASET_NOT_READY", str(exc), 404, request_id)
    except Exception as exc:  # noqa: BLE001
        return _error("RESUME_FAILED", str(exc), 500, request_id)


@router.get("/l2/handoff/{handoff_id}")
def get_handoff(handoff_id: str, request: Request):
    request_id = _request_id(request)
    try:
        job = HANDOFF_STORE.get_job(handoff_id)
        return {"request_id": request_id, **job}
    except KeyError:
        return _error("HANDOFF_NOT_FOUND", f"衔接任务不存在：{handoff_id}", 404, request_id)


@router.post("/l2/handoff/{handoff_id}/train")
def confirm_train(
    handoff_id: str,
    request: Request,
    body: HandoffActionBody | None = None,
):
    request_id = _request_id(request)
    try:
        item_id = body.item_id if body else None
        job = ORCHESTRATOR.confirm_train(handoff_id, item_id=item_id)
        return {"request_id": request_id, "handoff": job}
    except ManagementError as exc:
        return _error(exc.error_code, exc.message, exc.status_code, request_id, exc.details)
    except KeyError as exc:
        return _error("HANDOFF_NOT_FOUND", str(exc), 404, request_id)


@router.post("/l2/handoff/{handoff_id}/skip-training")
def skip_training(
    handoff_id: str,
    request: Request,
    body: HandoffActionBody | None = None,
):
    request_id = _request_id(request)
    try:
        item_id = body.item_id if body else None
        job = ORCHESTRATOR.skip_training(handoff_id, item_id=item_id)
        return {"request_id": request_id, "handoff": job}
    except KeyError as exc:
        return _error("HANDOFF_NOT_FOUND", str(exc), 404, request_id)


@router.get("/l2/diagnosis-runs/{run_id}")
def get_diagnosis_run(run_id: str, request: Request):
    request_id = _request_id(request)
    try:
        run = HANDOFF_STORE.get_diagnosis_run(run_id)
        return {"request_id": request_id, **run}
    except KeyError:
        return _error("DIAGNOSIS_RUN_NOT_FOUND", f"诊断批次不存在：{run_id}", 404, request_id)


@router.get("/l2/diagnosis-runs/{run_id}/routing-summary")
def get_diagnosis_routing_summary(run_id: str, request: Request):
    request_id = _request_id(request)
    try:
        summary = HANDOFF_STORE.get_routing_summary(run_id)
        return {"request_id": request_id, **summary}
    except KeyError:
        return _error("DIAGNOSIS_RUN_NOT_FOUND", f"诊断批次不存在：{run_id}", 404, request_id)


@router.get("/l2/diagnosis-runs/{run_id}/records")
def get_diagnosis_records(
    run_id: str,
    request: Request,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    screening_result: str | None = Query(default=None),
):
    request_id = _request_id(request)
    try:
        HANDOFF_STORE.get_diagnosis_run(run_id)
    except KeyError:
        return _error("DIAGNOSIS_RUN_NOT_FOUND", f"诊断批次不存在：{run_id}", 404, request_id)
    body: dict[str, Any] = HANDOFF_STORE.list_diagnosis_records(
        run_id,
        offset=offset,
        limit=limit,
        screening_result=screening_result,
    )
    body["request_id"] = request_id
    body["diagnosis_run_id"] = run_id
    return body
