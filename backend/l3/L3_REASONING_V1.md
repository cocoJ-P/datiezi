# L3 Reasoning v1｜冻结说明

**状态**：功能冻结（见 `L3_FREEZE.md`；不自动进入 L4/L5）  
**模块**：`l3_reasoning_v1`  
**Prompt**：`l3_reasoning_prompt_v1`  
**工具注册表**：`l3_reasoning_tools_v1`  
**策略**：`l3_reasoning_policy_v1`  
**默认 max_output_tokens**：4096（可用 `L3_REASONING_MAX_OUTPUT_TOKENS` 覆盖）

## 架构要点

- Core 全量执行；Reasoning **按需**（CONFLICT / UNCERTAIN / 证据冲突等优先；充分一致的 ABNORMAL 可跳过）。
- 单智能体 Tool Calling；复用 DeepSeek OpenAI-compatible 客户端。
- 工具只读白名单：`get_l2_diagnosis` / `get_l3_evidence` / `get_reasoning_trace` / `search_fault_knowledge` / `query_equipment_graph` / `get_related_cases`。
- 结果经程序化校验；非法引用不可标为成功。
- `ReasoningRun` 独立 SQLite，不覆盖 AnalysisRun / EvidenceBundle / KnowledgeEnrichment。
- Trace 覆盖层增加 `HYPOTHESIS` / `TOOL_RESULT`。
- 禁止输出最终根因、维修、优先级、影响程度。

## API

| 方法 | 路径 |
|---|---|
| GET | `/l3/reasoning/status` |
| POST | `/l3/reasoning/runs` |
| GET | `/l3/reasoning/runs/{id}` |
| GET | `/l3/reasoning/runs/{id}/results` |
| GET | `/l3/reasoning/runs/{id}/records/{record_id}` |
| GET | `/l3/reasoning/by-analysis/{analysis_run_id}` |

`POST` body 支持 `automatic_selection` / `manual_request` / `record_ids` / `dry_run` / `force_new`。

## 配置（backend/.env）

见 `.env.example` 中 `L3_REASONING_*` 与既有 `DEEPSEEK_API_KEY`。
