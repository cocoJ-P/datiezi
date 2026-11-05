"""不可变知识快照。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from l3.knowledge import GRAPH_QUERY_VERSION, KNOWLEDGE_EXTRACT_VERSION, RETRIEVER_VERSION
from l3.knowledge.import_docs import import_knowledge_corpus
from l3.knowledge.paths import SNAPSHOT_DIR


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_snapshot(*, force_new: bool = False) -> dict[str, Any]:
    corpus = import_knowledge_corpus()
    payload = {
        "extract_version": KNOWLEDGE_EXTRACT_VERSION,
        "graph_query_version": GRAPH_QUERY_VERSION,
        "retriever_version": RETRIEVER_VERSION,
        "source_hashes": corpus["source_hashes"],
        "item_count": corpus["item_count"],
        "duplicate_alias_count": corpus["duplicate_alias_count"],
        "conflicts": corpus["conflicts"],
        "pending_documents": corpus["pending_documents"],
        "items": corpus["items"],
        "source_status": corpus["source_status"],
    }
    digest = hashlib.sha256(
        json.dumps(
            {
                "extract_version": payload["extract_version"],
                "source_hashes": payload["source_hashes"],
                "item_count": payload["item_count"],
                "ids": sorted(i["knowledge_id"] for i in payload["items"]),
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:16]
    snapshot_id = f"KS-{digest}"
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = SNAPSHOT_DIR / f"{snapshot_id}.json"
    if path.is_file() and not force_new:
        return json.loads(path.read_text(encoding="utf-8"))
    body = {
        "knowledge_snapshot_id": snapshot_id,
        "created_at": _now(),
        **payload,
    }
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    latest = SNAPSHOT_DIR / "latest.json"
    latest.write_text(json.dumps({"knowledge_snapshot_id": snapshot_id, "path": str(path)}, ensure_ascii=False, indent=2), encoding="utf-8")
    return body


def load_snapshot(snapshot_id: str | None = None) -> dict[str, Any]:
    if snapshot_id:
        path = SNAPSHOT_DIR / f"{snapshot_id}.json"
        if not path.is_file():
            raise KeyError(snapshot_id)
        return json.loads(path.read_text(encoding="utf-8"))
    latest = SNAPSHOT_DIR / "latest.json"
    if latest.is_file():
        meta = json.loads(latest.read_text(encoding="utf-8"))
        return load_snapshot(meta["knowledge_snapshot_id"])
    return build_snapshot()


def list_snapshots() -> list[dict[str, Any]]:
    if not SNAPSHOT_DIR.is_dir():
        return []
    out = []
    for path in sorted(SNAPSHOT_DIR.glob("KS-*.json")):
        body = json.loads(path.read_text(encoding="utf-8"))
        out.append(
            {
                "knowledge_snapshot_id": body["knowledge_snapshot_id"],
                "created_at": body.get("created_at"),
                "item_count": body.get("item_count"),
                "path": str(path),
            }
        )
    return out
