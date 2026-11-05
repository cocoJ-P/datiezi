"""将确认后的草案持久化为 EquipmentSpec（运行时配置目录）。"""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Any

import yaml

from l2.equipment.registry import CONFIG_DIR, get_equipment_spec, has_equipment, load_all_specs
from l2.inference.config import PROJECT_ROOT

DEFAULT_RUNTIME_DIR = PROJECT_ROOT / "data" / "runtime" / "l2_equipment"
DEFAULT_TRAINING_TEMPLATE = (
    PROJECT_ROOT / "backend" / "l2" / "training" / "config" / "high_pressure_descaling_pump.yaml"
)


def runtime_equipment_dir() -> Path:
    raw = os.getenv("L2_EQUIPMENT_RUNTIME_DIR", "").strip()
    path = Path(raw) if raw else DEFAULT_RUNTIME_DIR
    if not path.is_absolute():
        path = (PROJECT_ROOT / path).resolve()
    path.mkdir(parents=True, exist_ok=True)
    (path / "training").mkdir(parents=True, exist_ok=True)
    return path


def register_equipment_spec(draft: dict[str, Any]) -> dict[str, Any]:
    equipment_type = str(draft["equipment_type_candidate"]).strip()
    display_name = str(draft["display_name_candidate"]).strip()
    selected = [f for f in (draft.get("feature_candidates") or []) if f.get("selected")]
    if not selected:
        raise ValueError("没有可注册的特征。")

    feature_names = [str(f.get("name") or f["feature_id"]) for f in selected]
    class_labels = [str(x) for x in (draft.get("class_labels") or [])]
    normal_label = str(draft.get("normal_label_candidate") or "正常")
    if normal_label not in class_labels:
        class_labels = [normal_label, *class_labels]

    # Schema 版本：已有同特征集合则复用；否则递增
    schema_version, reused = _resolve_schema_version(equipment_type, feature_names)
    if reused:
        spec = get_equipment_spec(equipment_type, refresh=True)
        _ensure_mapping(
            equipment_type=equipment_type,
            display_name=display_name,
            category=draft.get("equipment_category"),
            dataset_id=draft.get("dataset_id"),
            filename=draft.get("original_filename"),
        )
        return {
            "reused": True,
            "equipment_type": equipment_type,
            "display_name": spec.display_name,
            "schema_version": spec.schema_version,
            "feature_count": spec.feature_count,
            "class_count": spec.class_count,
            "config_path": str(_spec_path(equipment_type)),
        }

    runtime_dir = runtime_equipment_dir()
    training_path = runtime_dir / "training" / f"{equipment_type}.yaml"
    try:
        training_rel = str(training_path.resolve().relative_to(PROJECT_ROOT.resolve())).replace("\\", "/")
    except ValueError:
        training_rel = str(training_path.resolve())
    _write_training_config(training_path, equipment_type=equipment_type)

    payload = {
        "equipment_type": equipment_type,
        "display_name": display_name,
        "schema_version": schema_version,
        "record_id_field": "记录编号",
        "equipment_id_field": "设备编号",
        "timestamp_field": "采集时间",
        "status_field": "设备状态",
        "normal_label": normal_label,
        "abnormal_label": "非正常",
        "target_field": str(draft.get("target_candidate") or "故障类型"),
        "training_config": training_rel.replace("\\", "/"),
        "features": [
            {
                "name": str(item.get("name") or item["feature_id"]),
                "dtype": str(item.get("dtype") or "float"),
                "unit": item.get("unit"),
                "required": bool(item.get("required", True)),
                "feature_id": str(item.get("feature_id") or item.get("name")),
            }
            for item in selected
        ],
        "class_labels": class_labels,
        "binary_labels": [normal_label, "非正常"],
        "leakage_columns": list(draft.get("leakage_columns") or []),
        "onboarding": {
            "source_pd_revision": draft.get("source_pd_revision"),
            "dataset_id": draft.get("dataset_id"),
            "warnings": draft.get("warnings") or [],
        },
    }
    config_path = _spec_path(equipment_type)
    _atomic_write_yaml(config_path, payload)
    _ensure_mapping(
        equipment_type=equipment_type,
        display_name=display_name,
        category=draft.get("equipment_category"),
        dataset_id=draft.get("dataset_id"),
        filename=draft.get("original_filename"),
    )
    load_all_specs(refresh=True)
    return {
        "reused": False,
        "equipment_type": equipment_type,
        "display_name": display_name,
        "schema_version": schema_version,
        "feature_count": len(selected),
        "class_count": len(class_labels),
        "config_path": str(config_path),
        "training_config": training_rel.replace("\\", "/"),
    }


