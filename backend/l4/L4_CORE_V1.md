# L4 Core v1 + Causal Knowledge v1

**状态**：首个可运行确定性版本（无 LLM）  
**模块**：`l4_core_v1`  
**Schema**：`l4_root_cause_assessment_v1`  
**因果知识**：`l4_causal_knowledge_v1`（独立 JSON，不修改 `graph.json`）

## 边界

- 只读消费 L3 EvidenceBundle / 可选 Reasoning / Enrichment
- 不调用 DeepSeek / 不重算 L3 数值证据
- 根因确认状态固定 `unconfirmed`
- 拓扑 `CONNECTS_TO` → 潜在影响；`propagation_status=unknown`
- 不输出影响程度等级；不读 Excel 答案列
- 单次 Run 默认最多 200 条（`L4_MAX_RECORDS_PER_RUN`），禁止无界补跑

## API

| 方法 | 路径 |
|---|---|
| GET | `/l4/status` |
| GET | `/l4/overview` |
| POST | `/l4/runs` |
| GET | `/l4/runs/{id}` |
| GET | `/l4/by-analysis/{analysis_run_id}` |
| GET | `/l4/runs/{id}/results` |
| GET | `/l4/runs/{id}/records/{record_id}` |
| GET | `/l4/mapping` |
