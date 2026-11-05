# L4 Reasoning v1｜可靠性收尾与最终冻结验收

## A. 修改文件与具体修复

**修改**

- `backend/l4/reasoning/orchestrator.py`
  - 工具阶段与最终 JSON 阶段分离；工具额度用尽或非 JSON 内容时强制 `response_format=json_object` 最终输出。
  - 一次受控 JSON 修复（`json_repair_limit=1`）。
  - 失败分类：`empty_final_content` / `malformed_json` / `non_json_prose` / `output_truncated`（不再一律当截断）。
  - 最终输出规范化：将 `{evidence_id, note}` 收敛为纯 ID（**不放宽**引用合法性）。
- `backend/l4/reasoning/tools.py`
  - 同 Run 内只读工具结果缓存（相同参数复用）；`unique_tool_exec_count` 计预算，`cache_hits` 入审计。
  - 工具返回精简为 ID/摘要，降低 prompt 体积与截断。
- `backend/l4/reasoning/policy.py`：`max_final_output_tokens=6144`、`json_repair_limit=1`、工具预算 12、超时 120s。
- `backend/l4/reasoning/__init__.py`：`prompt/tools/policy` → `v1_1`（新幂等 key）。
- `frontend/.../L4Workbench.tsx` + `l4.css`：明确区分 success / insufficient / invalid_json_fallback / validation_failed / llm_failed。
- 测试：缓存、失败分类、ID 规范化。

**未修改**：L3、L4 Core v1.2、因果知识目录、原始 PED。

## B. 原 JSON 失败的真实原因

对历史失败记录（00008、00162 等）审计：

| 项 | 观测 |
|---|---|
| `finish_reason` | **stop**（非 length） |
| `completion_tokens` | ≈468–561（成功案例通常 2k–4k） |
| 错误类型 | `invalid_json` / `empty_final_content` 类 |

**结论**：主要不是 `max_output_tokens` 截断，而是：

1. 工具调用打满后模型仍返回 `tool_calls`，`content` 为空却被当作最终输出；
2. 重复工具调用浪费预算，最终阶段未强制结构化 JSON；
3. 另见合法 JSON 但证据字段写成对象 → **Schema/引用校验失败**（00392 复现），与截断无关。

## C. 修复前后成功与回退

| 记录 | 修复前 | 修复后 |
|---|---|---|
| REC-MA-20260406-00399 | live_llm_success | live_llm_success |
| REC-MA-20260314-00008 | llm_invalid_json_fallback | **live_llm_success** |
| REC-DP-20260314-00021 | live_llm_success | live_llm_success |
| REC-DP-20260314-00162 | llm_invalid_json_fallback | **live_llm_success** |
| REC-MA-20260406-00392（额外） | （曾 validation_failed：证据对象） | **live_llm_success**（ID 规范化后） |
| REC-MA-20260314-00011（额外） | — | live_llm_success |

全部保持 `root_cause_confirmation=unconfirmed`，`impact_degree=null`，L4 Core `core_hash` 不变。

## D. Tool Calling 与 Token 对比（摘要）

- 修复前典型：prompt ~25–33k，重复 `get_l4_candidates` / 同路径 inspect；失败时 completion 极短。
- 修复后：缓存命中 ≥1；`unique_tool_exec_count`≈11–12；多数走 `final_response_format=json_object`。
- 示例 00008：~18.5k total / 13.3s / live success（原 ~22–25k + invalid_json）。
- 示例 00162：~23.3k total / live success（原 ~15.9k 但无效短输出）。

目标是减少无效调用并稳定最终 JSON，**不是硬性 Token 减半**。

## E. 真实根因推理案例与证据引用

- **轴承/润滑（00399、00008、00392）**：接触疲劳 → `mechanism_only` 或竞争；润滑相关 → `insufficient_upstream_evidence` / `indistinguishable`；温度不升级为已确认润滑根因。
- **除鳞泵（00021、00162）**：机封/轴承候选保持未确认；工具含 `inspect_causal_path`、`get_equipment_dependencies`。
- **00011**：同层 `mechanism_only` + 多候选 `indistinguishable`。

## F. ReasoningAssessment 与因果路径校验

- 程序化校验仍强制：候选/证据/知识 ID、排除不可恢复、impact/propagation 边界、确认态。
- 规范化只抽取已声明的 `evidence_id`；伪造 ID 仍失败。

## G. 前端状态

Workbench 展示中文语义标签，回退/校验失败明确提示「不是完整 AI 成功」；与「证据不足但推理成功」区分。

## H. 验证

- pytest `l4/reasoning/tests`：**11 passed**
- 前端 `npm run build`：**成功**
- 6 条真实 PED 复测：见 `L4_REASONING_RELIABILITY_LIVE.json` + 00392 追加 run `L4RR-5897431cad02`

## I. 剩余限制

- 模型仍可能写出非法 ID 或越权语义 → `validation_failed`（正确拒绝）。
- 无专家金标准，不报告根因准确率。
- 缺油液/相位/拆检时竞争假设仍常不可区分。
- DeepSeek `json_object` 若偶发不可用会降级 plain + 修复轮。

## J. 是否建议冻结 L4 Reasoning v1

**建议冻结 L4 Reasoning v1（含可靠性补丁 v1.1 运行配置）**。

工程验收达标：真实 LLM 可稳定输出可校验的因果假设比较；不改变 L4 Core 确定性结论与证据边界。

**停止，不进入 L5。**
