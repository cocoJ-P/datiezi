"""十字段监督训练（CatBoost；每设备每目标独立头）。"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd
from catboost import CatBoostClassifier, CatBoostRegressor

from l5.prediction import FEATURE_SCHEMA_VERSION, __version__
from l5.prediction.artifacts import save_bundle
from l5.prediction.contract import (
    CLASSIFICATION_TARGETS,
    REGRESSION_TARGETS,
    SHEET_TO_EQUIPMENT_TYPE,
    TARGET_COLUMNS,
    TEMPLATE_TARGETS,
)
from l5.prediction.dataset import extract_targets, load_train_tables, project_features
from l5.prediction.metrics import ORDINAL_FOR_TARGET, classification_metrics, regression_metrics
from l5.prediction.paths import DEFAULT_BUNDLE
from l5.prediction.preprocess import FeaturePreprocessor
from l5.prediction.split import assert_no_target_in_features, time_split_equipment


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _cb_classifier(**kwargs: Any) -> CatBoostClassifier:
    params = {
        "loss_function": "MultiClass",
        "iterations": 200,
        "depth": 6,
        "learning_rate": 0.08,
        "random_seed": 42,
        "verbose": False,
        "allow_writing_files": False,
    }
    params.update(kwargs)
    return CatBoostClassifier(**params)


def _cb_regressor(**kwargs: Any) -> CatBoostRegressor:
    params = {
        "loss_function": "RMSE",
        "iterations": 200,
        "depth": 6,
        "learning_rate": 0.08,
        "random_seed": 42,
        "verbose": False,
        "allow_writing_files": False,
    }
    params.update(kwargs)
    return CatBoostRegressor(**params)


def _model_key(equipment_category: str, target: str) -> str:
    return f"{equipment_category}::{target}"


def train_bundle(
    *,
    bundle_dir=None,
    train_ratio: float = 0.8,
) -> dict[str, Any]:
    loaded = load_train_tables()
    tables: dict[str, pd.DataFrame] = loaded["tables"]
    meta = loaded["meta"]
    fingerprint = meta["dataset_fingerprint"]

    split_manifests: list[dict[str, Any]] = []
    train_parts: list[pd.DataFrame] = []
    valid_parts: list[pd.DataFrame] = []

    for stem, df in tables.items():
        eq_type = SHEET_TO_EQUIPMENT_TYPE[stem]
        cat = str(df["设备类别"].iloc[0])
        split = time_split_equipment(
            df,
            equipment_type=eq_type,
            equipment_category=cat,
            train_ratio=train_ratio,
            dataset_fingerprint=fingerprint,
        )
        split_manifests.append(split["manifest"])
        train_parts.append(split["train_df"])
        valid_parts.append(split["valid_df"])

    train_all = pd.concat(train_parts, ignore_index=True)
    valid_all = pd.concat(valid_parts, ignore_index=True)

    # 预处理仅拟合训练区
    pre = FeaturePreprocessor()
    x_train_all = pre.fit_transform(project_features(train_all))
    assert_no_target_in_features(list(x_train_all.columns))

    models: dict[str, Any] = {}
    vocabularies: dict[str, Any] = {}
    template_dictionaries: dict[str, Any] = {}
    metrics_out: dict[str, Any] = {
        "fit_repro": {},
        "internal_val": {},
        "per_equipment_internal_val": {},
        "channel_note": {
            "fit_repro": "predictions_on_train_split_only_not_generalization",
            "internal_val": "time_holdout_primary_metrics",
            "blind_test": "unavailable_same_obs_as_train",
        },
    }

    hyper = {
        "classifier": {"iterations": 200, "depth": 6, "learning_rate": 0.08, "random_seed": 42},
        "regressor": {"iterations": 200, "depth": 6, "learning_rate": 0.08, "random_seed": 42},
    }

    for stem, train_df in zip(tables.keys(), train_parts):
        cat = str(train_df["设备类别"].iloc[0])
        valid_df = next(v for v in valid_parts if str(v["设备类别"].iloc[0]) == cat)
        x_tr = pre.transform(project_features(train_df))
        x_va = pre.transform(project_features(valid_df))
        y_tr = extract_targets(train_df)
        y_va = extract_targets(valid_df)
        cat_features = ["设备类别"]

        for target in TARGET_COLUMNS:
            key = _model_key(cat, target)
            ytr = y_tr[target]
            yva = y_va[target]

            if target in REGRESSION_TARGETS:
                ytr_n = pd.to_numeric(ytr, errors="coerce")
                yva_n = pd.to_numeric(yva, errors="coerce")
                if ytr_n.isna().any() or yva_n.isna().any():
                    raise ValueError(f"confidence_nan:{cat}")
                model = _cb_regressor()
                model.fit(x_tr, ytr_n, cat_features=cat_features)
                pred_tr = np.clip(model.predict(x_tr), 0.0, 1.0)
                pred_va = np.clip(model.predict(x_va), 0.0, 1.0)
                metrics_out["fit_repro"][key] = regression_metrics(ytr_n.to_numpy(), pred_tr)
                metrics_out["internal_val"][key] = regression_metrics(yva_n.to_numpy(), pred_va)
                metrics_out["fit_repro"][key]["target_field"] = target
                metrics_out["internal_val"][key]["target_field"] = target
                metrics_out["fit_repro"][key]["equipment_category"] = cat
                metrics_out["internal_val"][key]["equipment_category"] = cat
                vocabularies[key] = {"type": "regression", "clip": [0.0, 1.0]}
            else:
                ytr_s = ytr.astype(str)
                yva_s = yva.astype(str)
                classes = sorted(ytr_s.unique().tolist())
                if not classes:
                    raise ValueError(f"empty_classes:{key}")
                # 验证集中训练未见标签
                unseen = sorted(set(yva_s.unique()) - set(classes))
                model = _cb_classifier()
                model.fit(x_tr, ytr_s, cat_features=cat_features)
                pred_tr = [str(x) for x in model.predict(x_tr).reshape(-1)]
                pred_va = [str(x) for x in model.predict(x_va).reshape(-1)]
                # 强制限制在训练词表（CatBoost 一般不会越界，双保险）
                pred_va = [p if p in classes else classes[0] for p in pred_va]
                order = ORDINAL_FOR_TARGET.get(target)
                m_tr = classification_metrics(ytr_s.tolist(), pred_tr, labels=classes, ordinal_order=order)
                m_va = classification_metrics(yva_s.tolist(), pred_va, labels=classes, ordinal_order=order)
                m_tr.update({"target_field": target, "equipment_category": cat, "n_templates_or_classes": len(classes)})
                m_va.update(
                    {
                        "target_field": target,
                        "equipment_category": cat,
                        "n_templates_or_classes": len(classes),
                        "unseen_labels_in_valid": unseen,
                        "n_unseen_labels_in_valid": len(unseen),
                    }
                )
                metrics_out["fit_repro"][key] = m_tr
                metrics_out["internal_val"][key] = m_va
                vocabularies[key] = {"type": "classification", "classes": classes}
                if target in TEMPLATE_TARGETS:
                    # 模板字典：id -> 原文；仅训练区
                    template_dictionaries[key] = {str(i): c for i, c in enumerate(classes)}
                    # 也存逆向
                    template_dictionaries[key + "::text_to_id"] = {c: str(i) for i, c in enumerate(classes)}

            models[key] = model

            pe = metrics_out["per_equipment_internal_val"].setdefault(cat, {})
            pe[target] = metrics_out["internal_val"][key]

    model_id = "L5P-" + hashlib.sha256(f"{fingerprint}|{__version__}|{ _now()}".encode()).hexdigest()[:12]
    manifest = {
        "model_id": model_id,
        "schema_version": FEATURE_SCHEMA_VERSION,
        "prediction_engine_version": __version__,
        "algorithm": "CatBoost per-equipment per-target",
        "created_at": _now(),
        "dataset_fingerprint": fingerprint,
        "column_audit_summary": meta.get("n_columns_resolved"),
        "train_column_fact": "train_xlsx_has_33_columns; official_dev_has_34th_数据集划分_excluded",
        "feature_schema": {
            "version": FEATURE_SCHEMA_VERSION,
            "numeric_features": list(pre.feature_names_[:-1]),
            "categorical_features": ["设备类别"],
            "excluded_forever": ["记录编号", "设备编号", "采集时间", "数据集划分", *TARGET_COLUMNS],
        },
        "targets": TARGET_COLUMNS,
        "classification_targets": CLASSIFICATION_TARGETS,
        "regression_targets": REGRESSION_TARGETS,
        "template_targets": TEMPLATE_TARGETS,
        "equipment_categories": sorted({str(df['设备类别'].iloc[0]) for df in tables.values()}),
        "hyperparameters": hyper,
        "split_ids": [m["split_id"] for m in split_manifests],
        "n_models": len(models),
        "l2_note": "L2 NORMAL/ABNORMAL is not the 4-class 设备状态; L5 trains 正常/预警/报警/危险 independently",
        "l3_l4_note": "dataset_predicted_* texts are independent from l3/l4 assessments",
    }

    out_dir = bundle_dir or DEFAULT_BUNDLE
    save_bundle(
        out_dir,
        manifest=manifest,
        preprocessor=pre,
        models=models,
        vocabularies=vocabularies,
        template_dictionaries=template_dictionaries,
        metrics=metrics_out,
        split_manifests=split_manifests,
    )

    # 精简摘要
    summary = {
        "model_id": model_id,
        "bundle_dir": str(out_dir),
        "dataset_fingerprint": fingerprint,
        "n_models": len(models),
        "internal_val_highlights": {},
    }
    for target in TARGET_COLUMNS:
        rows = [metrics_out["internal_val"][k] for k in metrics_out["internal_val"] if k.endswith("::" + target)]
        if target in REGRESSION_TARGETS:
            summary["internal_val_highlights"][target] = {
                "macro_mae": float(np.mean([r["mae"] for r in rows])),
                "macro_rmse": float(np.mean([r["rmse"] for r in rows])),
            }
        else:
            summary["internal_val_highlights"][target] = {
                "macro_accuracy": float(np.mean([r["accuracy"] for r in rows])),
                "macro_f1": float(np.mean([r["macro_f1"] for r in rows])),
                "exact_match_rate": float(np.mean([r.get("exact_match_rate", r["accuracy"]) for r in rows])),
            }
    return {"manifest": manifest, "summary": summary, "metrics": metrics_out, "meta": meta}


def json_dumps_safe(obj: Any) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)


if __name__ == "__main__":
    result = train_bundle()
    print(json_dumps_safe(result["summary"]))
