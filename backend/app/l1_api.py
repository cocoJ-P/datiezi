"""L1 数据接入与区域识别接口。上传使用原始字节，文件名放在查询参数里。"""

from __future__ import annotations

import json

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

from app.services.l1_grouping import (
    apply_grouping,
    equipment_fields,
    grouping_summary,
    list_equipment_groups,
    list_group_rows,
    list_grouping_rows,
    list_ungrouped_rows,
    preview_grouping,
)
from app.services.l1_regions import apply_regions, list_region_rows, list_regions, preview_regions, suggest_regions
from app.services.l1_schema.service import (
    apply_mapping,
    diff_schema_run,
    get_mappings,
    get_schema_run,
    get_schema_template,
    list_schema_rows,
    list_schema_templates,
    preview_mapping,
    suggest_mappings,
    validate_mapping,
)
from app.services.l1_standardization.service import (
    apply_standardization,
    get_standardization_config,
    get_standardization_run,
    get_standardization_summary,
    list_standardization_diff,
    list_standardization_issues,
    list_standardization_rows,
    preview_standardization,
    validate_standardization,
)
from app.services.l1_prepare import (
    get_prepare_report,
    get_prepare_run,
    get_prepared_dataset,
    latest_prepare,
    list_prepare_artifacts,
    list_prepare_issues,
    list_prepare_stages,
    list_prepared_rows,
    run_prepare,
)
from app.services.l1_store import (
    L1Error,
    TEMPLATE_ID,
    create_dataset,
    get_dataset,
    get_raw_cell,
    list_datasets,
    list_raw_rows,
    list_sheets,
    raw_file,
    stage_catalog,
)


