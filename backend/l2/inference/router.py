"""L2 Inference FastAPI 路由。"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from l2.inference.config import SERVICE_VERSION
from l2.inference.model_manager import MODEL_MANAGER, ModelNotReadyError
from l2.inference.schemas import BatchDiagnoseRequest, DatasetDiagnoseRequest, DiagnoseRequest
from l2.inference.service import INFERENCE_SERVICE
from l2.inference.validators import InferenceValidationError

LOG = logging.getLogger("l2.inference")
router = APIRouter(prefix="/l2", tags=["l2-inference"])


def _error(error_code: str, message: str, status_code: int, details: dict[str, Any] | None = None, request_id: str | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error_code": error_code,
            "message": message,
            "details": details or {},
            "request_id": request_id,
        },
    )


def _request_id(request: Request) -> str:
    incoming = request.headers.get("x-request-id")
    return incoming.strip() if incoming and incoming.strip() else uuid.uuid4().hex


@router.get("/health")
def l2_health():
    body = MODEL_MANAGER.health()
    body["service_version"] = SERVICE_VERSION
    status = 200 if body["model_loaded"] else 503
    return JSONResponse(status_code=status, content=body)


@router.get("/model")
def l2_model(request: Request, equipment_type: str | None = None):
    request_id = _request_id(request)
    try:
        return MODEL_MANAGER.model_info(equipment_type)
    except ModelNotReadyError as exc:
        return _error(exc.error_code, str(exc), 503, request_id=request_id)


@router.post("/diagnose")
def l2_diagnose(payload: DiagnoseRequest, request: Request):
    request_id = _request_id(request)
    try:
        result = INFERENCE_SERVICE.diagnose_one(
            equipment_type=payload.equipment_type,
            features=payload.features,
            record_id=payload.record_id,
        )
        result["request_id"] = request_id
        return result
    except ModelNotReadyError as exc:
        return _error(exc.error_code, str(exc), 503, request_id=request_id)
    except InferenceValidationError as exc:
        return _error(exc.error_code, exc.message, exc.status_code, exc.details, request_id=request_id)
    except Exception:
        LOG.exception("L2 diagnose failed request_id=%s", request_id)
        return _error("INTERNAL_ERROR", "L2 推理服务内部错误。", 500, request_id=request_id)


@router.post("/diagnose/batch")
def l2_diagnose_batch(payload: BatchDiagnoseRequest, request: Request):
    request_id = _request_id(request)
    try:
        records = [item.model_dump() for item in payload.records]
        result = INFERENCE_SERVICE.diagnose_batch(equipment_type=payload.equipment_type, records=records)
        result["request_id"] = request_id
        return result
    except ModelNotReadyError as exc:
        return _error(exc.error_code, str(exc), 503, request_id=request_id)
    except InferenceValidationError as exc:
        return _error(exc.error_code, exc.message, exc.status_code, exc.details, request_id=request_id)
    except Exception:
        LOG.exception("L2 batch diagnose failed request_id=%s", request_id)
        return _error("INTERNAL_ERROR", "L2 批量推理服务内部错误。", 500, request_id=request_id)


@router.post("/datasets/{prepared_dataset_id}/diagnose")
def l2_diagnose_prepared_dataset(
    prepared_dataset_id: str,
    payload: DatasetDiagnoseRequest,
    request: Request,
):
    request_id = _request_id(request)
    try:
        if "/" in prepared_dataset_id or "\\" in prepared_dataset_id or ".." in prepared_dataset_id:
            return _error("INVALID_DATASET_ID", "非法数据集标识。", 400, request_id=request_id)
        result = INFERENCE_SERVICE.diagnose_prepared_records(
            prepared_dataset_id=prepared_dataset_id,
            record_ids=payload.record_ids,
            revision_id=payload.revision_id,
        )
        result["request_id"] = request_id
        return result
    except ModelNotReadyError as exc:
        return _error(exc.error_code, str(exc), 503, request_id=request_id)
    except InferenceValidationError as exc:
        return _error(exc.error_code, exc.message, exc.status_code, exc.details, request_id=request_id)
    except Exception:
        LOG.exception("L2 prepared dataset diagnose failed request_id=%s", request_id)
        return _error("INTERNAL_ERROR", "L2 数据集诊断内部错误。", 500, request_id=request_id)
