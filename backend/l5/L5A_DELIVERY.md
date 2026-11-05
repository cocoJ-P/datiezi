# L5a｜综合诊断报告、验证建议与全链路证据交付

## A. 修改文件

**新增**

- `backend/l5/`：`schema`、`input_adapter`、`aggregator`、`recommendations`、`provenance`、`database`、`store`、`jobs`、`service`、`api/router`
- `backend/l5/tests/test_l5a.py`
- `frontend/src/report/api.ts`
- 报告中心改为真实 L5 + 演示隔离

**修改**

- `backend/app/main.py`：注册 `/l5`
- `frontend/src/report/ReportCenterPage.tsx`：默认真实 L5Run；Demo 独立模式
- `frontend/src/report/reportData.ts`：标明静态演示

**未修改**：L1–L4 分析逻辑、知识目录、PED、答案列。

## B. 跨层输入聚合

`build_l5_input_context` 要求显式 `diagnosis_run_id` / `analysis_run_id` / `l4_run_id`（及可选 Reasoning/Enrichment）。

校验：

- L4.`source_analysis_run_id` ≡ 请求 analysis
- Analysis.`source_diagnosis_run_id` ≡ 请求 diagnosis
- `model_bundle_id` / `prepared_dataset_id` / `equipment_type` 一致
- 无效 L4 ID 在入队阶段 `RuntimeError` 拒绝

可选层缺失记入 `source_availability` + `limitations`，不伪装完整。

真实例：`REC-DP-20260314-00021` → `DR-8c6cde64a303` / `AR-82c631a14fd1` / `L4R-2b3535935e05`。

## C. 十字段实际输出（摘录）

| 记录 | 设备状态 | 严重度 | 优先级 | 影响程度 | 置信度 | 根因确认 | 验证建议数 |
|---|---|---|---|---|---|---|---|
| REC-DP-…-00021 | ABNORMAL | null | null | null | null | unconfirmed | 13 |
| REC-DP-…-00162 | UNCERTAIN | null | null | null | null | unconfirmed | 12 |
| REC-MA-…-00008 | ABNORMAL | null | null | null | null | unconfirmed | 19 |

null 原因：`no_validated_severity_scale` / `no_validated_p0_p3_rules` / `no_validated_impact_degree_scale` / `no_unified_confidence_definition`。  
**未用 Demo 填补。**

## D. 验证建议

来源：L3 `missing_evidence` / spectral unverified、L4 candidate/path `missing_evidence` & `required_observations`、可选 L4 Reasoning `unresolved_links`。  
类型仅 `verification`；`condition_id` 标准化去重；`requires_human_review=true`；明确非维修/停机指令。

## E. 证据追溯

`GET /l5/runs/{id}/records/{record_id}/provenance?field=...`  
字段 → source_layer/run → EvidenceItem ID →（根因）CausalKnowledge ID → PED/`model_bundle_id`/`knowledge_snapshot_id`。  
前端「查看证据追溯」展示真实链；不复制整包 L3/L4。

## F. 报告中心

- 默认：**真实 L5Run** 列表 + 十字段 + 验证建议 + 追溯  
- `DR-2026-0314-017` 等仅在「演示数据」模式，显著标注「演示数据」  
- 不展示伪造风险/P1 作为真实报告

## G. L5Run

- DB：`backend/data/l5_analysis.sqlite3`（WAL）  
- content_key 幂等；`force_new` 新历史  
- 状态：queued/running/completed/partial/failed  
- 审查：draft（不自动 approved）  
- 默认 `max_records≤200`

## H. 测试

- pytest `l5/tests`：**6 passed**  
- 真实 e2e：≥5 条报告；L4 hash 不变；无效 L4 入队拒绝  
- 前端 `npm run build`：成功  
- 无 DeepSeek 调用

## I. 局限性

- 无正式风险等级  
- 无 P0–P3 自动决策  
- 无正式维修指令  
- 根因仍 unconfirmed  
- 影响程度未分级  
- 无独立专家一致率  
- L3/L4 Reasoning 未绑定时报告仍可生成但标记缺失

## J. L5a 验收

| 项 | 结果 |
|---|---|
| 十字段固定十项 | 通过 |
| 显式 run 绑定与版本校验 | 通过 |
| 不读答案列 / 不编造 severity/priority/impact/confidence | 通过 |
| L4 unconfirmed 保持 | 通过 |
| 验证建议可追溯 | 通过 |
| Demo 隔离 | 通过 |
| 幂等与单记录失败隔离 | 通过 |
| 风险/优先级引擎 | **本阶段不做（留给 L5b/c）** |

**达到 L5a 工程验收。**  
下一步 L5b：KB-03 与 YAML 限值对齐、危险级结构化、适用性受限的告警提示（仍非完整风险矩阵）。

**停止，不进入 L5b/c/d/e。**
