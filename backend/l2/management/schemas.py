"""Management API 请求/响应模型。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


class TrainJobCreateRequest(BaseModel):
    equipment_type: str = Field(min_length=1, max_length=64)
    dataset_id: str | None = Field(default=None, max_length=128)
    prepared_dataset_id: str | None = Field(default=None, max_length=128)
    training_config_id: str | None = Field(default=None, max_length=64)
    parent_model_bundle_id: str | None = Field(default=None, max_length=64)

    @field_validator("equipment_type", "dataset_id", "prepared_dataset_id", "parent_model_bundle_id")
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


class OnboardingPrepareRequest(BaseModel):
    dataset_id: str = Field(min_length=1, max_length=128)
    pd_revision_id: str = Field(min_length=1, max_length=64)
    equipment_group_id: str | None = Field(default=None, max_length=128)
    equipment_category: str | None = Field(default=None, max_length=128)

    @field_validator("dataset_id", "pd_revision_id", "equipment_group_id", "equipment_category")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        return text or None


class OnboardingConfirmRequest(BaseModel):
    equipment_type: str | None = None
    display_name: str | None = None
    normal_label: str | None = None
    selected_feature_ids: list[str] | None = None
    handoff_id: str | None = None
    handoff_item_id: str | None = None
    continue_handoff: bool = True


class OnboardingValidateRequest(BaseModel):
    equipment_type: str | None = None
    display_name: str | None = None
    normal_label: str | None = None
    selected_feature_ids: list[str] | None = None
