# L4 Core v1 + Causal Knowledge v1｜交付报告

**完成时间**：2026-11-05  
**范围**：确定性根因候选 + curated 因果知识 + 潜在影响；**无 LLM / 不改 L3 / 不做 L5**

---

## A. 修改与新增

### 新增

- `backend/l4/` 全模块（input / engine / causal_knowledge / mapping / impact / store / jobs / api）
- `backend/l4/causal_knowledge/catalog_v1.json`（8 条候选机理）
- `backend/l4/equipment_mapping/mappings_v1.json`
- `backend/l4/tests/test_l4_core.py`
- `backend/l4/L4_CORE_V1.md`、`L4_DELIVERY_V1.md`
- `frontend/src/diagnosis/l4/*`（API + Workbench + CSS）

### 修改（非 L3 冻结面）

- `backend/app/main.py`：注册 `/l4` 路由
- `frontend/vite.config.ts`：代理 `/l4`
- `frontend/src/diagnosis/LayerPanels.tsx`：L4 页接入 Workbench
- `frontend/src/events/diagnosisNav.ts`：L4 文案调整
- `backend/.env.example`：可选 `L4_*` 注释

---

## B. L3→L4 输入契约

- `L4InputContext`（`l4_input_v1`）：钉扎 `analysis_run_id` + `record_id` + 可选 reasoning/enrichment ID
- `input_fingerprint`：对故障候选、支持证据、充分性等稳定哈希
- Reasoning 缺失：仍可执行（仅 Core EvidenceBundle）
- `insufficient_evidence`：允许进入，根因仍为 `unconfirmed`

---

## C. Causal Knowledge

| 项 | 值 |
|---|---|
| 版本 | `l4_causal_knowledge_v1` |
| 条目数 | **8** |
| 状态 | 全部 `candidate_mechanism`（非 confirmed_causality） |
| 覆盖 | 轴承疲劳、润滑→轴承、润滑故障、转子质量不均、对中/弯曲鉴别、齿轮啮合、油含水→点蚀、机封进水→轴承 |
| 来源 | KB-02 / KB-04（及快照 knowledge_id 引用）；独立于 `graph.json` |

---

## D–G. 真实样例（本机已跑）

| 记录 | L3 侧 | L4 outcome | 要点 |
|---|---|---|---|
| REC-MA-20260314-00008 | ABNORMAL + insufficient Reasoning | `insufficient_evidence` | 多故障候选→多根因候选；特异支持 0（仅有越限类）；映射 `equipment_type`；下游潜在 6；propagation unknown |
| REC-MA-20260314-00003 | 无 Reasoning | `insufficient_evidence` | 无 Reasoning 仍可跑 |
| REC-DP-20260314-03404 | CONFLICT | `candidates_generated` | 1X 等特异证据可计入路径支持；映射 unresolved→无具体下游 |
| REC-DP-20260314-00162 | UNCERTAIN | `candidates_generated` | 有 Reasoning；泵类型 unresolved |
| REC-DP-20260314-00003 | ABNORMAL 泵 | `candidates_generated` | 多条 BPFO/1X 支持计数>0，仍 unconfirmed |

Causal Trace：含 ROOT_CAUSE_CANDIDATE / CAUSAL_MECHANISM / FAULT_REFERENCE / EVIDENCE_REFERENCE / IMPACT_PATH；`acyclic=true`；知识节点 `fact_claim=false`。

---

## H. Run / API / 前端

- SQLite：`backend/data/l4_analysis.sqlite3`（WAL，独立表）
- 幂等：`content_key`；默认 `max_records=200`
- API：`GET/POST /l4/*`
- 前端：诊断体系 **L4 根因** 页 → `L4Workbench`

---

## I. 测试

```text
pytest l4/tests/test_l4_core.py  → 10 passed
```

覆盖：只读 Bundle、无 Reasoning、insufficient、答案列隔离、L3 不变、知识来源、空候选、异常≠确认根因、映射区分、拓扑≠已传播、无影响等级、Trace 无环、Run 幂等。

---

## J. 不足与进入 Reasoning v1 的条件

**不足**

- 无 exact_asset 映射；主轧机仅类型级拓扑参考
- 除鳞泵图谱节点缺失 → 无法列具体下游
- 缺油液/相位/2X/维修史 → 多数路径缺失条件完整
- 未自动在 L3 完成后入队（避免改冻结 L3 / 误跑全量）；需显式 `POST /l4/runs`

**进入 L4 Reasoning v1 前应具备**

1. 至少若干 `exact_asset` 人工确认映射  
2. Core 批量与成本配额策略  
3. 明确 LLM 仅解释/补全缺失，不得确认根因  
4. 更严的 anomaly vs fault-specific 证据角色沿用 L3 语义