def _resolve_schema_version(equipment_type: str, feature_names: list[str]) -> tuple[str, bool]:
    if not has_equipment(equipment_type):
        return f"{equipment_type}_features_v1", False
    existing = get_equipment_spec(equipment_type, refresh=True)
    if sorted(existing.feature_names) == sorted(feature_names):
        return existing.schema_version, True
    # 递增版本号
    base = f"{equipment_type}_features_v"
    version = 2
    current = existing.schema_version
    if current.startswith(base):
        suffix = current[len(base) :]
        if suffix.isdigit():
            version = int(suffix) + 1
    return f"{base}{version}", False


def _spec_path(equipment_type: str) -> Path:
    # 内置配置不覆盖；运行时写入 runtime
    builtin = CONFIG_DIR / f"{equipment_type}.yaml"
    if builtin.is_file():
        # Schema 变化时写到 runtime 同名文件，registry 优先 runtime
        return runtime_equipment_dir() / f"{equipment_type}.yaml"
    return runtime_equipment_dir() / f"{equipment_type}.yaml"


def _write_training_config(path: Path, *, equipment_type: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        return
    if DEFAULT_TRAINING_TEMPLATE.is_file():
        text = DEFAULT_TRAINING_TEMPLATE.read_text(encoding="utf-8")
        text = text.replace("high_pressure_descaling_pump", equipment_type)
        path.write_text(text, encoding="utf-8")
        return
    payload = {
        "equipment_type": equipment_type,
        "dataset_id": f"{equipment_type}_dev",
        "dataset_revision_id": "rev1",
        "random_seed": 42,
        "split": {"train_ratio": 0.70, "calib_ratio": 0.15, "test_ratio": 0.15},
        "binary_params": {
            "n_estimators": 250,
            "max_depth": 12,
            "min_samples_leaf": 2,
            "random_state": 42,
            "n_jobs": -1,
        },
        "multiclass_params": {
            "iterations": 350,
            "depth": 5,
            "learning_rate": 0.06,
            "loss_function": "MultiClass",
            "random_seed": 42,
            "task_type": "CPU",
            "verbose": False,
            "allow_writing_files": False,
        },
    }
    _atomic_write_yaml(path, payload)


def _ensure_mapping(
    *,
    equipment_type: str,
    display_name: str,
    category: str | None,
    dataset_id: str | None,
    filename: str | None,
) -> None:
    runtime_map = runtime_equipment_dir() / "mappings.yaml"
    payload = {"mappings": []}
    if runtime_map.is_file():
        payload = yaml.safe_load(runtime_map.read_text(encoding="utf-8")) or {"mappings": []}
    mappings = list(payload.get("mappings") or [])

    def _has(match: dict[str, Any]) -> bool:
        for item in mappings:
            if (item.get("match") or {}) == match and item.get("equipment_type") == equipment_type:
                return True
        return False

    changed = False
    if category:
        match = {"category_equals": str(category)}
        if not _has(match):
            mappings.append({"match": match, "equipment_type": equipment_type})
            changed = True
    if dataset_id:
        match = {"dataset_id": str(dataset_id)}
        if not _has(match):
            mappings.append({"match": match, "equipment_type": equipment_type})
            changed = True
    if filename:
        stem = Path(filename).stem
        if stem:
            match = {"filename_contains": stem}
            if not _has(match):
                mappings.append({"match": match, "equipment_type": equipment_type})
                changed = True
    if display_name and display_name != category:
        match = {"category_equals": display_name}
        if not _has(match):
            mappings.append({"match": match, "equipment_type": equipment_type})
            changed = True
    if changed:
        _atomic_write_yaml(runtime_map, {"mappings": mappings})


def _atomic_write_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name, dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        Path(tmp_name).replace(path)
    finally:
        if Path(tmp_name).exists():
            Path(tmp_name).unlink(missing_ok=True)


def draft_fingerprint(draft: dict[str, Any]) -> str:
    selected = sorted(
        str(f.get("feature_id"))
        for f in (draft.get("feature_candidates") or [])
        if f.get("selected")
    )
    raw = "|".join(
        [
            str(draft.get("equipment_type_candidate") or ""),
            str(draft.get("normal_label_candidate") or ""),
            ",".join(selected),
            ",".join(str(x) for x in (draft.get("class_labels") or [])),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
