"""L2 推理业务服务。"""

from __future__ import annotations

import logging
import time
from typing import Any

from l2.equipment.registry import has_equipment
from l2.inference.decision import DecisionThresholds, decide
from l2.inference.model_manager import MODEL_MANAGER, ModelManager, ModelNotReadyError
from l2.inference.spectral_reference import attach_spectral_reference
from l2.inference.validators import InferenceValidationError, validate_equipment_type, validate_features
from l2.training import predict_bundle

LOG = logging.getLogger("l2.inference")


def _validate_proba_map(name: str, proba: dict[str, float]) -> None:
    if not proba:
        raise RuntimeError(f"{name} 概率为空。")
    values = list(proba.values())
    if any((v < -1e-9 or v > 1 + 1e-9 or v != v) for v in values):
        raise RuntimeError(f"{name} 概率非法：{proba}")
    total = float(sum(values))
    if abs(total - 1.0) > 1e-4:
        raise RuntimeError(f"{name} 概率未归一化：sum={total}")


def _enrich_prediction(
    raw: dict[str, Any],
    *,
    record_id: str | None,
    equipment_type: str,
    thresholds: dict[str, float],
    normal_label: str = "正常",
) -> dict[str, Any]:
    binary_proba = {str(k): float(v) for k, v in (raw.get("binary_proba") or {}).items()}
    multiclass_proba = {str(k): float(v) for k, v in (raw.get("multiclass_proba") or {}).items()}
    _validate_proba_map("binary", binary_proba)
    _validate_proba_map("multiclass", multiclass_proba)

    p_a = float(raw.get("p_a_abnormal", binary_proba.get("非正常", 0.0)))
    p_b = float(raw.get("p_b_abnormal", 1.0 - multiclass_proba.get(normal_label, 0.0)))
    decision = decide(
        p_a_abnormal=p_a,
        p_b_abnormal=p_b,
        multiclass_proba=multiclass_proba,
        thresholds=thresholds,
        normal_label=normal_label,
    )
    calibration = raw.get("calibration_status") or {}
    body = {
        "record_id": record_id,
        "equipment_type": equipment_type,
        "model_bundle_id": raw.get("model_bundle_id"),
        "binary_classifier": {
            "predicted_label": raw.get("binary_label"),
            "probabilities": binary_proba,
            "p_abnormal": p_a,
            "calibration_status": calibration.get("binary") if isinstance(calibration, dict) else None,
        },
        "multiclass_classifier": {
            "predicted_label": raw.get("multiclass_label"),
            "probabilities": multiclass_proba,
            "ranked_classes": decision["ranked_classes"],
            "top1_probability": decision["top1_probability"],
            "top2_probability": decision["top2_probability"],
            "top1_top2_margin": decision["top1_top2_margin"],
            "p_abnormal_aggregate": p_b,
            "calibration_status": calibration.get("multiclass") if isinstance(calibration, dict) else None,
        },
        "consistency": {
            "p_a_abnormal": p_a,
            "p_b_abnormal": p_b,
            "probability_gap": decision["probability_gap"],
            "binary_direction": decision["binary_direction"],
            "multiclass_aggregate_direction": decision["multiclass_aggregate_direction"],
            "multiclass_top1": decision["multiclass_top1"],
        },
        "decision": {
            "screening_result": decision["screening_result"],
            "preliminary_fault": decision["preliminary_fault"],
            "status": decision["status"],
            "route": decision["route"],
            "reason_codes": decision["reason_codes"],
        },
        "calibration_status": calibration,
    }
    # 频谱参考为后置解释字段，不参与概率与分流计算。
    return attach_spectral_reference(body)


