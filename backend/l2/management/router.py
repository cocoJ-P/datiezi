"""L2 Management FastAPI 路由。"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from l2.equipment.onboarding.service import ONBOARDING_SERVICE
from l2.management.errors import ManagementError
from l2.management.schemas import (
    OnboardingConfirmRequest,
    OnboardingPrepareRequest,
    OnboardingValidateRequest,
    RollbackRequest,
    TrainJobCreateRequest,
)
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
def create_train_job(payload: TrainJobCreateRequest, request: Request):
    request_id = _request_id(request)
    try:
        return MANAGEMENT_SERVICE.submit_train_job(
            equipment_type=payload.equipment_type,
            dataset_id=payload.dataset_id,
            prepared_dataset_id=payload.prepared_dataset_id,
            parent_model_bundle_id=payload.parent_model_bundle_id,
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
def rollback_model_api(payload: RollbackRequest, request: Request):
    request_id = _request_id(request)
    try:
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
def activate_model_api(model_bundle_id: str, request: Request):
    request_id = _request_id(request)
    try:
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
def bootstrap_models(request: Request):
    """一次性登记本地可信模型包（不激活）。"""
    request_id = _request_id(request)
    try:
        return MANAGEMENT_SERVICE.bootstrap_models()
    except ManagementError as exc:
        return _error(exc, request_id)


@router.post("/equipment-onboarding/prepare")
def prepare_equipment_onboarding(payload: OnboardingPrepareRequest, request: Request):
    request_id = _request_id(request)
    try:
        body = ONBOARDING_SERVICE.prepare(
            dataset_id=payload.dataset_id,
            pd_revision_id=payload.pd_revision_id,
            equipment_group_id=payload.equipment_group_id,
            equipment_category=payload.equipment_category,
        )
        body["request_id"] = request_id
        return body
    except ManagementError as exc:
        return _error(exc, request_id)
    except KeyError as exc:
        return _error(
            ManagementError("PD_NOT_FOUND", f"PD 不存在：{exc}", status_code=404),
            request_id,
        )


@router.get("/equipment-onboarding/{onboarding_id}")
def get_equipment_onboarding(onboarding_id: str, request: Request):
    request_id = _request_id(request)
    try:
        body = ONBOARDING_SERVICE.get(onboarding_id)
        body["request_id"] = request_id
        return body
    except ManagementError as exc:
        return _error(exc, request_id)


@router.post("/equipment-onboarding/{onboarding_id}/validate")
def validate_equipment_onboarding(
    onboarding_id: str,
    request: Request,
    payload: OnboardingValidateRequest | None = None,
):
    request_id = _request_id(request)
    try:
        overrides = payload.model_dump(exclude_none=True) if payload else {}
        body = ONBOARDING_SERVICE.validate(onboarding_id, overrides=overrides)
        body["request_id"] = request_id
        return body
    except ManagementError as exc:
        return _error(exc, request_id)


@router.post("/equipment-onboarding/{onboarding_id}/confirm")
def confirm_equipment_onboarding(
    onboarding_id: str,
    request: Request,
    payload: OnboardingConfirmRequest | None = None,
):
    request_id = _request_id(request)
    try:
        data = payload.model_dump(exclude_none=True) if payload else {}
        body = ONBOARDING_SERVICE.confirm(
            onboarding_id,
            overrides={
                k: data[k]
                for k in ("equipment_type", "display_name", "normal_label", "selected_feature_ids")
                if k in data
            },
            continue_handoff=bool(data.get("continue_handoff", True)),
            handoff_id=data.get("handoff_id"),
            handoff_item_id=data.get("handoff_item_id"),
        )
        body["request_id"] = request_id
        return body
    except ManagementError as exc:
        return _error(exc, request_id)
