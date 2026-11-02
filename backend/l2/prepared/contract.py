"""PreparedEquipmentDataset 发布契约（L1→L2）。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class PreparedManifest:
    prepared_dataset_id: str
    dataset_id: str
    revision_id: str
    equipment_type: str
    equipment_group_id: str | None
    schema_version: str
    source_stage: str
    source_revision_ids: dict[str, str]
    created_at: str
    record_count: int
    trainable_record_count: int
    feature_count: int
    label_count: int
    class_labels: list[str]
    release_status: str
    release_level: str
    training_ready: bool
    inference_ready: bool
    source_type: str
    l1_prepared_version_id: str | None
    validation_summary: dict[str, Any]
    content_hashes: dict[str, str]
    artifact_files: dict[str, str]
    generator_version: str = "l1-l2-prepared-v1"
    warning_count: int = 0
    excluded_record_count: int = 0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def manifest_hash(self) -> str:
        payload = {k: v for k, v in self.to_dict().items() if k != "content_hashes"}
        # content_hashes 参与哈希，但排除自身 manifest_hash 循环
        payload["content_hashes"] = self.content_hashes
        return sha256_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))

    def write(self, path: Path) -> None:
        body = self.to_dict()
        body["manifest_hash"] = self.manifest_hash()
        path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")


def read_manifest(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