class InferenceService:
    def __init__(self, manager: ModelManager | None = None):
        self.manager = manager or MODEL_MANAGER

    def diagnose_one(self, *, equipment_type: str, features: dict[str, Any], record_id: str | None = None) -> dict[str, Any]:
        started = time.perf_counter()
        if not has_equipment(equipment_type):
            raise InferenceValidationError(
                "UNSUPPORTED_EQUIPMENT_TYPE",
                f"不支持的设备类型：{equipment_type}。",
                {"equipment_type": equipment_type},
                status_code=400,
            )
        state = self.manager.require(equipment_type)
        validate_equipment_type(equipment_type, state.config.equipment_type)
        columns = list(state.bundle.feature_schema["feature_columns"])
        cleaned = validate_features(features, columns)
        preds = predict_bundle(state.bundle, [cleaned])
        if not preds:
            raise RuntimeError("模型未返回预测结果。")
        normal_label = str(
            state.bundle.label_map.get("normal_label")
            or (state.bundle.manifest.get("class_labels") or {}).get("normal_label")
            or "正常"
        )
        body = _enrich_prediction(
            preds[0],
            record_id=record_id,
            equipment_type=equipment_type,
            thresholds=state.config.decision_thresholds,
            normal_label=normal_label,
        )
        body["schema_version"] = state.bundle.feature_schema.get("version")
        body["inference_time_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
        return body

    def diagnose_batch(self, *, equipment_type: str, records: list[dict[str, Any]]) -> dict[str, Any]:
        started = time.perf_counter()
        if not has_equipment(equipment_type):
            raise InferenceValidationError(
                "UNSUPPORTED_EQUIPMENT_TYPE",
                f"不支持的设备类型：{equipment_type}。",
                {"equipment_type": equipment_type},
                status_code=400,
            )
        state = self.manager.require(equipment_type)
        validate_equipment_type(equipment_type, state.config.equipment_type)
        if len(records) > state.config.max_batch_size:
            raise InferenceValidationError(
                "BATCH_TOO_LARGE",
                f"批量大小 {len(records)} 超过上限 {state.config.max_batch_size}。",
                {"max_batch_size": state.config.max_batch_size, "received": len(records)},
                status_code=400,
            )
        columns = list(state.bundle.feature_schema["feature_columns"])
        cleaned_rows: list[dict[str, float]] = []
        record_ids: list[str | None] = []
        for index, item in enumerate(records):
            features = item.get("features") if isinstance(item, dict) else None
            if not isinstance(features, dict):
                raise InferenceValidationError(
                    "INVALID_RECORD",
                    f"records[{index}].features 必须是对象。",
                    {"index": index},
                )
            cleaned_rows.append(validate_features(features, columns, record_index=index))
            record_ids.append(item.get("record_id") if isinstance(item, dict) else None)

        preds = predict_bundle(state.bundle, cleaned_rows)
        if len(preds) != len(cleaned_rows):
            raise RuntimeError("批量预测结果数量与输入不一致。")

        normal_label = str(
            state.bundle.label_map.get("normal_label")
            or (state.bundle.manifest.get("class_labels") or {}).get("normal_label")
            or "正常"
        )
        results = [
            _enrich_prediction(
                pred,
                record_id=record_ids[i],
                equipment_type=equipment_type,
                thresholds=state.config.decision_thresholds,
                normal_label=normal_label,
            )
            for i, pred in enumerate(preds)
        ]
        total_ms = (time.perf_counter() - started) * 1000.0
        return {
            "equipment_type": equipment_type,
            "model_bundle_id": state.bundle.manifest.get("model_bundle_id"),
            "count": len(results),
            "total_inference_time_ms": round(total_ms, 3),
            "avg_inference_time_ms": round(total_ms / max(len(results), 1), 3),
            "results": results,
        }


    def diagnose_prepared_records(
        self,
        *,
        prepared_dataset_id: str,
        record_ids: list[str],
        revision_id: str | None = None,
    ) -> dict[str, Any]:
        from l2.prepared.adapter import PreparedDatasetAdapter
        from l2.prepared.catalog import get_published_dataset

        try:
            meta = get_published_dataset(prepared_dataset_id)
        except KeyError as exc:
            raise InferenceValidationError(
                "DATASET_NOT_FOUND",
                f"PreparedDataset 不存在：{prepared_dataset_id}",
                {"prepared_dataset_id": prepared_dataset_id},
                status_code=404,
            ) from exc
        if revision_id and meta.get("revision_id") and revision_id != meta.get("revision_id"):
            raise InferenceValidationError(
                "DATASET_REVISION_MISMATCH",
                "请求的 revision_id 与已发布快照不一致。",
                {
                    "requested": revision_id,
                    "actual": meta.get("revision_id"),
                },
                status_code=400,
            )
        if not meta.get("inference_ready"):
            raise InferenceValidationError(
                "DATASET_NOT_INFERENCE_READY",
                "数据集不可用于推理。",
                {"prepared_dataset_id": prepared_dataset_id},
                status_code=400,
            )
        equipment_type = str(meta["equipment_type"])
        adapter = PreparedDatasetAdapter(prepared_dataset_id)
        adapter.validate_dataset()
        state = self.manager.require(equipment_type)
        model_schema = str(state.bundle.feature_schema.get("version") or "")
        data_schema = str(meta.get("schema_version") or "")
        if model_schema and data_schema and model_schema != data_schema:
            raise InferenceValidationError(
                "SCHEMA_INCOMPATIBLE",
                "Active 模型 Feature Schema 与数据集不兼容。",
                {"model_schema": model_schema, "dataset_schema": data_schema},
                status_code=400,
            )
        if len(record_ids) > state.config.max_batch_size:
            raise InferenceValidationError(
                "BATCH_TOO_LARGE",
                f"批量大小 {len(record_ids)} 超过上限 {state.config.max_batch_size}。",
                {"max_batch_size": state.config.max_batch_size, "received": len(record_ids)},
                status_code=400,
            )
        records = []
        for record_id in record_ids:
            try:
                item = adapter.get_record(record_id)
            except KeyError as exc:
                raise InferenceValidationError(
                    "RECORD_NOT_FOUND",
                    f"记录不存在：{record_id}",
                    {"record_id": record_id},
                    status_code=404,
                ) from exc
            records.append({"record_id": item["record_id"], "features": item["features"]})
        batch = self.diagnose_batch(equipment_type=equipment_type, records=records)
        for result in batch.get("results") or []:
            result["prepared_dataset_id"] = prepared_dataset_id
            result["dataset_revision_id"] = meta.get("revision_id")
            result["schema_version"] = meta.get("schema_version")
        batch["prepared_dataset_id"] = prepared_dataset_id
        batch["dataset_revision_id"] = meta.get("revision_id")
        batch["source_type"] = "l1_prepared"
        return batch


INFERENCE_SERVICE = InferenceService()
