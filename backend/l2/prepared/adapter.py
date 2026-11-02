"""PreparedDatasetAdapter：读取 L2 快照供训练/推理。"""

from __future__ import annotations

from typing import Any

import pandas as pd

from l2.equipment.registry import get_equipment_spec
from l2.equipment.validation import PreparedEquipmentDataset, validate_prepared_dataset
from l2.prepared.catalog import get_published_dataset, resolve_bundle_dir
from l2.prepared.contract import read_manifest, sha256_file
from l2.training.data_adapter import TrainingSnapshot


class PreparedDatasetAdapter:
    def __init__(self, prepared_dataset_id: str):
        self.prepared_dataset_id = prepared_dataset_id
        self.meta = get_published_dataset(prepared_dataset_id)
        self.bundle_dir = resolve_bundle_dir(prepared_dataset_id)
        self.manifest = read_manifest(self.bundle_dir / "manifest.json")

    def get_dataset_metadata(self) -> dict[str, Any]:
        return dict(self.meta)

    def validate_dataset(self) -> dict[str, Any]:
        hashes = self.manifest.get("content_hashes") or {}
        for name, expected in hashes.items():
            if name == "content_key":
                continue
            path = self.bundle_dir / name
            if not path.is_file():
                raise FileNotFoundError(f"缺少快照文件：{name}")
            actual = sha256_file(path)
            if actual != expected:
                raise ValueError(f"快照哈希不匹配：{name}")
        return {"ok": True, "prepared_dataset_id": self.prepared_dataset_id}

    def load_features(self) -> pd.DataFrame:
        return pd.read_csv(self.bundle_dir / "features.csv", encoding="utf-8-sig")

    def load_labels(self) -> pd.DataFrame:
        return pd.read_csv(self.bundle_dir / "labels.csv", encoding="utf-8-sig")

    def load_metadata(self) -> pd.DataFrame:
        return pd.read_csv(self.bundle_dir / "metadata.csv", encoding="utf-8-sig")

    def get_record(self, record_id: str) -> dict[str, Any]:
        features = self.load_features()
        spec = get_equipment_spec(str(self.meta["equipment_type"]))
        id_col = spec.record_id_field
        matched = features[features[id_col].astype(str) == str(record_id)]
        if matched.empty:
            raise KeyError(record_id)
        row = matched.iloc[0]
        feature_values = {name: float(row[name]) for name in spec.feature_names}
        return {
            "record_id": str(record_id),
            "equipment_type": spec.equipment_type,
            "features": feature_values,
            "prepared_dataset_id": self.prepared_dataset_id,
            "dataset_revision_id": self.meta.get("revision_id"),
            "schema_version": self.meta.get("schema_version"),
        }

    def get_records(self, record_ids: list[str]) -> list[dict[str, Any]]:
        return [self.get_record(rid) for rid in record_ids]

    def load_training_snapshot(self) -> TrainingSnapshot:
        self.validate_dataset()
        if not self.meta.get("training_ready"):
            raise ValueError("数据集不可用于训练。")
        features = self.load_features()
        labels = self.load_labels()
        spec = get_equipment_spec(str(self.meta["equipment_type"]))
        id_col = spec.record_id_field
        labeled = labels.dropna(subset=[spec.target_field])
        labeled = labeled[labeled[spec.target_field].astype(str).str.strip() != ""]
        frame = features.merge(labeled, on=id_col, how="inner", validate="one_to_one")
        metadata = self.load_metadata()
        meta_id = id_col if id_col in metadata.columns else "record_id"
        if "equipment_id" in metadata.columns:
            meta_view = metadata[[meta_id, "equipment_id"]].copy()
            if meta_id != id_col:
                meta_view = meta_view.rename(columns={meta_id: id_col})
            frame = frame.merge(meta_view, on=id_col, how="left")
        features_path = self.bundle_dir / "features.csv"
        labels_path = self.bundle_dir / "labels.csv"
        source_hash = (
            sha256_file(features_path) + ":" + sha256_file(labels_path)
        )
        import hashlib

        source_hash = hashlib.sha256(source_hash.encode("utf-8")).hexdigest()
        return TrainingSnapshot(
            frame=frame.reset_index(drop=True),
            source_kind="l1_prepared",
            features_path=str(features_path.resolve()),
            labels_path=str(labels_path.resolve()),
            dataset_id=str(self.meta.get("prepared_dataset_id") or self.prepared_dataset_id),
            dataset_revision_id=str(self.meta.get("revision_id") or ""),
            source_data_hash=source_hash,
            warnings=tuple(),
            equipment_type=spec.equipment_type,
            schema_version=spec.schema_version,
        )

    def load_prepared_equipment_dataset(self) -> PreparedEquipmentDataset:
        snapshot = self.load_training_snapshot()
        spec = get_equipment_spec(str(self.meta["equipment_type"]))
        features = snapshot.frame[[spec.record_id_field, *spec.feature_names]].copy()
        labels = snapshot.frame[[spec.record_id_field, spec.target_field]].copy()
        dataset = PreparedEquipmentDataset(
            dataset_id=snapshot.dataset_id,
            dataset_revision_id=snapshot.dataset_revision_id,
            equipment_type=spec.equipment_type,
            schema_version=spec.schema_version,
            feature_schema=spec.feature_schema_dict(),
            features=features,
            labels=labels,
            record_ids=tuple(features[spec.record_id_field].astype(str).tolist()),
            metadata={
                "prepared_dataset_id": self.prepared_dataset_id,
                "source_type": "l1_prepared",
                "manifest": self.manifest,
            },
        )
        validate_prepared_dataset(dataset, spec)
        return dataset
