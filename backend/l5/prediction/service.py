"""L5PredictionService：有界批量推理。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from l5.prediction import FEATURE_SCHEMA_VERSION, SCHEMA_VERSION, __version__
from l5.prediction.artifacts import load_bundle
from l5.prediction.contract import FEATURE_WHITELIST, TARGET_COLUMNS, TEMPLATE_TARGETS
from l5.prediction.dataset import load_test_tables, project_features
from l5.prediction.paths import DEFAULT_BUNDLE
from l5.prediction.synthesis_contract import attach_l5_prediction, empty_diagnosis_synthesis_input


class L5PredictionError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class L5PredictionService:
    def __init__(self, bundle_dir: Path | str | None = None):
        self.bundle_dir = Path(bundle_dir) if bundle_dir else DEFAULT_BUNDLE
        if not self.bundle_dir.is_dir():
            raise L5PredictionError("bundle_missing", f"模型包不存在：{self.bundle_dir}")
        self.bundle = load_bundle(self.bundle_dir)
        self.manifest = self.bundle["manifest"]
        self.preprocessor = self.bundle["preprocessor"]
        self.models = self.bundle["models"]
        self.vocabularies = self.bundle["vocabularies"]
        self.templates = self.bundle["template_dictionaries"]

    def _model_key(self, equipment_category: str, target: str) -> str:
        return f"{equipment_category}::{target}"

    def _input_fingerprint(self, features: dict[str, Any], equipment_category: str) -> str:
        payload = {k: features.get(k) for k in FEATURE_WHITELIST}
        payload["设备类别"] = equipment_category
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def predict_record(
        self,
        *,
        record_id: str,
        equipment_category: str,
        features: dict[str, Any],
        equipment_type: str | None = None,
        equipment_id: str | None = None,
        reject_answer_fields: bool = True,
    ) -> dict[str, Any]:
        # 显式拒绝答案字段进入推理
        leaked = [k for k in features.keys() if k in TARGET_COLUMNS or k == "数据集划分"]
        if leaked and reject_answer_fields:
            # 投影丢弃，不依赖其取值
            features = {k: v for k, v in features.items() if k not in leaked}

        missing = [c for c in FEATURE_WHITELIST if c not in features]
        if missing:
            raise L5PredictionError("missing_features", f"缺少观测字段：{missing}")

        # 禁止借用其他设备模型
        supported = self.manifest.get("equipment_categories") or []
        if equipment_category not in supported:
            raise L5PredictionError("unsupported_equipment", f"未支持的设备类别：{equipment_category}")

        row = {c: features[c] for c in FEATURE_WHITELIST}
        row["设备类别"] = equipment_category
        df = pd.DataFrame([row])
        x = self.preprocessor.transform(project_features(df))

        predictions: dict[str, Any] = {}
        statuses: dict[str, str] = {}
        sources: dict[str, str] = {}
        for target in TARGET_COLUMNS:
            key = self._model_key(equipment_category, target)
            if key not in self.models:
                raise L5PredictionError("model_missing", f"缺少模型：{key}")
            model = self.models[key]
            vocab = self.vocabularies.get(key) or {}
            if target == "诊断置信度":
                val = float(np.clip(model.predict(x)[0], 0.0, 1.0))
                predictions[target] = val
                statuses[target] = "ok"
                sources[target] = "supervised_regression"
            else:
                raw_pred = str(model.predict(x).reshape(-1)[0])
                classes = list(vocab.get("classes") or [])
                if classes and raw_pred not in classes:
                    # 不应发生；安全回退到训练词表众数首位
                    raw_pred = classes[0]
                    statuses[target] = "clamped_to_train_vocab"
                else:
                    statuses[target] = "ok"
                predictions[target] = raw_pred
                if target in TEMPLATE_TARGETS:
                    sources[target] = "template_id_classification"
                else:
                    sources[target] = "supervised_classification"

        assessment = {
            "schema": SCHEMA_VERSION,
            "record_id": record_id,
            "equipment_id": equipment_id,
            "equipment_type": equipment_type,
            "equipment_category": equipment_category,
            "model_versions": {
                "model_id": self.manifest.get("model_id"),
                "prediction_engine_version": __version__,
                "feature_schema_version": FEATURE_SCHEMA_VERSION,
            },
            "predictions": predictions,
            "prediction_status": statuses,
            "prediction_sources": sources,
            "field_semantics": {
                "根因分析": "dataset_predicted_root_cause",
                "建议措施": "dataset_predicted_recommendation_not_work_order",
                "影响程度": "dataset_predicted_impact_not_l4_propagation",
                "诊断置信度": "dataset_target_regression_not_calibrated_probability",
                "设备状态": "four_class_dataset_label_not_l2_binary",
            },
            "limitations": [
                "internal_val_is_time_holdout_not_blind_generalization",
                "official_test_file_shares_observations_with_train",
                "do_not_merge_with_l4_as_confirmed_root_cause",
            ],
            "source_feature_schema_version": FEATURE_SCHEMA_VERSION,
            "model_input_fingerprint": self._input_fingerprint(row, equipment_category),
            "dataset_fingerprint": self.manifest.get("dataset_fingerprint"),
        }
        return assessment

    def predict_dataframe(self, df: pd.DataFrame, *, equipment_category: str) -> list[dict[str, Any]]:
        if "设备类别" not in df.columns:
            raise L5PredictionError("missing_equipment_category", "输入缺少设备类别")
        cats = df["设备类别"].astype(str).unique().tolist()
        if cats != [equipment_category]:
            raise L5PredictionError("equipment_mismatch", f"期望 {equipment_category}，实际 {cats}")
        out = []
        for _, row in df.iterrows():
            feats = {c: row[c] for c in FEATURE_WHITELIST}
            out.append(
                self.predict_record(
                    record_id=str(row.get("记录编号")),
                    equipment_category=equipment_category,
                    equipment_id=str(row.get("设备编号")) if "设备编号" in df.columns else None,
                    features=feats,
                )
            )
        return out

    def predict_test_files(self, *, max_per_equipment: int = 20) -> dict[str, Any]:
        loaded = load_test_tables()
        results = []
        for stem, df in loaded["tables"].items():
            cat = str(df["设备类别"].iloc[0])
            sample = df.head(max_per_equipment)
            preds = self.predict_dataframe(sample, equipment_category=cat)
            results.append(
                {
                    "equipment_category": cat,
                    "n_inferred": len(preds),
                    "sample_record_ids": [p["record_id"] for p in preds[:5]],
                    "sample_predictions": [
                        {"record_id": p["record_id"], "predictions": p["predictions"]} for p in preds[:3]
                    ],
                }
            )
        return {
            "channel": "unlabeled_test_file_smoke_not_blind_eval",
            "model_id": self.manifest.get("model_id"),
            "equipment_runs": results,
        }

    def to_synthesis_input(self, assessment: dict[str, Any]) -> dict[str, Any]:
        base = empty_diagnosis_synthesis_input(
            record_id=str(assessment.get("record_id")),
            equipment_type=assessment.get("equipment_type"),
        )
        return attach_l5_prediction(base, assessment)
