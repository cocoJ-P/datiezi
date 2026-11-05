# L3 Knowledge v1｜冻结说明

**状态**：已完成（本阶段停止，不自动进入 L3 Reasoning / L4 / L5）  
**快照**：`KS-757bbb5b681923d8`  
**模块版本**：`l3_knowledge_v1`  
**检索器**：`hybrid_rag_v1`（BM25 jieba 复用 + TF-IDF 向量 + RRF）  
**图谱查询**：`graph_query_v1`（只读 `data/processed/graph/graph.json`）

## 能力边界

- 六份官方 DOCX（KB-01…06）可导入、可追溯、可快照版本化。
- 设备/部件/故障/特征图谱查询可用；关系**不**视为当前故障已发生。
- Hybrid RAG：设备适用范围过滤 → BM25 + 向量 → RRF → 去重引用。
- `KnowledgeEnrichmentRun` 独立落库，**不覆盖**历史 `EvidenceBundle`。
- ReasoningTrace 可叠加 `KNOWLEDGE_REFERENCE`（覆盖层）；历史无 Trace 时轻量重建，不重算数值。
- **不**做 LLM 根因推理、维修决策、严重度补齐、L4/L5。

## CLI

```bash
cd backend
.\.venv\Scripts\python.exe -m l3.knowledge.cli inspect
.\.venv\Scripts\python.exe -m l3.knowledge.cli import-snapshot
.\.venv\Scripts\python.exe -m l3.knowledge.cli import-snapshot --force-new
```

## 主要 API（只读 / 受控）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/l3/knowledge/status` | 文档、快照、图谱、BM25/向量状态 |
| POST | `/l3/knowledge/snapshots` | 构建知识快照 |
| GET | `/l3/knowledge/search` | Hybrid 检索 |
| GET | `/l3/knowledge/graph/*` | 部件/特征/案例/规程 |
| POST | `/l3/knowledge/enrichment-runs` | 对 AnalysisRun 做知识增强 |
| GET | `/l3/knowledge/by-analysis/{id}` | 按分析运行查增强结果 |

## 给 L3 Reasoning v1 的工具接口（可复用）

1. `hybrid_search` / `GET /l3/knowledge/search`
2. `query_equipment_components` / `query_fault_features` / `query_fault_cases` / `query_rules_for_equipment`
3. `build_knowledge_context` / `KnowledgeEnrichmentRun` 记录
4. Trace 覆盖层中的 `KNOWLEDGE_REFERENCE` 节点（含 `knowledge_id` + 原文 locator）

## 已知限制

- 向量默认后端为 `tfidf_sklearn`（可配置切换 sentence-transformers；不可用时降级 `bm25_only` 并报告 `semantic_search_unavailable`）。
- `high_pressure_descaling_pump` 在 KB 中几乎无专用规程；图谱部件匹配可能为空（正确降级，不得套用电机/冷却泵知识）。
- 通用故障机理（KB-02）常为 `unspecified_scope`：仅作参考，不得单独构成 SUPPORT。
- 本阶段未接入 LLM Tool Calling 运行时，仅预留稳定 API。
