"""L5 FastAPI 路由。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from l5 import service
from l5.alerts import service as alert_service

router = APIRouter(prefix="/l5", tags=["l5"])


class CreateL5RunBody(BaseModel):
    diagnosis_run_id: str | None = None
    analysis_run_id: str | None = None
    l4_run_id: str | None = None
    l3_reasoning_run_id: str | None = None
    l4_reasoning_run_id: str | None = None
    enrichment_run_id: str | None = None
    record_ids: list[str] | None = None
    force_new: bool = False
    max_records: int | None = Field(default=None, ge=1, le=200)


@router.get("/status")
def l5_status() -> dict[str, Any]:
    return service.status()


@router.get("/runs")
def list_runs(offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    return service.list_runs(offset=offset, limit=limit)


@router.post("/runs")
def create_run(body: CreateL5RunBody) -> dict[str, Any]:
    try:
        return service.create_run(
            diagnosis_run_id=body.diagnosis_run_id,
            analysis_run_id=body.analysis_run_id,
            l4_run_id=body.l4_run_id,
            l3_reasoning_run_id=body.l3_reasoning_run_id,
            l4_reasoning_run_id=body.l4_reasoning_run_id,
            enrichment_run_id=body.enrichment_run_id,
            record_ids=body.record_ids,
            force_new=body.force_new,
            max_records=body.max_records,
        )
    except service.L5ServiceError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"error_code": exc.code, "message": exc.message, "details": exc.details},
        ) from exc


@router.get("/runs/{l5_run_id}")
def get_run(l5_run_id: str) -> dict[str, Any]:
    try:
        return service.get_run(l5_run_id)
    except service.L5ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"error_code": exc.code, "message": exc.message}) from exc


@router.get("/runs/{l5_run_id}/records")
def list_records(
    l5_run_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    record_id: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    try:
        return service.list_results(
            l5_run_id, offset=offset, limit=limit, record_id=record_id, status=status
        )
    except service.L5ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"error_code": exc.code, "message": exc.message}) from exc


@router.get("/runs/{l5_run_id}/records/{record_id}")
def get_record(l5_run_id: str, record_id: str) -> dict[str, Any]:
    try:
        return service.get_record(l5_run_id, record_id)
    except service.L5ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"error_code": exc.code, "message": exc.message}) from exc


@router.get("/runs/{l5_run_id}/records/{record_id}/provenance")
def get_provenance(
    l5_run_id: str,
    record_id: str,
    field: str | None = Query(None, description="业务字段英文键，如 fault_type"),
) -> dict[str, Any]:
    try:
        return service.get_provenance(l5_run_id, record_id, field=field)
    except service.L5ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"error_code": exc.code, "message": exc.message}) from exc


# ----- L5b Alerts -----


class CreateAlertRunBody(BaseModel):
    analysis_run_id: str | None = None
    diagnosis_run_id: str | None = None
    l4_run_id: str | None = None
    source_l5_run_id: str | None = None
    record_ids: list[str] | None = None
    force_new: bool = False
    max_records: int | None = Field(default=None, ge=1, le=200)


@router.get("/alerts/status")
def alerts_status() -> dict[str, Any]:
    return alert_service.status()


@router.get("/alerts/catalog")
def alerts_catalog() -> dict[str, Any]:
    return alert_service.catalog_view()


@router.get("/alerts/rules/{rule_id}")
def alert_rule(rule_id: str) -> dict[str, Any]:
    try:
        return alert_service.get_rule_detail(rule_id)
    except alert_service.AlertServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"error_code": exc.code, "message": exc.message}) from exc


@router.post("/alerts/runs")
def create_alert_run(body: CreateAlertRunBody) -> dict[str, Any]:
    try:
        return alert_service.create_run(
            analysis_run_id=body.analysis_run_id,
            diagnosis_run_id=body.diagnosis_run_id,
            l4_run_id=body.l4_run_id,
            source_l5_run_id=body.source_l5_run_id,
            record_ids=body.record_ids,
            force_new=body.force_new,
            max_records=body.max_records,
        )
    except alert_service.AlertServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"error_code": exc.code, "message": exc.message}) from exc


@router.get("/alerts/runs/{alert_run_id}")
def get_alert_run(alert_run_id: str) -> dict[str, Any]:
    try:
        return alert_service.get_run(alert_run_id)
    except alert_service.AlertServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"error_code": exc.code, "message": exc.message}) from exc


@router.get("/alerts/by-l5/{l5_run_id}")
def alerts_by_l5(l5_run_id: str) -> dict[str, Any]:
    return alert_service.get_by_l5(l5_run_id)


@router.get("/alerts/runs/{alert_run_id}/records")
def list_alert_records(
    alert_run_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    try:
        return alert_service.list_records(alert_run_id, offset=offset, limit=limit)
    except alert_service.AlertServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"error_code": exc.code, "message": exc.message}) from exc


@router.get("/alerts/runs/{alert_run_id}/records/{record_id}")
def get_alert_record(alert_run_id: str, record_id: str) -> dict[str, Any]:
    try:
        return alert_service.get_record(alert_run_id, record_id)
    except alert_service.AlertServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"error_code": exc.code, "message": exc.message}) from exc


# ----- L5-P2/P3 Prediction + Synthesis -----

from l5.synthesis import service as synthesis_service


class CreateSynthesisBody(BaseModel):
    l5_run_id: str
    record_ids: list[str] | None = None
    allow_llm_synthesis: bool = False
    force_new: bool = False
    max_records: int = Field(default=5, ge=1, le=20)


@router.get("/prediction/status")
def prediction_status() -> dict[str, Any]:
    try:
        return synthesis_service.prediction_status()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail={"error_code": "prediction_status", "message": str(exc)}) from exc


@router.get("/synthesis/status")
def synthesis_status() -> dict[str, Any]:
    return synthesis_service.status()


@router.post("/synthesis/runs")
def create_synthesis_run(body: CreateSynthesisBody) -> dict[str, Any]:
    try:
        return synthesis_service.run_synthesis_for_l5(
            l5_run_id=body.l5_run_id,
            record_ids=body.record_ids,
            allow_llm_synthesis=body.allow_llm_synthesis,
            force_new=body.force_new,
            max_records=body.max_records,
        )
    except synthesis_service.SynthesisServiceError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"error_code": exc.code, "message": exc.message},
        ) from exc


@router.get("/synthesis/runs/{synthesis_run_id}")
def get_synthesis_run(synthesis_run_id: str) -> dict[str, Any]:
    try:
        return synthesis_service.get_synthesis(synthesis_run_id)
    except synthesis_service.SynthesisServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"error_code": exc.code, "message": exc.message}) from exc


@router.get("/synthesis/runs/{synthesis_run_id}/records")
def list_synthesis_records(
    synthesis_run_id: str,
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    return synthesis_service.list_synthesis_records(synthesis_run_id, limit=limit)


@router.post("/prediction/export-test")
def export_test_predictions(max_per_equipment: int | None = None) -> dict[str, Any]:
    """导出六类无答案测试预测；非独立泛化评测。"""
    from l5.synthesis.export import export_unlabeled_test_predictions

    return export_unlabeled_test_predictions(max_per_equipment=max_per_equipment)
