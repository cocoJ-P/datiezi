"""L4 FastAPI 路由。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from l4 import service

router = APIRouter(prefix="/l4", tags=["l4"])


class CreateL4RunBody(BaseModel):
    analysis_run_id: str
    record_ids: list[str] | None = None
    force_new: bool = False
    max_records: int | None = Field(default=None, ge=1, le=500)
    reasoning_run_id: str | None = None
    enrichment_run_id: str | None = None


@router.get("/status")
def l4_status() -> dict[str, Any]:
    return service.status()


@router.get("/overview")
def l4_overview(analysis_run_id: str | None = None) -> dict[str, Any]:
    return service.overview(analysis_run_id)


@router.post("/runs")
def create_run(body: CreateL4RunBody) -> dict[str, Any]:
    try:
        return service.create_run(
            analysis_run_id=body.analysis_run_id,
            record_ids=body.record_ids,
            force_new=body.force_new,
            max_records=body.max_records,
            reasoning_run_id=body.reasoning_run_id,
            enrichment_run_id=body.enrichment_run_id,
        )
    except service.L4ServiceError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"error_code": exc.code, "message": exc.message, "details": exc.details},
        ) from exc


@router.get("/runs/{l4_run_id}")
def get_run(l4_run_id: str) -> dict[str, Any]:
    try:
        return service.get_run(l4_run_id)
    except service.L4ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"error_code": exc.code, "message": exc.message}) from exc


@router.get("/by-analysis/{analysis_run_id}")
def by_analysis(analysis_run_id: str) -> dict[str, Any]:
    return service.get_by_analysis(analysis_run_id)


@router.get("/runs/{l4_run_id}/results")
def list_results(
    l4_run_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    record_id: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    try:
        return service.list_results(l4_run_id, offset=offset, limit=limit, record_id=record_id, status=status)
    except service.L4ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"error_code": exc.code, "message": exc.message}) from exc


@router.get("/runs/{l4_run_id}/records/{record_id}")
def get_record(l4_run_id: str, record_id: str) -> dict[str, Any]:
    try:
        return service.get_record(l4_run_id, record_id)
    except service.L4ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"error_code": exc.code, "message": exc.message}) from exc


@router.get("/mapping")
def mapping(equipment_id: str | None = None, equipment_type: str | None = None) -> dict[str, Any]:
    return service.mapping_lookup(equipment_id, equipment_type)
