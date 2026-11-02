"""L2 Management FastAPI 路由。"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import JSONResponse

from l2.management.errors import ManagementError
from l2.management.schemas import RollbackRequest, TrainJobCreateRequest
from l2.management.service import MANAGEMENT_SERVICE

router = APIRouter(prefix="/l2/management", tags=["l2-management"])


def _request_id(request: Request) -> str:
    incoming = request.headers.get("x-request-id")
    return incoming.strip() if incoming and incoming.strip() else uuid.uuid4().hex


def _error(exc: ManagementError, request_id: str) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error_code": exc.error_code,
            "message": exc.message,
            "details": exc.details,
            "request_id": request_id,
        },
    )


def _auth_write(token: str | None) -> None:
    MANAGEMENT_SERVICE.require_token(token)


@router.get("/equipment-types")
def list_equipment_types():
    return MANAGEMENT_SERVICE.list_equipment_types_api()


@router.get("/equipment-types/{equipment_type}")
def get_equipment_type(equipment_type: str, request: Request):
    request_id = _request_id(request)
    try:
        return MANAGEMENT_SERVICE.get_equipment_type_api(equipment_type)
    except ManagementError as exc:
        return _error(exc, request_id)


@router.get("/datasets")
def list_datasets():
    return MANAGEMENT_SERVICE.list_datasets()


@router.post("/train/jobs")
def create_train_job(
    payload: TrainJobCreateRequest,
    request: Request,
    x_l2_management_token: str | None = Header(default=None, alias="X-L2-Management-Token"),
):
    request_id = _request_id(request)
    try:
        _auth_write(x_l2_management_token)
        return MANAGEMENT_SERVICE.submit_train_job(
            equipment_type=payload.equipment_type,
            dataset_id=payload.dataset_id,
            prepared_dataset_id=payload.prepared_dataset_id,
        )
    except ManagementError as exc:
        return _error(exc, request_id)


@router.get("/train/jobs")
def list_train_jobs(
    status: str | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
):
    return MANAGEMENT_SERVICE.list_train_jobs(status=status, offset=offset, limit=limit)


@router.get("/train/jobs/{job_id}")
def get_train_job(job_id: str, request: Request):
    request_id = _request_id(request)
    try:
        return MANAGEMENT_SERVICE.get_train_job(job_id)
    except ManagementError as exc:
        return _error(exc, request_id)


@router.get("/train/jobs/{job_id}/logs")
def get_train_job_logs(
    job_id: str,
    request: Request,
    max_lines: int = Query(default=200, ge=1, le=2000),
):
    request_id = _request_id(request)
    try:
        return MANAGEMENT_SERVICE.get_train_job_logs(job_id, max_lines=max_lines)
    except ManagementError as exc:
        return _error(exc, request_id)


@router.get("/models")
def list_models(
    equipment_type: str | None = Query(default=None),
    status: str | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
):
    return MANAGEMENT_SERVICE.list_models_api(
        equipment_type=equipment_type,
        status=status,
        offset=offset,
        limit=limit,
    )


@router.post("/models/rollback")
def rollback_model_api(
    payload: RollbackRequest,
    request: Request,
    x_l2_management_token: str | None = Header(default=None, alias="X-L2-Management-Token"),
):
    request_id = _request_id(request)
    try:
        _auth_write(x_l2_management_token)
        return MANAGEMENT_SERVICE.rollback(
            equipment_type=payload.equipment_type,
            target_model_bundle_id=payload.target_model_bundle_id,
        )
    except ManagementError as exc:
        return _error(exc, request_id)


@router.get("/models/{model_bundle_id}")
def get_model(model_bundle_id: str, request: Request):
    request_id = _request_id(request)
    try:
        return MANAGEMENT_SERVICE.get_model_detail(model_bundle_id)
    except ManagementError as exc:
        return _error(exc, request_id)


@router.post("/models/{model_bundle_id}/activate")
def activate_model_api(
    model_bundle_id: str,
    request: Request,
    x_l2_management_token: str | None = Header(default=None, alias="X-L2-Management-Token"),
):
    request_id = _request_id(request)
    try:
        _auth_write(x_l2_management_token)
        return MANAGEMENT_SERVICE.activate(model_bundle_id)
    except ManagementError as exc:
        return _error(exc, request_id)


@router.get("/active")
def get_active(equipment_type: str | None = Query(default=None)):
    if equipment_type is None:
        return MANAGEMENT_SERVICE.active_status_all()
    return MANAGEMENT_SERVICE.active_status(equipment_type)


@router.get("/events")
def get_events(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
):
    return MANAGEMENT_SERVICE.list_events_api(offset=offset, limit=limit)


@router.post("/bootstrap")
def bootstrap_models(
    request: Request,
    x_l2_management_token: str | None = Header(default=None, alias="X-L2-Management-Token"),
):
    """一次性登记本地可信模型包（不激活）。"""
    request_id = _request_id(request)
    try:
        _auth_write(x_l2_management_token)
        return MANAGEMENT_SERVICE.bootstrap_models()
    except ManagementError as exc:
        return _error(exc, request_id)
