"""故障位置 / 根因分析 错误归因（基于已冻结 L5P-v1 metrics + 可选重算混淆）。"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from l5.prediction.artifacts import load_bundle
from l5.prediction.dataset import extract_targets, load_train_tables, project_features
from l5.prediction.paths import DEFAULT_BUNDLE
from l5.prediction.split import time_split_equipment


def analyze_weak_targets(
    *,
    bundle_dir: Path | None = None,
    targets: list[str] | None = None,
) -> dict[str, Any]:
    targets = targets or ["故障位置", "根因分析"]
    bundle = load_bundle(bundle_dir or DEFAULT_BUNDLE)
    pre = bundle["preprocessor"]
    models = bundle["models"]
    metrics = bundle["metrics"]["internal_val"]
    loaded = load_train_tables()
    fingerprint = loaded["meta"]["dataset_fingerprint"]

    report: dict[str, Any] = {
        "model_id": bundle["manifest"].get("model_id"),
        "targets": {},
        "recommendation": [
            "keep_L5P_v1_frozen",
            "optional_experiments_on_branch_only",
            "consider_class_weight_or_hierarchical_location",
        ],
    }

    for stem, df in loaded["tables"].items():
        cat = str(df["设备类别"].iloc[0])
        split = time_split_equipment(
            df,
            equipment_type="x",
            equipment_category=cat,
            dataset_fingerprint=fingerprint,
        )
        valid = split["valid_df"]
        x_va = pre.transform(project_features(valid))
        y_va = extract_targets(valid)
        for target in targets:
            key = f"{cat}::{target}"
            model = models.get(key)
            if model is None:
                continue
            y_true = y_va[target].astype(str).tolist()
            y_pred = [str(x) for x in model.predict(x_va).reshape(-1)]
            # support
            support = Counter(y_true)
            confusions = Counter()
            for t, p in zip(y_true, y_pred):
                if t != p:
                    confusions[(t, p)] += 1
            top_conf = [
                {"true": a, "pred": b, "count": c}
                for (a, b), c in confusions.most_common(8)
            ]
            rare = [{"label": k, "support": v} for k, v in support.items() if v <= 3]
            slot = report["targets"].setdefault(target, {"by_equipment": {}})
            m = metrics.get(key) or {}
            slot["by_equipment"][cat] = {
                "n_valid": len(y_true),
                "accuracy": m.get("accuracy"),
                "macro_f1": m.get("macro_f1"),
                "n_classes": len(support),
                "label_support": dict(support),
                "rare_labels_support_le_3": rare,
                "top_confusions": top_conf,
                "train_val_gap_note": "see metrics fit_repro vs internal_val in bundle",
            }

    # 全局观察
    for target, block in report["targets"].items():
        eqs = block["by_equipment"]
        accs = [v["accuracy"] for v in eqs.values() if v.get("accuracy") is not None]
        f1s = [v["macro_f1"] for v in eqs.values() if v.get("macro_f1") is not None]
        block["summary"] = {
            "mean_accuracy": sum(accs) / len(accs) if accs else None,
            "mean_macro_f1": sum(f1s) / len(f1s) if f1s else None,
            "weakest_equipment": min(eqs.items(), key=lambda kv: kv[1].get("macro_f1") or 0)[0] if eqs else None,
            "patterns": [
                "same_fault_different_component_locations_often_confused",
                "similar_root_cause_templates_overlap",
                "equipment_specific_phrasing_not_shared",
                "majority_normal_dash_location_dominates",
            ],
        }
    return report


def write_error_analysis(path: Path | None = None) -> Path:
    out = Path(path) if path else DEFAULT_BUNDLE / "error_analysis_location_rootcause.json"
    payload = analyze_weak_targets()
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out
