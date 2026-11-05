# L5-P4｜综合诊断一致性修复与最终端到端验收

> 范围：正确性检查与 Demo 验收。**不**重训 60 头、**不**改 L1–L4、**不**加业务字段/新智能体/审批。  
> 冻结模型：`L5P-v1` / `L5P-f037a960e2e1`

---

## A. 修改文件

| 文件 | 变更 |
|---|---|
| `backend/l5/synthesis/features.py` | 重写观测提取：live 优先、冲突拒绝、回填带 provenance、答案列隔离 |
| `backend/l5/synthesis/prediction_overlay.py` | 接入新回填策略；输出 `observation_input` 追溯 |
| `backend/l5/synthesis/comparison.py` | 根因语义修正；内部相容性只标记；引擎 `v1_1` |
| `backend/l5/synthesis/synthesis_agent.py` | 提示：unconfirmed ≠ conflicting |
| `backend/l5/synthesis/tests/test_synthesis.py` | 扰动测试 + 根因语义测试 |
| `docs/artifacts/L5_P4_E2E_SAMPLES.json` | 六设备各 ≥5 条推理样例 |
| `docs/L5_P4_DELIVERY.md` | 本交付 |

---

## B. 观测回填及答案泄漏检查

### 规则落实

| 要求 | 结果 |
|---|---|
| 19 观测为唯一物理输入 | 是；`record_id` 仅追溯，指纹不含 ID |
| ID/文件名/行号不作特征 | 是 |
| 回填绑定数据源/设备/时间/版本 | 是（`channel`/`source_file`/`collection_time`/`dataset_fingerprint`/`equipment_*`） |
| 共同特征不一致拒绝静默覆盖 | 是 → `feature_conflict` / `live_vs_dataset_feature_conflict` |
| 新数据缺特征不得偷用训练行 | 是：无索引命中或设备不匹配 → 明确失败 |
| 索引不暴露十个答案字段 | 是；加载时 drop `TARGET_COLUMNS`，查找再过滤 |

### 扰动测试（已通过）

1. **同观测、不同 record_id** → 预测与 `model_input_fingerprint` 一致。  
2. **同 record_id、完整扰动观测** → 使用本次 live，指纹变化。  
3. **部分 live 与历史共同特征冲突** → 拒绝合并。  
4. **缺特征且无合法来源** → 失败。

---

## C. Synthesis 冲突语义修复

五态定义写入对照引擎 `semantics`：

- `consistent` / `partially_consistent` / `conflicting` / `insufficient_evidence` / `unavailable`

### 根因关键修复

**L5 根因模板 + L4 `unconfirmed` 且无明确反证 → `insufficient_evidence`，不再因文本不重叠自动 `conflicting`。**

仅当存在 `contradicting_evidence`、明确拒绝状态，或 L4 已 `confirmed` 且不相容时标 `conflicting`。

### `REC-MA-20260314-00008`

| 项 | 结论 |
|---|---|
| L4 confirmation | `unconfirmed`；候选无 `contradicting_evidence` |
| 根因对照（修复后） | **`insufficient_evidence`**（`l5_template_with_l4_unconfirmed_no_refutation`） |
| 内部相容性 | **标记** `location_root_component_mismatch`（位置=电机转子 vs 根因=非驱动端轴承）；**不改写**十字段 |

---

## D. 六设备十字段推理结果

样例见 `docs/artifacts/L5_P4_E2E_SAMPLES.json`（测试集观测推理；**非独立盲测**）。

| 设备 | 样例数 | 覆盖状态 |
|---|---|---|
| 主轧机电机 | 5 | 正常/预警/报警/危险 |
| 主减速机/齿轮箱 | 5 | 正常/预警/报警/危险 |
| 冷却水循环泵 | 5 | 正常/预警/报警（本批前 100 条未抽到危险） |
| 加热炉助燃风机 | 5 | 正常/预警/报警/危险 |
| 高压除鳞泵 | 5 | 正常/预警/报警/危险 |
| 连铸机扇形段驱动电机 | 5 | 正常/预警/报警/危险 |

链路：观测 19 维 → L5P-v1 → 十个目标字段 →（有 Run 时）知识对照。

---

## E. LLM + KG 真实协作案例

仍复用已存 L3/L4 Reasoning（RAG/KG 在上游完成），Synthesis 按需：

| 记录 | L3/L4 | 确定性根因语义 | 说明 |
|---|---|---|---|
| `REC-MA-20260314-00008` | available | insufficient_evidence | 模板未升格为 confirmed；内部位置/根因矛盾仅标记 |
| `REC-DP-20260314-00162` | available | 对照可运行 | 故障类型可 consistent |
| 无 Reasoning 的设备记录 | unavailable | 不伪造 Agent | 仅 L5 预测 + 可用证据 |

默认报告查询 **不**自动调用 LLM；失败时确定性对照与十字段仍完整。

---

## F. 报告中心及导出验证

| 项 | 结果 |
|---|---|
| 十字段主输出 | `source_layer=l5_prediction` / `L5P-v1` |
| L2/L3/L4 | 独立证据与推理；upstream 在 extras |
| AI 复核覆盖预测 | 否（`l5_predictions_unchanged`） |
| 根因模板≠物理确认 | `root_cause_confirmation=unconfirmed` |
| 历史 L5a | 既有 Run 行不被后台静默改写；仅新执行/`force_new` 才写入 overlay |
| 追溯 | `model_id` + `model_input_fingerprint` + `observation_input` + source Run IDs |
| 导出 | `data/l5_exports/L5P-v1_test_predictions/` **24000** 行，六类×4000，十字段齐全 |
| 前端 | `npm run build` **成功** |

---

## G. 测试结果与剩余问题

```
unittest l5.synthesis.tests + l5.prediction.tests → 17 OK
frontend build → OK
export 24000 → OK
synthesis 幂等复用 → OK
```

### 剩余问题（不阻塞路演）

1. 故障位置 Macro-F1≈0.53、根因≈0.70 — 已知弱项，**本轮不重训**。  
2. 部分设备 L3/L4 Run 覆盖不全 — 正确降级为 `unavailable`。  
3. 冷却水泵本批抽检未覆盖「危险」状态 — 模型与导出仍支持四级；抽检样本局限。  
4. 测试文件观测与训练同源通道 — **不得称为独立盲测**（已在样例 JSON 注明）。

---

## H. 是否达到最终路演验收

**是。**

在数据集十字段范围内：监督学习高覆盖预测、L3/L4 知识增强、轻量综合对照、报告三区、六类导出与测试均已可运行、可解释、可评测。观测回填与冲突语义已按 P4 收口。

**停止：不再增加新模型、新智能体、新业务规则或企业审批功能。**
