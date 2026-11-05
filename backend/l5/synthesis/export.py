"""无答案测试文件十字段导出（禁止 record_id 回查训练答案）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from l5.prediction.contract import FEATURE_WHITELIST, TARGET_COLUMNS
from l5.prediction.dataset import load_test_tables
from l5.prediction.paths import REPO_ROOT
from l5.prediction.service import L5PredictionService
from l5.synthesis.mapping import CATEGORY_ALIASES, normalize_equipment_category


def export_unlabeled_test_predictions(
    *,
    out_dir: Path | None = None,
    max_per_equipment: int | None = None,
) -> dict[str, Any]:
    root = Path(out_dir) if out_dir else REPO_ROOT / "data" / "l5_exports" / "L5P-v1_test_predictions"
    root.mkdir(parents=True, exist_ok=True)
    svc = L5PredictionService()
    loaded = load_test_tables()
    summary = []
    frames = []
    for stem, df in loaded["tables"].items():
        cat_raw = str(df["设备类别"].iloc[0])
        cat = normalize_equipment_category(cat_raw) or CATEGORY_ALIASES.get(cat_raw, cat_raw)
        work = df if max_per_equipment is None else df.head(max_per_equipment)
        rows = []
        for _, row in work.iterrows():
            feats = {c: row[c] for c in FEATURE_WHITELIST}
            pred = svc.predict_record(
                record_id=str(row["记录编号"]),
                equipment_category=cat,
                equipment_id=str(row.get("设备编号")),
                features=feats,
            )
            item = {
                "记录编号": row["记录编号"],
                "设备编号": row.get("设备编号"),
                "设备类别": cat,
                "model_id": (pred.get("model_versions") or {}).get("model_id"),
                "input_fingerprint": pred.get("model_input_fingerprint"),
            }
            for t in TARGET_COLUMNS:
                item[t] = pred["predictions"].get(t)
            rows.append(item)
        out_df = pd.DataFrame(rows)
        path = root / f"{stem}_predictions.xlsx"
        out_df.to_excel(path, index=False)
        frames.append(out_df)
        summary.append({"equipment": cat, "rows": len(out_df), "path": str(path)})
    all_path = root / "all_equipment_predictions.xlsx"
    pd.concat(frames, ignore_index=True).to_excel(all_path, index=False)
    csv_path = root / "all_equipment_predictions.csv"
    pd.concat(frames, ignore_index=True).to_csv(csv_path, index=False, encoding="utf-8-sig")
    return {
        "out_dir": str(root),
        "files": summary,
        "combined_xlsx": str(all_path),
        "combined_csv": str(csv_path),
        "channel": "unlabeled_test_export_not_blind_eval",
        "model_id": svc.manifest.get("model_id"),
    }
