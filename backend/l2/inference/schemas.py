"""L2 Inference API 请求/响应模型。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DiagnoseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    equipment_type: str
    record_id: str | None = None
    features: dict[str, Any]


class BatchRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_id: str | None = None
    features: dict[str, Any]


class BatchDiagnoseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    equipment_type: str
    records: list[BatchRecord] = Field(min_length=1)


class DatasetDiagnoseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_ids: list[str] = Field(min_length=1)
    revision_id: str | None = None


class ErrorBody(BaseModel):
    error_code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    request_id: str | None = None