def register_l1_routes(app: FastAPI) -> None:
    @app.get("/api/l1/pipeline")
    def read_pipeline():
        return {"template_id": TEMPLATE_ID, "stages": stage_catalog()}

    @app.post("/api/l1/datasets")
    async def create(request: Request, filename: str, encoding: str | None = None, delimiter: str | None = None):
        media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if media_type != "application/octet-stream":
            return _error("invalid_content_type", "请使用 Content-Type: application/octet-stream 上传原始文件。", 415)
        payload = await request.body()
        try:
            body, status = create_dataset(filename, payload, encoding, delimiter)
        except L1Error as exc:
            return _from_error(exc)
        return JSONResponse(body, status_code=status)

    @app.get("/api/l1/datasets")
    def read_datasets():
        try:
            return list_datasets()
        except L1Error as exc:
            return _from_error(exc)

    @app.get("/api/l1/datasets/{dataset_id}")
    def read_dataset(dataset_id: str):
        try:
            return get_dataset(dataset_id)
        except L1Error as exc:
            return _from_error(exc)

    @app.get("/api/l1/datasets/{dataset_id}/sheets")
    def read_sheets(dataset_id: str):
        try:
            return list_sheets(dataset_id)
        except L1Error as exc:
            return _from_error(exc)

    @app.get("/api/l1/datasets/{dataset_id}/raw-rows")
    def read_rows(
        dataset_id: str,
        request: Request,
        sheet_id: str | None = None,
        offset: int = 0,
        limit: int = 100,
        row: int | None = None,
    ):
        if "offset" in request.query_params and not _integer(request.query_params.get("offset")):
            return _error("invalid_offset", "offset 必须是大于等于 0 的整数。", 422)
        if "limit" in request.query_params and not _integer(request.query_params.get("limit")):
            return _error("invalid_limit", "limit 必须是 1 到 200 的整数。", 422)
        if "row" in request.query_params and not _integer(request.query_params.get("row"), minimum=1):
            return _error("invalid_row", "原始行号必须从 1 开始。", 422)
        try:
            return list_raw_rows(dataset_id, sheet_id, offset, limit, row)
        except L1Error as exc:
            return _from_error(exc)

    @app.get("/api/l1/datasets/{dataset_id}/raw-cells/{cell_id}")
    def read_cell(dataset_id: str, cell_id: str):
        try:
            return get_raw_cell(dataset_id, cell_id)
        except L1Error as exc:
            return _from_error(exc)

    @app.get("/api/l1/datasets/{dataset_id}/regions/suggestions")
    def read_region_suggestions(dataset_id: str):
        try:
            return suggest_regions(dataset_id)
        except L1Error as exc:
            return _from_error(exc)

    @app.post("/api/l1/datasets/{dataset_id}/regions/preview")
    async def preview_region_config(dataset_id: str, request: Request):
        try:
            return preview_regions(dataset_id, await _json_object(request))
        except L1Error as exc:
            return _from_error(exc)

    @app.post("/api/l1/datasets/{dataset_id}/regions/apply")
    async def apply_region_config(dataset_id: str, request: Request):
        try:
            return JSONResponse(apply_regions(dataset_id, await _json_object(request)), status_code=201)
        except L1Error as exc:
            return _from_error(exc)

    @app.get("/api/l1/datasets/{dataset_id}/regions")
    def read_regions(dataset_id: str, run_id: str | None = None):
        try:
            return list_regions(dataset_id, run_id)
        except L1Error as exc:
            return _from_error(exc)

    @app.get("/api/l1/regions/{region_id}/rows")
    def read_region_rows(region_id: str, request: Request, offset: int = 0, limit: int = 100):
        if "offset" in request.query_params and not _integer(request.query_params.get("offset")):
            return _error("invalid_offset", "offset 必须是大于等于 0 的整数。", 422)
        if "limit" in request.query_params and not _integer(request.query_params.get("limit"), minimum=1):
            return _error("invalid_limit", "limit 必须是 1 到 200 的整数。", 422)
        try:
            return list_region_rows(region_id, offset, limit)
        except L1Error as exc:
            return _from_error(exc)

    @app.get("/api/l1/datasets/{dataset_id}/equipment-fields")
    def read_equipment_fields(dataset_id: str):
        try:
            return equipment_fields(dataset_id)
        except L1Error as exc:
            return _from_error(exc)

    @app.post("/api/l1/datasets/{dataset_id}/grouping/preview")
    async def preview_equipment_grouping(dataset_id: str, request: Request):
        try:
            return preview_grouping(dataset_id, await _json_object(request))
        except L1Error as exc:
            return _from_error(exc)

    @app.post("/api/l1/datasets/{dataset_id}/grouping/apply")
    async def apply_equipment_grouping(dataset_id: str, request: Request):
        try:
            return JSONResponse(apply_grouping(dataset_id, await _json_object(request)), status_code=201)
        except L1Error as exc:
            return _from_error(exc)

    @app.get("/api/l1/datasets/{dataset_id}/equipment-groups")
    def read_equipment_groups(dataset_id: str, request: Request, run_id: str | None = None, offset: int = 0, limit: int = 50):
        if not _page_params(request):
            return _error("invalid_page", "offset 必须大于等于 0，limit 必须是 1 到 200 的整数。", 422)
        try:
            return list_equipment_groups(dataset_id, run_id, offset, limit)
        except L1Error as exc:
            return _from_error(exc)

    @app.get("/api/l1/datasets/{dataset_id}/grouping/summary")
    def read_grouping_summary(dataset_id: str, run_id: str | None = None):
        try:
            return grouping_summary(dataset_id, run_id)
        except L1Error as exc:
            return _from_error(exc)

    @app.get("/api/l1/equipment-groups/{group_id}/rows")
    def read_equipment_group_rows(group_id: str, request: Request, offset: int = 0, limit: int = 100):
        if not _page_params(request):
            return _error("invalid_page", "offset 必须大于等于 0，limit 必须是 1 到 200 的整数。", 422)
        try:
            return list_group_rows(group_id, offset, limit)
        except L1Error as exc:
            return _from_error(exc)

    @app.get("/api/l1/datasets/{dataset_id}/ungrouped-rows")
    def read_ungrouped_rows(dataset_id: str, request: Request, run_id: str | None = None, offset: int = 0, limit: int = 100, reason: str | None = None):
        if not _page_params(request):
            return _error("invalid_page", "offset 必须大于等于 0，limit 必须是 1 到 200 的整数。", 422)
        try:
            return list_ungrouped_rows(dataset_id, run_id, offset, limit, reason)
        except L1Error as exc:
            return _from_error(exc)

    @app.get("/api/l1/datasets/{dataset_id}/grouping-rows")
    def read_grouping_rows(
        dataset_id: str,
        request: Request,
        run_id: str | None = None,
        offset: int = 0,
        limit: int = 100,
        sheet_id: str | None = None,
        region_id: str | None = None,
    ):
        if not _page_params(request):
            return _error("invalid_page", "offset 必须大于等于 0，limit 必须是 1 到 200 的整数。", 422)
        try:
            return list_grouping_rows(dataset_id, run_id, offset, limit, sheet_id, region_id)
        except L1Error as exc:
            return _from_error(exc)

    @app.get("/api/l1/schema/templates")
    def read_schema_templates():
        try:
            return list_schema_templates()
        except L1Error as exc:
            return _from_error(exc)

    @app.get("/api/l1/schema/templates/{template_id}")
    def read_schema_template(template_id: str, version: str | None = None):
        try:
            return get_schema_template(template_id, version)
        except L1Error as exc:
            return _from_error(exc)

    @app.get("/api/l1/datasets/{dataset_id}/schema/suggestions")
    def read_schema_suggestions(dataset_id: str):
        try:
            return suggest_mappings(dataset_id)
        except L1Error as exc:
            return _from_error(exc)

    @app.get("/api/l1/datasets/{dataset_id}/schema/mappings")
    def read_schema_mappings(dataset_id: str, run_id: str | None = None):
        try:
            return get_mappings(dataset_id, run_id)
        except L1Error as exc:
            return _from_error(exc)

    @app.post("/api/l1/datasets/{dataset_id}/schema/preview")
    async def preview_schema(dataset_id: str, request: Request):
        try:
            return preview_mapping(dataset_id, await _json_object(request))
        except L1Error as exc:
            return _from_error(exc)

    @app.post("/api/l1/datasets/{dataset_id}/schema/validate")
    async def validate_schema(dataset_id: str, request: Request):
        try:
            return validate_mapping(dataset_id, await _json_object(request))
        except L1Error as exc:
            return _from_error(exc)

    @app.post("/api/l1/datasets/{dataset_id}/schema/apply")
    async def apply_schema(dataset_id: str, request: Request):
        try:
            body = apply_mapping(dataset_id, await _json_object(request))
        except L1Error as exc:
            return _from_error(exc)
        return JSONResponse(body, status_code=201)

    @app.get("/api/l1/schema-runs/{run_id}")
    def read_schema_run(run_id: str):
        try:
            return get_schema_run(run_id)
        except L1Error as exc:
            return _from_error(exc)

    @app.get("/api/l1/schema-runs/{run_id}/rows")
    def read_schema_rows(run_id: str, request: Request, offset: int = 0, limit: int = 100, region_id: str | None = None, view: str = "output"):
        if not _page_params(request):
            return _error("invalid_page", "offset 必须大于等于 0，limit 必须是 1 到 200 的整数。", 422)
        try:
            return list_schema_rows(run_id, offset, limit, region_id, view)
        except L1Error as exc:
            return _from_error(exc)

    @app.get("/api/l1/schema-runs/{run_id}/diff")
    def read_schema_diff(run_id: str):
        try:
            return diff_schema_run(run_id)
        except L1Error as exc:
            return _from_error(exc)

    @app.get("/api/l1/datasets/{dataset_id}/standardization/config")
    def read_standardization_config(dataset_id: str):
        try:
            return get_standardization_config(dataset_id)
        except L1Error as exc:
            return _from_error(exc)

    @app.post("/api/l1/datasets/{dataset_id}/standardization/preview")
    async def preview_standardization_route(dataset_id: str, request: Request):
        try:
            return preview_standardization(dataset_id, await _json_object(request))
        except L1Error as exc:
            return _from_error(exc)

    @app.post("/api/l1/datasets/{dataset_id}/standardization/validate")
    async def validate_standardization_route(dataset_id: str, request: Request):
        try:
            return validate_standardization(dataset_id, await _json_object(request))
        except L1Error as exc:
            return _from_error(exc)

    @app.post("/api/l1/datasets/{dataset_id}/standardization/apply")
    async def apply_standardization_route(dataset_id: str, request: Request):
        try:
            body = apply_standardization(dataset_id, await _json_object(request))
        except L1Error as exc:
            return _from_error(exc)
        return JSONResponse(body, status_code=201)

    @app.get("/api/l1/standardization-runs/{run_id}")
    def read_standardization_run(run_id: str):
        try:
            return get_standardization_run(run_id)
        except L1Error as exc:
            return _from_error(exc)

    @app.get("/api/l1/standardization-runs/{run_id}/rows")
    def read_standardization_rows(run_id: str, request: Request, offset: int = 0, limit: int = 100, region_id: str | None = None, view: str = "output"):
        if not _page_params(request):
            return _error("invalid_page", "offset 必须大于等于 0，limit 必须是 1 到 200 的整数。", 422)
        try:
            return list_standardization_rows(run_id, offset, limit, region_id, view)
        except L1Error as exc:
            return _from_error(exc)

    @app.get("/api/l1/standardization-runs/{run_id}/diff")
    def read_standardization_diff(run_id: str, request: Request, offset: int = 0, limit: int = 100, region_id: str | None = None):
        if not _page_params(request):
            return _error("invalid_page", "offset 必须大于等于 0，limit 必须是 1 到 200 的整数。", 422)
        try:
            return list_standardization_diff(run_id, offset, limit, region_id)
        except L1Error as exc:
            return _from_error(exc)

    @app.get("/api/l1/standardization-runs/{run_id}/issues")
    def read_standardization_issues(run_id: str, request: Request, offset: int = 0, limit: int = 100, code: str | None = None):
        if not _page_params(request):
            return _error("invalid_page", "offset 必须大于等于 0，limit 必须是 1 到 200 的整数。", 422)
        try:
            return list_standardization_issues(run_id, offset, limit, code)
        except L1Error as exc:
            return _from_error(exc)

    @app.get("/api/l1/standardization-runs/{run_id}/summary")
    def read_standardization_summary(run_id: str):
        try:
            return get_standardization_summary(run_id)
        except L1Error as exc:
            return _from_error(exc)

    @app.post("/api/l1/datasets/{dataset_id}/prepare")
    async def prepare_dataset(dataset_id: str, request: Request):
        try:
            body = run_prepare(dataset_id, await _json_object(request))
        except L1Error as exc:
            return _from_error(exc)
        return JSONResponse(body, status_code=201)

    @app.get("/api/l1/prepare-runs/{run_id}")
    def read_prepare_run(run_id: str):
        try:
            return get_prepare_run(run_id)
        except L1Error as exc:
            return _from_error(exc)

    @app.get("/api/l1/prepare-runs/{run_id}/stages")
    def read_prepare_stages(run_id: str):
        try:
            return list_prepare_stages(run_id)
        except L1Error as exc:
            return _from_error(exc)

    @app.get("/api/l1/prepare-runs/{run_id}/issues")
    def read_prepare_issues(run_id: str, request: Request, offset: int = 0, limit: int = 100, stage: str | None = None, code: str | None = None):
        if not _page_params(request):
            return _error("invalid_page", "offset 必须大于等于 0，limit 必须是 1 到 200 的整数。", 422)
        try:
            return list_prepare_issues(run_id, offset, limit, stage, code)
        except L1Error as exc:
            return _from_error(exc)

    @app.get("/api/l1/prepare-runs/{run_id}/report")
    def read_prepare_report(run_id: str):
        try:
            return get_prepare_report(run_id)
        except L1Error as exc:
            return _from_error(exc)

    @app.get("/api/l1/prepare-runs/{run_id}/artifacts")
    def read_prepare_artifacts(run_id: str):
        try:
            return list_prepare_artifacts(run_id)
        except L1Error as exc:
            return _from_error(exc)

    @app.get("/api/l1/prepared-datasets")
    def read_prepared_datasets():
        """列出 L1 PD-* 及已发布到 L2 的 PED-* 目录摘要。"""
        try:
            from l2.prepared.catalog import list_published_datasets
            from l2.prepared.l1_source import list_l1_prepared_datasets

            l1_items = [
                {
                    "version_id": item.version_id,
                    "dataset_id": item.dataset_id,
                    "original_filename": item.original_filename,
                    "row_count": item.row_count,
                    "feature_count": item.feature_count,
                    "created_at": item.created_at,
                    "readiness": item.readiness,
                    "source_stage": "P8",
                }
                for item in list_l1_prepared_datasets()
            ]
            return {
                "l1_prepared_datasets": l1_items,
                "l2_published_datasets": list_published_datasets(),
            }
        except Exception as exc:  # noqa: BLE001
            return _error("prepared_list_failed", str(exc), 500)

    @app.get("/api/l1/prepared-datasets/{version_id}")
    def read_prepared_dataset(version_id: str):
        try:
            if version_id.startswith("PED-"):
                from l2.prepared.catalog import get_published_dataset

                return get_published_dataset(version_id)
            return get_prepared_dataset(version_id)
        except KeyError:
            return _error("not_found", f"数据集不存在：{version_id}", 404)
        except L1Error as exc:
            return _from_error(exc)

    @app.post("/api/l1/prepared-datasets/publish")
    async def publish_prepared_to_l2(request: Request):
        """从 L1 PD-* 生成不可变 L2 PreparedEquipmentDataset（PED-*）。"""
        body = await _json_object(request)
        l1_version_id = str(body.get("l1_version_id") or body.get("source_revision_id") or "").strip()
        if not l1_version_id:
            return _error("invalid_request", "必须提供 l1_version_id。", 422)
        if "/" in l1_version_id or "\\" in l1_version_id or ".." in l1_version_id:
            return _error("invalid_request", "不允许提交路径型标识。", 422)
        equipment_type = body.get("equipment_type")
        release_mode = str(body.get("release_mode") or "training_and_inference")
        try:
            from l2.prepared.publisher import PreparedPublishError, publish_from_l1_prepared

            result = publish_from_l1_prepared(
                l1_version_id,
                equipment_type=str(equipment_type).strip() if equipment_type else None,
                release_mode=release_mode,
            )
            return result
        except PreparedPublishError as exc:
            return JSONResponse(
                status_code=400,
                content={
                    "error_code": exc.code,
                    "message": exc.message,
                    "details": exc.details,
                },
            )
        except KeyError as exc:
            return _error("not_found", f"L1 prepared 版本不存在：{exc}", 404)
        except Exception as exc:  # noqa: BLE001
            return _error("publish_failed", str(exc), 500)

    @app.get("/api/l1/prepared-datasets/{version_id}/rows")
    def read_prepared_rows(
        version_id: str,
        request: Request,
        offset: int = 0,
        limit: int = 100,
        equipment_id: str | None = None,
        start: str | None = None,
        end: str | None = None,
    ):
        if not _page_params(request):
            return _error("invalid_page", "offset 必须大于等于 0，limit 必须是 1 到 200 的整数。", 422)
        try:
            if version_id.startswith("PED-"):
                from l2.prepared.adapter import PreparedDatasetAdapter

                adapter = PreparedDatasetAdapter(version_id)
                meta = adapter.load_metadata()
                total = int(len(meta))
                page = meta.iloc[offset : offset + limit]
                return {
                    "prepared_dataset_id": version_id,
                    "total": total,
                    "offset": offset,
                    "limit": limit,
                    "rows": page.to_dict(orient="records"),
                }
            return list_prepared_rows(version_id, offset, limit, equipment_id, start, end)
        except KeyError:
            return _error("not_found", f"数据集不存在：{version_id}", 404)
        except L1Error as exc:
            return _from_error(exc)

    @app.get("/api/l1/datasets/{dataset_id}/prepare/latest")
    def read_latest_prepare(dataset_id: str):
        try:
            return latest_prepare(dataset_id)
        except L1Error as exc:
            return _from_error(exc)

    @app.get("/api/l1/datasets/{dataset_id}/file")
    def read_file(dataset_id: str):
        try:
            path, filename = raw_file(dataset_id)
        except L1Error as exc:
            return _from_error(exc)
        media = (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            if filename.lower().endswith(".xlsx")
            else "text/csv; charset=utf-8"
        )
        return FileResponse(path, media_type=media, filename=filename)


async def _json_object(request: Request) -> dict:
    raw = await request.body()
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise L1Error("invalid_config", "区域配置不是合法 JSON。", 422) from exc
    if not isinstance(payload, dict):
        raise L1Error("invalid_config", "区域配置必须是 JSON 对象。", 422)
    return payload


def _page_params(request: Request) -> bool:
    if "offset" in request.query_params and not _integer(request.query_params.get("offset")):
        return False
    if "limit" in request.query_params and not _integer(request.query_params.get("limit"), minimum=1):
        return False
    return True


def _integer(value: str | None, minimum: int = 0) -> bool:
    if value is None or not value.strip().lstrip("-").isdigit():
        return False
    try:
        number = int(value)
    except ValueError:
        return False
    return number >= minimum


def _from_error(exc: L1Error) -> JSONResponse:
    return _error(exc.error_type, exc.message, exc.status_code, exc.location)


def _error(error_type: str, message: str, status_code: int, location: dict | None = None) -> JSONResponse:
    body: dict = {"success": False, "error_type": error_type, "message": message}
    if location is not None:
        body["location"] = location
    return JSONResponse(body, status_code=status_code)
