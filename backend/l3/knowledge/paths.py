"""知识源路径（复用项目既有 data/official 与 processed）。"""

from __future__ import annotations

from pathlib import Path

from l2.inference.config import PROJECT_ROOT

OFFICIAL_DIR = PROJECT_ROOT / "data" / "official"
MARKDOWN_DIR = PROJECT_ROOT / "data" / "processed" / "markdown"
GRAPH_PATH = PROJECT_ROOT / "data" / "processed" / "graph" / "graph.json"
RAG_INDEX_PATH = PROJECT_ROOT / "data" / "processed" / "rag" / "index.json"
KNOWLEDGE_RUNTIME = PROJECT_ROOT / "data" / "runtime" / "l3_knowledge"
SNAPSHOT_DIR = KNOWLEDGE_RUNTIME / "snapshots"
VECTOR_DIR = KNOWLEDGE_RUNTIME / "vectors"

KB_DOCUMENTS = [
    "KB-01_设备知识图谱",
    "KB-02_故障模式诊断知识库",
    "KB-03_操作规程与技术手册",
    "KB-04_专家经验与典型案例",
    "KB-05_设备关联关系",
    "KB-06_RAG检索知识库",
]


def official_docx(stem: str) -> Path:
    return OFFICIAL_DIR / f"{stem}.docx"


def markdown_path(stem: str) -> Path:
    return MARKDOWN_DIR / f"{stem}.md"
