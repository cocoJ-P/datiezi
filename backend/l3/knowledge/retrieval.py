"""Hybrid RAG：复用既有 BM25 + 可配置向量检索 + RRF 融合。"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

import numpy as np

from l3.knowledge import RETRIEVER_VERSION
from l3.knowledge.paths import VECTOR_DIR
from l3.knowledge.schema import EQUIPMENT_SCOPE_ALIASES, equipment_type_matches_text, scopes_for_equipment_type
from l3.knowledge.snapshot import load_snapshot


def _scope_applicable(equipment_type: str | None, item: dict[str, Any], blob: str) -> tuple[bool, str]:
    """返回 (keep, applicability_status)。明确跨设备条目直接丢弃。"""
    if not equipment_type:
        return True, "unspecified_scope"
    match = equipment_type_matches_text(equipment_type, blob)
    scope_list = item.get("equipment_scope") or []
    if scope_list:
        scope_blob = " ".join(str(x) for x in scope_list)
        aliases = scopes_for_equipment_type(equipment_type)
        if aliases and any(a in scope_blob for a in aliases):
            return True, "applicable"
        # 条目已标注其他设备族 → 不适用
        for et, other_aliases in EQUIPMENT_SCOPE_ALIASES.items():
            if et == equipment_type:
                continue
            if any(a in scope_blob for a in other_aliases):
                return False, "inapplicable_equipment"
        scope_match = equipment_type_matches_text(equipment_type, scope_blob)
        if scope_match is False:
            return False, "inapplicable_equipment"
    if match is False:
        return False, "inapplicable_equipment"
    if match is True:
        return True, "applicable"
    return True, "unspecified_scope"

LOG = logging.getLogger("l3.knowledge.retrieval")

# 过宽通用词：单独命中不足以构成相关知识（避免“故障/设备”刷屏）
_GENERIC_TOKENS = {
    "存在",
    "设备",
    "故障",
    "知识",
    "诊断",
    "案例",
    "典型",
    "检索",
    "系统",
    "关系",
    "参数",
    "异常",
    "特征",
    "分析",
    "结果",
    "信息",
    "基本",
}


def _distinctive_query_tokens(query: str) -> list[str]:
    from app.services.knowledge_search import tokenize

    out: list[str] = []
    for tok in tokenize(query.strip()):
        t = tok.strip().lower()
        if len(t) < 2 or t in _GENERIC_TOKENS:
            continue
        out.append(t)
    return out


def _item_blob(item: dict[str, Any]) -> str:
    return f"{item.get('canonical_name','')}\n{item.get('content','')}".lower()


def _has_distinctive_overlap(blob: str, distinctive: list[str]) -> bool:
    if not distinctive:
        return True
    return any(tok in blob for tok in distinctive)


def _bm25_on_items(query: str, items: list[dict[str, Any]], top_k: int = 20) -> dict[str, Any]:
    """复用 app.services.knowledge_search 的 tokenize/bm25_scores，对 canonical items 检索。"""
    from app.services.knowledge_search import bm25_scores, tokenize

    text = query.strip()
    if not text:
        return {"available": True, "results": [], "count": 0, "query": query}
    chunks = []
    for item in items:
        if item.get("canonical_of"):
            continue
        loc = item.get("source_location") or {}
        chunks.append(
            {
                "id": item["knowledge_id"],
                "document": item.get("source_document_id"),
                "section": loc.get("section") or item.get("canonical_name") or "",
                "locator": loc.get("locator") or "",
                "text": item.get("content") or "",
                "tokens": tokenize(f"{item.get('canonical_name','')}\n{item.get('content','')}"),
            }
        )
    q_tokens = tokenize(text)
    scores = bm25_scores(chunks, q_tokens, text)
    ranked = sorted(
        ((score, chunk) for score, chunk in zip(scores, chunks) if score > 0),
        key=lambda item: (-item[0], item[1]["id"]),
    )[:top_k]
    results = [
        {
            "id": chunk["id"],
            "knowledge_id": chunk["id"],
            "text": chunk["text"],
            "document": chunk["document"],
            "section": chunk["section"],
            "locator": chunk["locator"],
            "score": round(score, 4),
        }
        for score, chunk in ranked
    ]
    return {"available": True, "results": results, "count": len(results), "query": query, "engine": "bm25_jieba_reused"}


class EmbeddingAdapter:
    """可替换嵌入后端。默认使用 sklearn TF-IDF（无需 GPU）。"""

    def __init__(self, backend: str = "tfidf"):
        self.backend = backend
        self._vectorizer = None
        self._matrix = None
        self._ids: list[str] = []
        self.available = False
        self.status = "not_built"
        self.model_name = backend
        self.model_version = "tfidf_sklearn_1.5"

    def build(self, texts: list[str], ids: list[str]) -> None:
        if self.backend == "sentence_transformers":
            try:
                from sentence_transformers import SentenceTransformer  # type: ignore

                model = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
                emb = model.encode(texts, normalize_embeddings=True)
                self._matrix = np.asarray(emb, dtype=np.float32)
                self._ids = list(ids)
                self.available = True
                self.status = "ready"
                self.model_name = "paraphrase-multilingual-MiniLM-L12-v2"
                self.model_version = "st"
                return
            except Exception as exc:  # noqa: BLE001
                LOG.warning("sentence_transformers unavailable: %s; fallback tfidf", exc)
                self.backend = "tfidf"
        # tfidf
        from sklearn.feature_extraction.text import TfidfVectorizer

        self._vectorizer = TfidfVectorizer(max_features=8192)
        self._matrix = self._vectorizer.fit_transform(texts)
        self._ids = list(ids)
        self.available = True
        self.status = "ready"
        self.model_name = "tfidf_sklearn"
        self.model_version = "1.0"

    def search(self, query: str, top_k: int = 10) -> list[tuple[str, float]]:
        if not self.available or self._matrix is None:
            return []
        if self.backend == "sentence_transformers":
            # not used in default path; keep simple
            return []
        assert self._vectorizer is not None
        q = self._vectorizer.transform([query])
        scores = (self._matrix @ q.T).toarray().ravel()
        order = np.argsort(-scores)[:top_k]
        out = []
        for i in order:
            s = float(scores[i])
            if s <= 0:
                continue
            out.append((self._ids[int(i)], s))
        return out


def _vector_cache_key(snapshot_id: str, backend: str) -> Path:
    VECTOR_DIR.mkdir(parents=True, exist_ok=True)
    return VECTOR_DIR / f"{snapshot_id}_{backend}.meta.json"


def get_or_build_vector_index(snapshot: dict[str, Any] | None = None, *, backend: str = "tfidf") -> EmbeddingAdapter:
    snap = snapshot or load_snapshot()
    adapter = EmbeddingAdapter(backend=backend)
    # 仅索引非 alias 条目，避免 KB-06 重复计分
    items = [i for i in snap["items"] if not i.get("canonical_of")]
    ids = [i["knowledge_id"] for i in items]
    texts = [f"{i.get('canonical_name','')}\n{i.get('content','')}" for i in items]
    meta_path = _vector_cache_key(snap["knowledge_snapshot_id"], backend)
    # 简化：每次进程内重建；元数据记录模型版本以便追溯
    adapter.build(texts, ids)
    meta_path.write_text(
        json.dumps(
            {
                "knowledge_snapshot_id": snap["knowledge_snapshot_id"],
                "backend": adapter.backend,
                "model_name": adapter.model_name,
                "model_version": adapter.model_version,
                "item_count": len(ids),
                "status": adapter.status,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return adapter


def rrf_fuse(
    ranked_lists: list[list[str]],
    *,
    k: int = 60,
    top_k: int = 10,
) -> list[tuple[str, float]]:
    scores: dict[str, float] = {}
    for ranked in ranked_lists:
        for rank, kid in enumerate(ranked, start=1):
            scores[kid] = scores.get(kid, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda x: (-x[1], x[0]))[:top_k]


def hybrid_search(
    *,
    query: str,
    equipment_type: str | None = None,
    fault_mode: str | None = None,
    top_k: int = 8,
    snapshot_id: str | None = None,
) -> dict[str, Any]:
    snap = load_snapshot(snapshot_id)
    by_id = {i["knowledge_id"]: i for i in snap["items"]}
    text = query.strip()
    if fault_mode and fault_mode not in text:
        text = f"{text} {fault_mode}".strip()

    bm25 = _bm25_on_items(text, snap["items"], top_k=min(20, max(top_k * 2, 5)))
    bm25_available = bool(bm25.get("available"))
    bm25_ids = [str(h["knowledge_id"]) for h in bm25.get("results") or []]
    bm25_hits = [{"knowledge_id": h["knowledge_id"], "score": h.get("score"), "via": "bm25"} for h in bm25.get("results") or []]

    semantic_status = "ready"
    vector_ids: list[str] = []
    vector_hits = []
    try:
        adapter = get_or_build_vector_index(snap, backend="tfidf")
        if not adapter.available:
            semantic_status = "semantic_search_unavailable"
        else:
            for kid, score in adapter.search(text, top_k=min(20, max(top_k * 2, 5))):
                vector_ids.append(kid)
                vector_hits.append({"knowledge_id": kid, "score": score, "via": "tfidf_vector"})
    except Exception as exc:  # noqa: BLE001
        semantic_status = "semantic_search_unavailable"
        LOG.warning("vector search failed: %s", exc)

    mode = "hybrid_rrf"
    if semantic_status != "ready" and bm25_available:
        mode = "bm25_only"
    elif semantic_status == "ready" and not bm25_available:
        mode = "vector_only"
    elif semantic_status != "ready" and not bm25_available:
        mode = "unavailable"

    if mode == "hybrid_rrf":
        fused = rrf_fuse([bm25_ids, vector_ids], top_k=top_k * 3)
    elif mode == "bm25_only":
        fused = [(i, float(top_k - r)) for r, i in enumerate(bm25_ids[: top_k * 3])]
    elif mode == "vector_only":
        fused = [(i, float(top_k - r)) for r, i in enumerate(vector_ids[: top_k * 3])]
    else:
        fused = []

    distinctive = _distinctive_query_tokens(text)
    # 查询含特异词但全库无任何条目包含时，直接无匹配（禁止靠“故障/设备”泛词填充）
    if distinctive:
        any_corpus_hit = any(_has_distinctive_overlap(_item_blob(i), distinctive) for i in snap["items"] if not i.get("canonical_of"))
        if not any_corpus_hit:
            return {
                "query": query,
                "effective_query": text,
                "equipment_type": equipment_type,
                "fault_mode": fault_mode,
                "top_k": top_k,
                "retriever_version": RETRIEVER_VERSION,
                "knowledge_snapshot_id": snap["knowledge_snapshot_id"],
                "mode": mode,
                "bm25": {"available": bm25_available, "hit_count": len(bm25_ids), "raw_count": bm25.get("count", 0)},
                "semantic": {"status": semantic_status, "hit_count": len(vector_ids), "backend": "tfidf_sklearn"},
                "count": 0,
                "message": "no_relevant_knowledge",
                "results": [],
                "filter": {"distinctive_tokens": distinctive, "reason": "no_corpus_token_overlap"},
            }

    results = []
    for kid, score in fused:
        item = by_id.get(kid)
        if not item:
            continue
        if item.get("canonical_of"):
            # 不把 KB-06 重复别名当独立证据
            continue
        item_text = _item_blob(item)
        if distinctive and not _has_distinctive_overlap(item_text, distinctive):
            continue
        blob = f"{item.get('canonical_name','')}\n{item.get('content','')}\n{' '.join(item.get('equipment_scope') or [])}"
        keep, applicability = _scope_applicable(equipment_type, item, blob)
        if not keep:
            continue
        if fault_mode and fault_mode not in blob and fault_mode not in (item.get("fault_mode_scope") or []):
            # soft keep if unspecified
            pass
        results.append(
            {
                "knowledge_id": kid,
                "knowledge_type": item.get("knowledge_type"),
                "canonical_name": item.get("canonical_name"),
                "content_summary": (item.get("content") or "")[:280],
                "source_document_id": item.get("source_document_id"),
                "source_location": item.get("source_location"),
                "equipment_scope": item.get("equipment_scope"),
                "fault_mode_scope": item.get("fault_mode_scope"),
                "validation_status": item.get("validation_status"),
                "applicability_status": applicability,
                "retrieval_score": round(float(score), 6),
                "note": "检索相似度不等于诊断置信度",
            }
        )
        if len(results) >= top_k:
            break

    return {
        "query": query,
        "effective_query": text,
        "equipment_type": equipment_type,
        "fault_mode": fault_mode,
        "top_k": top_k,
        "retriever_version": RETRIEVER_VERSION,
        "knowledge_snapshot_id": snap["knowledge_snapshot_id"],
        "mode": mode,
        "bm25": {"available": bm25_available, "hit_count": len(bm25_ids), "raw_count": bm25.get("count", 0)},
        "semantic": {"status": semantic_status, "hit_count": len(vector_ids), "backend": "tfidf_sklearn"},
        "count": len(results),
        "message": "检索完成。" if results else "no_relevant_knowledge",
        "results": results,
        "filter": {"distinctive_tokens": distinctive},
    }
