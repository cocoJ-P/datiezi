"""从官方 DOCX 哈希 + 已处理 Markdown 构建 Canonical Knowledge（不修改原始文件）。"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from l3.knowledge import KNOWLEDGE_EXTRACT_VERSION
from l3.knowledge.paths import KB_DOCUMENTS, markdown_path, official_docx
from l3.knowledge.schema import make_knowledge_item

CASE_ID_RE = re.compile(r"(EXP-\d{4}-\d{3})")
FAULT_HINTS = ("轴承磨损", "转子不平衡", "齿轮异常", "润滑故障", "不平衡", "点蚀", "胶合", "剥落")
RULE_HINTS = ("限值", "报警", "℃", "mm/s", "不得超过", "应小于", "阈值")


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def stable_knowledge_id(*parts: str) -> str:
    raw = "\n".join(parts)
    return "K-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def inspect_sources() -> dict[str, Any]:
    docs = []
    for stem in KB_DOCUMENTS:
        docx = official_docx(stem)
        md = markdown_path(stem)
        docs.append(
            {
                "document_id": stem,
                "docx_path": str(docx) if docx.is_file() else None,
                "docx_hash": file_sha256(docx) if docx.is_file() else None,
                "markdown_path": str(md) if md.is_file() else None,
                "markdown_hash": file_sha256(md) if md.is_file() else None,
                "importable": docx.is_file() and md.is_file(),
            }
        )
    return {
        "documents": docs,
        "importable_count": sum(1 for d in docs if d["importable"]),
        "missing": [d["document_id"] for d in docs if not d["importable"]],
    }


def _infer_type(stem: str, section: str, text: str) -> str:
    if stem.startswith("KB-04") or CASE_ID_RE.search(text) or CASE_ID_RE.search(section):
        return "expert_case"
    if stem.startswith("KB-03") or any(h in text for h in RULE_HINTS):
        return "diagnostic_rule"
    if stem.startswith("KB-02") or any(h in section or h in text for h in FAULT_HINTS):
        return "fault_mode"
    if stem.startswith("KB-05"):
        return "equipment_link"
    if stem.startswith("KB-01"):
        return "equipment_fact"
    if stem.startswith("KB-06"):
        return "retrieval_chunk"
    return "retrieval_chunk"


def _scopes_from_text(text: str) -> tuple[list[str], list[str]]:
    equipment: list[str] = []
    faults: list[str] = []
    for name in (
        "棒线主轧机电机",
        "主轧机电机",
        "主电机",
        "高压除鳞泵",
        "除鳞泵",
        "棒线冷却水循环泵",
        "冷却水循环泵",
        "冷却泵",
        "棒线主减速机",
        "主减速机",
        "棒线齿轮箱",
        "齿轮箱",
    ):
        if name in text and name not in equipment:
            equipment.append(name)
    for name in FAULT_HINTS:
        if name in text and name not in faults:
            faults.append(name)
    return equipment, faults


def extract_from_markdown(stem: str, *, markdown_hash: str, docx_hash: str | None) -> list[dict[str, Any]]:
    path = markdown_path(stem)
    lines = path.read_text(encoding="utf-8").splitlines()
    items: list[dict[str, Any]] = []
    stack: list[tuple[int, str]] = []
    buffer: list[tuple[int, str]] = []
    section = ""

    def flush() -> None:
        nonlocal buffer
        if not buffer:
            return
        content_lines = [(n, t) for n, t in buffer if t.strip() and not t.startswith(">")]
        buffer = []
        if not content_lines:
            return
        start, end = content_lines[0][0], content_lines[-1][0]
        body = "\n".join(t for _, t in content_lines).strip()
        if not body:
            return
        current = section or "文档开头"
        kid = stable_knowledge_id(stem, current, str(start), str(end), body[:80])
        eq_scope, fault_scope = _scopes_from_text(current + "\n" + body)
        case_ids = CASE_ID_RE.findall(current + "\n" + body)
        canonical = case_ids[0] if case_ids else (current.split(" / ")[-1] if current else stem)
        items.append(
            make_knowledge_item(
                knowledge_id=kid,
                knowledge_type=_infer_type(stem, current, body),
                canonical_name=canonical[:120],
                content=body,
                source_document_id=stem,
                source_location={
                    "markdown_document": f"{stem}.md",
                    "official_document": f"{stem}.docx",
                    "section": current,
                    "paragraph_index_start": start,
                    "paragraph_index_end": end,
                    "locator": f"段落 第{start}-{end}行",
                    "note": "行号来自 Markdown 衍生文本，非伪造 Word 页码",
                },
                source_hash=markdown_hash,
                knowledge_version=KNOWLEDGE_EXTRACT_VERSION,
                equipment_scope=eq_scope,
                fault_mode_scope=fault_scope,
                validation_status="verified_source",
                extra={
                    "official_docx_hash": docx_hash,
                    "case_ids": case_ids,
                },
            )
        )

    for number, line in enumerate(lines, 1):
        m = re.match(r"^(#{1,6})\s+(.*\S)\s*$", line)
        if m:
            flush()
            level, title = len(m.group(1)), m.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            section = " / ".join(t for _, t in stack)
            buffer = [(number, line)]
            continue
        if stack:
            buffer.append((number, line))
    flush()
    return items


def _case_title_key(text: str) -> str:
    """归一化案例标题，便于 KB-06 与 KB-04 去重。"""
    t = re.sub(r"^EXP-\d{4}-\d{3}[：:]\s*", "", text or "")
    t = re.sub(r"[：:\s]+", "", t)
    t = t.replace("典型案例", "案例")
    return t.strip()


def inherit_case_equipment_scope(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同案例子段落继承主条目的 equipment_scope，避免跨设备误用。"""
    by_case: dict[str, set[str]] = {}
    for item in items:
        scopes = [str(x) for x in (item.get("equipment_scope") or []) if x]
        if not scopes:
            continue
        for cid in (item.get("extra") or {}).get("case_ids") or []:
            by_case.setdefault(str(cid), set()).update(scopes)
        name = str(item.get("canonical_name") or "")
        if name.startswith("EXP-"):
            cid = name.split("：", 1)[0].split(":", 1)[0].strip()
            by_case.setdefault(cid, set()).update(scopes)
    out: list[dict[str, Any]] = []
    for item in items:
        if item.get("equipment_scope"):
            out.append(item)
            continue
        merged: set[str] = set()
        for cid in (item.get("extra") or {}).get("case_ids") or []:
            merged |= by_case.get(str(cid), set())
        name = str(item.get("canonical_name") or "")
        if name.startswith("EXP-"):
            cid = name.split("：", 1)[0].split(":", 1)[0].strip()
            merged |= by_case.get(cid, set())
        if merged:
            item = dict(item)
            item["equipment_scope"] = sorted(merged)
        out.append(item)
    return out


