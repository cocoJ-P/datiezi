"""批量 DiagnosisRun：固定捕获的 model bundle，分批 predict_bundle。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from l2.equipment.registry import get_equipment_spec
from l2.handoff.store import HandoffStore
from l2.inference.config import PROJECT_ROOT, load_config
from l2.inference.model_manager import MODEL_MANAGER
from l2.inference.service import _enrich_prediction
from l2.inference.validators import validate_features
from l2.prepared.adapter import PreparedDatasetAdapter
from l2.prepared.catalog import get_published_dataset
from l2.training import predict_bundle


def _result_root() -> Path:
    path = PROJECT_ROOT / "data" / "runtime" / "l2_diagnosis_runs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def content_key_for(
    *,
    prepared_dataset_id: str,
    dataset_revision_id: str,
    model_bundle_id: str,
) -> str:
    raw = f"{prepared_dataset_id}|{dataset_revision_id}|{model_bundle_id}|v1"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def run_batch_diagnosis(
    store: HandoffStore,
    *,
    prepared_dataset_id: str,
    model_bundle_id: str,
    handoff_item_id: str | None = None,
    force_new: bool = False,
) -> dict[str, Any]:
    """对 PED 全量记录执行批量诊断，结果写入 DiagnosisRun。"""
    meta = get_published_dataset(prepared_dataset_id)
    equipment_type = str(meta["equipment_type"])
    revision_id = str(meta.get("revision_id") or "")
    schema_version = str(meta.get("schema_version") or "")

    if not meta.get("inference_ready"):
        raise RuntimeError("PED 不可用于推理。")
    if not MODEL_MANAGER.is_ready(equipment_type):
        raise RuntimeError(f"设备 {equipment_type} 运行时模型未加载。")

    # 捕获任务开始时的模型包；后续热切换不影响本任务
    state = MODEL_MANAGER.require(equipment_type)
    captured_bundle = state.bundle
    captured_id = str(captured_bundle.manifest.get("model_bundle_id") or "")
    if captured_id != model_bundle_id:
        raise RuntimeError(
            f"任务绑定模型 {model_bundle_id} 与当前运行时 {captured_id} 不一致。"
        )
    thresholds = dict(state.config.decision_thresholds)
    max_batch = int(state.config.max_batch_size or load_config().max_batch_size or 200)
    columns = list(captured_bundle.feature_schema["feature_columns"])
    normal_label = str(
        captured_bundle.label_map.get("normal_label")
        or (captured_bundle.manifest.get("class_labels") or {}).get("normal_label")
        or "正常"
    )

    adapter = PreparedDatasetAdapter(prepared_dataset_id)
    adapter.validate_dataset()
    features = adapter.load_features()
    spec = get_equipment_spec(equipment_type)
    id_col = spec.record_id_field
    record_ids = [str(x) for x in features[id_col].astype(str).tolist()]
    feature_map = {
        str(row[id_col]): {name: float(row[name]) for name in spec.feature_names}
        for _, row in features.iterrows()
    }

    key = content_key_for(
        prepared_dataset_id=prepared_dataset_id,
        dataset_revision_id=revision_id,
        model_bundle_id=model_bundle_id,
    )
    run, created = store.create_or_get_diagnosis_run(
        content_key=key,
        prepared_dataset_id=prepared_dataset_id,
        dataset_revision_id=revision_id,
        equipment_type=equipment_type,
        model_bundle_id=model_bundle_id,
        schema_version=schema_version,
        total_records=len(record_ids),
        handoff_item_id=handoff_item_id,
        force_new=force_new,
    )
    if not created and run["status"] in {"completed", "completed_with_errors"}:
        return {"reused": True, **run}

    run_id = run["diagnosis_run_id"]
    store.update_diagnosis_run(run_id, status="running")

    counts = {"NORMAL": 0, "ABNORMAL": 0, "CONFLICT": 0, "UNCERTAIN": 0, "FAILED": 0}
    processed = 0
    succeeded = 0
    failed = 0
    out_dir = _result_root() / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    for start in range(0, len(record_ids), max_batch):
        chunk_ids = record_ids[start : start + max_batch]
        try:
            cleaned_rows = []
            for rid in chunk_ids:
                cleaned_rows.append(validate_features(feature_map[rid], columns))
            preds = predict_bundle(captured_bundle, cleaned_rows)
            if len(preds) != len(cleaned_rows):
                raise RuntimeError("批量预测结果数量与输入不一致。")
            rows_to_save: list[dict[str, Any]] = []
            for rid, pred in zip(chunk_ids, preds):
                result = _enrich_prediction(
                    pred,
                    record_id=rid,
                    equipment_type=equipment_type,
                    thresholds=thresholds,
                    normal_label=normal_label,
                )
                result["model_bundle_id"] = model_bundle_id
                result["prepared_dataset_id"] = prepared_dataset_id
                result["dataset_revision_id"] = revision_id
                result["schema_version"] = schema_version
                screening = ((result.get("decision") or {}).get("screening_result") or "UNCERTAIN").upper()
                if screening not in counts:
                    screening = "UNCERTAIN"
                counts[screening] = counts.get(screening, 0) + 1
                rows_to_save.append(
                    {
                        "record_id": rid,
                        "status": "ok",
                        "screening_result": screening,
                        "result": result,
                    }
                )
                succeeded += 1
            store.save_diagnosis_records(run_id, rows_to_save)
        except Exception as exc:  # noqa: BLE001 - 单批失败记入失败计数
            fail_rows = []
            for rid in chunk_ids:
                fail_rows.append(
                    {
                        "record_id": rid,
                        "status": "failed",
                        "screening_result": "FAILED",
                        "result": {
                            "record_id": rid,
                            "error": str(exc),
                            "model_bundle_id": model_bundle_id,
                        },
                    }
                )
                failed += 1
                counts["FAILED"] = counts.get("FAILED", 0) + 1
            store.save_diagnosis_records(run_id, fail_rows)

        processed = succeeded + failed
        store.update_diagnosis_run(
            run_id,
            status="running",
            processed_records=processed,
            succeeded_records=succeeded,
            failed_records=failed,
            counts=counts,
        )

    final_status = "completed" if failed == 0 else ("completed_with_errors" if succeeded else "failed")
    summary_path = out_dir / "summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "diagnosis_run_id": run_id,
                "prepared_dataset_id": prepared_dataset_id,
                "model_bundle_id": model_bundle_id,
                "counts": counts,
                "total_records": len(record_ids),
                "succeeded_records": succeeded,
                "failed_records": failed,
                "status": final_status,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    updated = store.update_diagnosis_run(
        run_id,
        status=final_status,
        processed_records=processed,
        succeeded_records=succeeded,
        failed_records=failed,
        counts=counts,
        result_dir=str(out_dir),
        finished=True,
    )
    # L2 完成后自动登记 L3 AnalysisRun（不在推理路径同步执行分析）。
    if final_status in {"completed", "completed_with_errors"}:
        try:
            from l3.runtime.jobs import enqueue_from_diagnosis_run

            l3_body = enqueue_from_diagnosis_run(run_id, handoff_store=store)
            updated = {**updated, "l3_enqueue": l3_body}
        except Exception as exc:  # noqa: BLE001 - L3 入队失败不回滚 L2 结果
            updated = {**updated, "l3_enqueue_error": str(exc)}
    return updated
