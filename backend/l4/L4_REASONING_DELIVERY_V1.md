# L4 Reasoning v1｜交付报告

## A. 新增与修改文件

**新增**

- `backend/l4/reasoning/`：schema、input_adapter、tools、path_inspector、validator、orchestrator、outcome、policy、database、store、jobs、api
- `backend/l4/reasoning/tests/test_l4_reasoning.py`
- 前端：`api.ts` / `L4Workbench.tsx` 增加 AI 根因推理

**修改**

- `backend/app/main.py`：注册 `/l4/reasoning` 路由

**未修改**：L3、L4 Core 引擎逻辑、L5。

## B. 架构与复用

- 独立 `L4ReasoningRun`（sqlite：`l4_reasoning.sqlite3`），不写入/覆盖 L4 Core Assessment。
- 复用 DeepSeek OpenAI 兼容客户端与 Tool Calling 循环（同 L3 Reasoning 模式）。
- 消费冻结 L4 Core v1.2：Eligibility / Applicability / EvidenceMetadata / CausalPathValidator 结果只读。
- 服务端确定性工具：`inspect_causal_path`。
- 输出经程序化校验；失败 → `validation_failed` 或确定性回退，不得标成功篡改。

## C. 实际 LLM Tool Calling 轨迹（真实验收）

运行示例：`L4RR-0c464e35e1f9` / `L4RR-eec7dc8f85d7`

典型工具序列：

1. `get_l4_candidates`
2. `inspect_causal_path`（多次按路径）
3. `get_l3_evidence`
4. `get_causal_knowledge`
5. `get_equipment_dependencies`

单记录约 4 轮 LLM、≤10 次工具；例：00399 ≈ 33k tokens。

## D. 真实案例（摘要）

| 记录 | 结果 | 要点 |
|---|---|---|
| REC-MA-20260406-00399 | live_llm_success | 接触疲劳 → mechanism_only；润滑候选 → insufficient / indistinguishable；全程 unconfirmed |
| REC-MA-20260314-00008 | llm_invalid_json_fallback | 工具已调用；确定性回退仍区分同层机理；unconfirmed |
| REC-DP-20260314-00021 | live_llm_success | 接触疲劳 mechanism_only；机封 insufficient（非 BPFO 确认）；mapping unresolved；propagation unknown |
| REC-DP-20260314-00162 | llm_invalid_json_fallback | 工具已调用；轴承排除仅 review_recommended；机封 contradicted；unconfirmed |

## E. 因果路径验证

`inspect_causal_path` 逐环节输出 knowledge_backed / observed / indirectly_supported / missing 等；同层条目 cause 不标 observed。

## F. 竞争性假设与反对证据

- 多润滑/疲劳候选并存，可标 indistinguishable。
- cause_specific（温度）不升级为已确认根因。
- 排除故障仅 `review_recommended`，不自动恢复。

## G. 潜在影响

复述 Core ImpactAssessment；`impact_degree=null`；`propagation_status=unknown`；禁止已传播表述。

## H. L4ReasoningRun 与审计

保存：reasoning_run_id、source_l4_run_id、prompt/tool/policy 版本、token、tool_history、validation、outcome。幂等 content_key 含 prompt+知识+规则版本。

## I. 前端

Workbench「AI 根因推理」：候选比较、validity、替代解释、未验证条件、排除复核建议、工具列表、tokens；固定展示根因未确认。

## J. 测试与限制

- pytest reasoning：**8 passed**；L4 Core 回归保持通过；前端 build 成功。
- 限制：复杂记录偶发 LLM JSON 截断/无效 → 回退；无专家金标准故不报准确率；缺油液/相位等仍难区分竞争假设。

### 最终问答

1. LLM 是否调用 L4 因果工具？**是**  
2. 区分表现与上游？**是**（mechanism_only 等）  
3. 多竞争根因？**是**  
4. 保留缺失证据？**是**  
5. 避免知识当观测？**是**（工具说明+校验）  
6. 避免已确认传播？**是**  
7. L3/L4 Core 不变？**是**  
8. 工程验收？**满足 L4 Reasoning v1**（功能成功 ≠ 诊断准确率）

**停止，不进入 L5。**
