# L3 功能冻结声明

**冻结范围**：L3 Core v1 + L3 Knowledge v1 + L3 Reasoning v1  
**冻结时间**：2026-11-02  
**状态**：功能冻结（不再扩展新分析器 / 知识库能力 / L4 / L5）

## 已冻结能力

- L3 Core：数值分析、EvidenceBundle、AnalysisRun、ReasoningTrace（计算链）
- L3 Knowledge：快照、图谱查询、Hybrid RAG、KnowledgeEnrichment
- L3 Reasoning：按需 Tool Calling、假设复核、语义校验、审计字段、Workbench 展示

## 收尾约束（冻结前已落实）

1. 通用异常证据不得单独升级为具体故障 SUPPORTED  
2. 轴承单特征不突出不得整体 CONTRADICTED  
3. `hypothesis_origin` 严格枚举（可白名单别名归一）  
4. outcome 区分：`live_llm_success` / `insufficient_evidence` / `llm_invalid_json_fallback` / `llm_failed_fallback` / `dry_run`  
5. 默认 `L3_REASONING_MAX_OUTPUT_TOKENS=4096`（可环境覆盖；不保证永不截断）  
6. ReasoningRun/Record 持久化 token 与工具计数（Usage 缺失则为 null）  

## 明确不做

- 不开发新 Analyzer  
- 不扩知识库入库平台  
- 不进入 L4 根因 / L5 维修决策  

旧 ReasoningRun 保持可读；新结果使用新 outcome 语义，不覆盖历史行内容。