def dedupe_kb06_against_cases(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """KB-06 与 KB-04 重复案例关联到 canonical，不作为独立证据。"""
    case_canon: dict[str, str] = {}
    title_canon: dict[str, str] = {}
    for item in items:
        if item["source_document_id"].startswith("KB-04"):
            for cid in (item.get("extra") or {}).get("case_ids") or []:
                case_canon[cid] = item["knowledge_id"]
            name = item.get("canonical_name") or ""
            if name.startswith("EXP-"):
                case_canon[name] = item["knowledge_id"]
            key = _case_title_key(name)
            if key and len(key) >= 4:
                title_canon.setdefault(key, item["knowledge_id"])
            # 章节名也可能含案例标题
            section = ((item.get("source_location") or {}).get("section") or "")
            sk = _case_title_key(section.split(" / ")[-1] if section else "")
            if sk and len(sk) >= 4:
                title_canon.setdefault(sk, item["knowledge_id"])

    out: list[dict[str, Any]] = []
    dup = 0
    for item in items:
        if item["source_document_id"].startswith("KB-06"):
            linked = None
            for cid in (item.get("extra") or {}).get("case_ids") or []:
                if cid in case_canon:
                    linked = case_canon[cid]
                    break
            if linked is None:
                for cid, kid in case_canon.items():
                    if cid in item["content"] or cid in item["canonical_name"]:
                        linked = kid
                        break
            if linked is None:
                # KB-06 案例段通常无 EXP 编号，按标题对齐 KB-04
                name_key = _case_title_key(item.get("canonical_name") or "")
                if name_key in title_canon:
                    linked = title_canon[name_key]
                else:
                    for title, kid in title_canon.items():
                        if title and (title in name_key or name_key in title):
                            linked = kid
                            break
                        if title and title in (item.get("content") or ""):
                            linked = kid
                            break
            if linked:
                item = dict(item)
                item["canonical_of"] = linked
                item["validation_status"] = "extracted_pending_review"
                item["applicability"] = "alias_of_canonical"
                dup += 1
        out.append(item)
    return out, dup


def import_knowledge_corpus() -> dict[str, Any]:
    status = inspect_sources()
    items: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    pending: list[str] = []
    source_hashes: dict[str, str] = {}

    for doc in status["documents"]:
        stem = doc["document_id"]
        if not doc["importable"]:
            pending.append(stem)
            continue
        source_hashes[f"{stem}.docx"] = doc["docx_hash"]
        source_hashes[f"{stem}.md"] = doc["markdown_hash"]
        extracted = extract_from_markdown(
            stem,
            markdown_hash=doc["markdown_hash"],
            docx_hash=doc["docx_hash"],
        )
        items.extend(extracted)

    items = inherit_case_equipment_scope(items)
    items, dup_count = dedupe_kb06_against_cases(items)

    # 简单冲突：同一设备范围下温度限值数字不同
    temp_rules = [i for i in items if i["knowledge_type"] == "diagnostic_rule" and "℃" in i["content"]]
    seen_nums: dict[str, list[str]] = {}
    for rule in temp_rules:
        nums = re.findall(r"(\d+(?:\.\d+)?)\s*℃", rule["content"])
        key = "|".join(sorted(rule.get("equipment_scope") or ["general"]))
        for n in nums:
            seen_nums.setdefault(f"{key}:{n}", []).append(rule["knowledge_id"])
    # 若同一 scope 出现多个不同温度数字，标记 conflicting 候选
    by_scope: dict[str, set[str]] = {}
    for rule in temp_rules:
        scope = "|".join(sorted(rule.get("equipment_scope") or ["general"]))
        for n in re.findall(r"(\d+(?:\.\d+)?)\s*℃", rule["content"]):
            by_scope.setdefault(scope, set()).add(n)
    for scope, nums in by_scope.items():
        if len(nums) >= 2:
            conflicts.append(
                {
                    "scope": scope,
                    "kind": "temperature_threshold_divergence",
                    "values": sorted(nums),
                    "note": "通用经验与设备规程可能不同，不得自动选较大/较小值",
                }
            )

    # 稳定去重 knowledge_id
    unique: dict[str, dict[str, Any]] = {}
    for item in items:
        unique[item["knowledge_id"]] = item

    return {
        "extract_version": KNOWLEDGE_EXTRACT_VERSION,
        "source_status": status,
        "source_hashes": source_hashes,
        "items": list(unique.values()),
        "item_count": len(unique),
        "duplicate_alias_count": dup_count,
        "conflicts": conflicts,
        "pending_documents": pending,
    }
