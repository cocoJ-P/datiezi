"""模型产物读写。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import joblib

from l5.prediction.preprocess import FeaturePreprocessor


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def save_bundle(
    bundle_dir: Path,
    *,
    manifest: dict[str, Any],
    preprocessor: FeaturePreprocessor,
    models: dict[str, Any],
    vocabularies: dict[str, Any],
    template_dictionaries: dict[str, Any],
    metrics: dict[str, Any],
    split_manifests: list[dict[str, Any]],
) -> Path:
    root = ensure_dir(bundle_dir)
    write_json(root / "manifest.json", manifest)
    write_json(root / "preprocessor.json", preprocessor.to_dict())
    write_json(root / "vocabularies.json", vocabularies)
    write_json(root / "template_dictionaries.json", template_dictionaries)
    write_json(root / "metrics.json", metrics)
    # split manifests without huge duplication optional — keep full as required
    write_json(root / "splits.json", {"splits": split_manifests})
    model_dir = ensure_dir(root / "models")
    key_map = {}
    for key, model in models.items():
        safe = _safe_model_filename(key)
        key_map[key] = safe
        joblib.dump(model, model_dir / f"{safe}.joblib")
    index = {"model_keys": sorted(models.keys()), "key_to_filename": key_map}
    write_json(root / "model_index.json", index)
    return root


def _safe_model_filename(key: str) -> str:
    # Windows 禁止 : * ? 等；逻辑键仍用 equipment::target
    return (
        key.replace("::", "__")
        .replace("/", "_")
        .replace("\\", "_")
        .replace(":", "_")
        .replace("*", "_")
        .replace("?", "_")
        .replace('"', "_")
        .replace("<", "_")
        .replace(">", "_")
        .replace("|", "_")
    )


def load_bundle(bundle_dir: Path) -> dict[str, Any]:
    root = Path(bundle_dir)
    manifest = read_json(root / "manifest.json")
    preprocessor = FeaturePreprocessor.from_dict(read_json(root / "preprocessor.json"))
    vocabularies = read_json(root / "vocabularies.json")
    template_dictionaries = read_json(root / "template_dictionaries.json")
    metrics = read_json(root / "metrics.json")
    index = read_json(root / "model_index.json")
    models = {}
    model_dir = root / "models"
    key_map = index.get("key_to_filename") or {}
    for key in index["model_keys"]:
        safe = key_map.get(key) or _safe_model_filename(key)
        models[key] = joblib.load(model_dir / f"{safe}.joblib")
    return {
        "manifest": manifest,
        "preprocessor": preprocessor,
        "vocabularies": vocabularies,
        "template_dictionaries": template_dictionaries,
        "metrics": metrics,
        "models": models,
        "bundle_dir": str(root),
    }
