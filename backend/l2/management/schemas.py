"""Management API 请求/响应模型。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


class TrainJobCreateRequest(BaseModel):
    equipment_type: str = Field(min_length=1, max_length=64)
    dataset_id: str | None = Field(default=None, max_length=128)
    prepared_dataset_id: str | None = Field(default=None, max_length=128)
    training_config_id: str | None = Field(default=None, max_length=64)

    @field_validator("equipment_type", "dataset_id", "prepared_dataset_id")
    @classmethod
    def strip_optional_ids(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        if not text:
            raise ValueError("字段不能为空")
        if "/" in text or "\\" in text or ".." in text:
            raise ValueError("字段不能包含路径字符")
        return text

    @model_validator(mode="after")
    def require_dataset_ref(self) -> "TrainJobCreateRequest":
        if not self.dataset_id and not self.prepared_dataset_id:
            raise ValueError("必须提供 dataset_id 或 prepared_dataset_id")
        return self


class RollbackRequest(BaseModel):
    equipment_type: str = Field(min_length=1, max_length=64)
    target_model_bundle_id: str = Field(min_length=1, max_length=64)

    @field_validator("equipment_type", "target_model_bundle_id")
    @classmethod
    def strip_ids(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("字段不能为空")
        if "/" in text or "\\" in text or ".." in text:
            raise ValueError("字段不能包含路径字符")
        return text


class ManagementErrorBody(BaseModel):
    error_code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    request_id: str | None = None
