"""L3 FastAPI 路由。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from l3.api import service

router = APIRouter(prefix="/l3", tags=["l3"])


class CreateAnalysisRunBody(BaseModel):
    diagnosis_run_id: str
    record_ids: list[str] | None = None
    manual_request: bool = False
    force_new: bool = False


@router.post("/analysis-runs")
def create_analysis_run(body: CreateAnalysisRunBody) -> dict[str, Any]:
    try:
        return service.create_analysis_run(
            diagnosis_run_id=body.diagnosis_run_id,
            record_ids=body.record_ids,
            manual_request=body.manual_request,
            force_new=body.force_new,
        )
    except service.L3ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"error_code": exc.code, "message": exc.message, "details": exc.details}) from exc


@router.get("/analysis-runs/{analysis_run_id}")
def get_analysis_run(analysis_run_id: str) -> dict[str, Any]:
    try:
        return service.get_analysis_run(analysis_run_id)
    except service.L3ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"error_code": exc.code, "message": exc.message}) from exc


@router.get("/by-diagnosis/{diagnosis_run_id}")
def get_by_diagnosis(diagnosis_run_id: str) -> dict[str, Any]:
    run = service.get_analysis_run_for_diagnosis(diagnosis_run_id)
    if not run:
        return {"analysis_run": None, "diagnosis_run_id": diagnosis_run_id}
    return {"analysis_run": run, "diagnosis_run_id": diagnosis_run_id}


@router.get("/analysis-runs/{analysis_run_id}/results")
def list_results(
    analysis_run_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    equipment_id: str | None = None,
    record_id: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    try:
        return service.list_results(
            analysis_run_id,
            offset=offset,
            limit=limit,
            equipment_id=equipment_id,
            record_id=record_id,
            status=status,
        )
    except service.L3ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"error_code": exc.code, "message": exc.message}) from exc


@router.get("/analysis-runs/{analysis_run_id}/records/{record_id}")
def get_record(analysis_run_id: str, record_id: str) -> dict[str, Any]:
    try:
        return service.get_record_evidence(analysis_run_id, record_id)
    except service.L3ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"error_code": exc.code, "message": exc.message}) from exc


@router.post("/analysis-runs/{analysis_run_id}/reanalyze")
def reanalyze(analysis_run_id: str) -> dict[str, Any]:
    """受控重跑：基于原 DiagnosisRun 创建新 AnalysisRun，不覆盖旧证据。"""
    try:
        old = service.get_analysis_run(analysis_run_id)
        return service.create_analysis_run(
            diagnosis_run_id=old["source_diagnosis_run_id"],
            force_new=True,
        )
    except service.L3ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"error_code": exc.code, "message": exc.message}) from exc
