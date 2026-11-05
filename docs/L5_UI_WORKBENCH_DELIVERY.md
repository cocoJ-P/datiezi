# 智能诊断工作台｜L4/L5 可视化接入交付

> 范围：前端审计、总览流程、L4/L5 入口与报告接线。不重训、不改推理引擎、不扩业务字段。

---

## A. 前端 L4/L5 真实实现情况审计

| 能力 | 结论 |
|---|---|
| 诊断与控制总览（原 L1–L3 流） | **已实现并可访问**；本轮扩展 L4/L5 节点 |
| 顶部 L1–L5 导航 | **已实现并可访问**；原 L5 文案为「反馈」 |
| L4 根因工作台 `L4Workbench` | **已实现并可访问**（真实 `/l4` API）；此前总览无入口且 Layer 依赖演示 Run |
| L5 报告中心 `/report` | **已实现并可访问**（十字段 + Synthesis + 追溯） |
| `ReportCenterPage` / `report/api.ts` | **已实现**；本轮增加 Synthesis 列表与深链 |
| L5 工作台页签 | **原为 UI 占位（演示反馈）** → 本轮改为真实 `L5Workbench` |
| 总览 → L4/L5 跳转 | **此前入口未接通** → 本轮接通 |

---

## B. 复用与新增组件

### 复用

- `DiagnosticFlowCanvas` / `flow.css`（扩展下游列）
- `L4Workbench` + `l4/api.ts`
- `ReportCenterPage` + `report/api.ts`
- L3 `saveL3HandoffFocus` 传递真实 `diagnosis_run_id` / `record_ids`

### 新增

- `frontend/src/diagnosis/l5/L5Workbench.tsx` + `l5.css`
- `frontend/src/diagnosis/overview/l5HandoffFocus.ts`
- `GET /l5/synthesis/runs`（仅列表，供总览真实计数）

### 修改

- `Overview.tsx`、`DiagnosticFlowCanvas.tsx`、`flow.css`
- `LayerPanels.tsx`、`diagnosisNav.ts`
- `L4Workbench.tsx` / `l4.css`（证据展开、确认语义、跳转 L5）
- `ReportCenterPage.tsx`、`report/api.ts`
- `backend/l5/synthesis/store.py` / `service.py` / `api/router.py`

---

## C. 总览 L1–L5 流程改动

在 L2 完成后展示下游：

- **L4 根因推理**：已分析记录、候选数、Reasoning 完成数、证据不足/冲突；来自真实 `getL4ByAnalysis` + results。
- **L5 综合诊断**：模型 ID、报告数、十字段记录、Synthesis 次数；来自 `/l5/prediction/status`、`/l5/runs`、`/l5/synthesis/runs`。

原则：

- L5 **不依赖** L3/L4 完成。
- 无 L3/L4 → 显示「知识增强分析未运行」，不伪造根因，不标预测失败。
- 跳转携带真实 Run / record focus，禁止按设备名猜测。

---

## D. L4 根因与证据可视化

- 候选：名称、关联故障、部件、证据支持态（supported / contradicted / insufficient_evidence / unconfirmed）、`root_cause_confirmation`、知识 ID。
- 因果链：基于 API `causal_paths`；未验证显示「候选关联」。
- 证据追溯：可展开真实 evidence ID；无虚构 ID。
- Reasoning：展示已有结构化结论与工具轨迹；无 Run 显示未运行。
- **不**把 L5 模板升格为 confirmed。

---

## E. L5 十字段综合报告

- 工作台页签「L5 综合诊断」：真实 L5Run 列表 + 十字段字段预览。
- 报告中心保留完整三区（预测 / 知识增强 / 评测追溯）与按需 Synthesis。
- 深链：`l5HandoffFocus` + `?runId=`。

---

## F. LLM / KG / 多智能体复核展示

- 仍在报告中心按需触发；页面加载不自动打 LLM。
- L4 Reasoning / L3 知识引用仅展示已存结果。
- Synthesis 失败不影响十字段主输出。

---

## G. 三条真实记录联调锚点

| record_id | 可用 L5 锚点（示例） |
|---|---|
| `REC-MA-20260314-00008` | `L5R-f59276524697`（主轧机，含 L3/L4 绑定） |
| `REC-DP-20260314-00021` | `L5R-8f4e1357ae2c` / `L5R-92f9ed699d86` |
| `REC-DP-20260314-00162` | 同上（含 Reasoning 的记录） |

路径：总览 → L4「查看根因」→「打开对应 L5 报告」；或总览 →「进入综合诊断」/「查看最终报告」。

---

## H. 测试与构建

- `npm run build`：**通过**
- Synthesis 列表 API：可用
- L1/L2/L3 原流程组件保留；L4/L5 不再依赖演示 DiagnosticRun

---

## I. 剩余问题

1. 总览 L4/L5 统计依赖当前数据集关联的 DiagnosisRun；全局历史 Run 可能混入“未过滤”列表（有匹配优先）。
2. 冷却水泵等设备若无本地 L3/L4 Run，知识增强区正确显示未运行。
3. 演示 Feedback 交接仍可通过旧演示任务使用，已与综合诊断页签分离。

**结论：用户可从智能诊断工作台总览进入真实 L4 因果证据与 L5 综合诊断/报告中心。本轮停止。**
