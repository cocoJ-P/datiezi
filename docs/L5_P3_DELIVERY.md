# L5-P3｜Hybrid Diagnosis Synthesis 交付

> 模型包保持冻结：`models/l5_prediction/L5P-v1/`（`model_id=L5P-f037a960e2e1`，60 头）  
> 综合模块：`backend/l5/synthesis/`  
> 数据集十字段十个字段为唯一业务主输出；不引入新业务标签 / 审批 / 风险矩阵。

---

## A. 新增与修改文件

### 新增

| 路径 | 作用 |
|---|---|
| `backend/l5/synthesis/` | 协调器、对照引擎、预测叠加、导出、错误分析、存储、测试 |
| `docs/L5_P3_ERROR_ANALYSIS.json` | 故障位置 / 根因分析错误归因 |
| `models/l5_prediction/L5P-v1/error_analysis_location_rootcause.json` | 同内容模型包副本 |
| `data/l5_exports/L5P-v1_test_predictions/` | 六类设备无答案测试预测导出 |
| `backend/data/l5_synthesis.sqlite3` | SynthesisRun 持久化 |

### 修改

| 路径 | 作用 |
|---|---|
| `backend/l5/jobs.py` | L5Run 执行时叠加 `prediction_overlay_v1` |
| `backend/l5/api/router.py` | `/prediction/status`、`/synthesis/*`、`/prediction/export-test` |
| `frontend/src/report/api.ts` | 综合 API 客户端 |
| `frontend/src/report/ReportCenterPage.tsx` | 报告三区 + 按需综合按钮 |

---

## B. L5Prediction 与 L5a 集成

1. `build_final_diagnosis` 仍做 L2–L4 确定性聚合。  
2. `try_predict_for_context` 用 19 维观测 + 设备类别调用 L5P-v1。  
3. `apply_supervised_predictions` 将十字段主 `value` 改为监督预测；原聚合写入 `extras.upstream_aggregation`。  
4. `root_cause_confirmation` **始终保持** `unconfirmed`。  
5. Evidence 缺列时，从竞赛观测表按 `记录编号` **仅回填特征列**（目标列永不进入索引）。

字段元数据至少含：`prediction_value`、`source_model_id`、`prediction_status`、`equipment_type`、`input_fingerprint`；诊断置信度标注为监督回归目标，非校准概率。

---

## C. L2/L3/L4/L5 数据流与版本绑定

```
PED / DiagnosisRun (L2)
   └─ AnalysisRun + EvidenceBundle (L3 core)
        └─ L3 ReasoningRun（按需已存）
   └─ L4Run + RootCauseAssessment
        └─ L4 ReasoningRun（按需已存）
            └─ L5Run（聚合 + L5P overlay）
                 └─ SynthesisRun（确定性对照 ± Synthesis Agent）
```

绑定键：`diagnosis_run_id` / `analysis_run_id` / `l4_run_id` / reasoning run ids / `model_bundle_id` / `prepared_dataset_id` / `l5_prediction_model_id=L5P-f037a960e2e1`。  
缺 L3/L4 → 角色 `unavailable`，**不**记为 L5 预测失败，不借用其他设备结果。

---

## D. 三角色真实协作（非改名）

| 角色 | 实现 | 交接 |
|---|---|---|
| Diagnosis Agent | 已有 L3 ReasoningAssessment | `fault_type_review`、证据/知识引用、频谱与位置解释 |
| Causal Agent | 已有 L4 CausalReasoningAssessment | `candidate_reviews`、`root_cause_confirmation`、支持证据 |
| Synthesis Agent | DeepSeek 结构化复核（**仅** `allow_llm_synthesis=true`） | 接收双方摘要 + L5 预测 + 确定性对照；输出一致性 JSON |

默认路径：**L5 预测 → 确定性对照 → 报告**（0 LLM）。  
增强路径：读取已存 Reasoning → Synthesis Agent。失败时预测与对照仍可用。

---

## E. LLM / RAG / KG 实际调用

- **不重训、不重写** L3/L4；优先消费已保存 ReasoningRun。  
- 报告普通查询 **不**自动打 LLM。  
- Synthesis Agent 校验输出；伪造 `fake*` / `FORGED*` 引用会被拒绝。  
- 引用集合与上游 evidence/knowledge ID 求交，禁止新增伪造 ID。

真实联调样例：`REC-MA-20260314-00008`（`L5R-f59276524697`）在开启 LLM 后得到 `overall_consistency=conflicting`，且 `l5_predictions_unchanged=true`、`l4_root_cause_confirmation_snapshot=unconfirmed`。

---

## F. 真实综合诊断案例（5）

| # | record_id | 设备 | L5 十字段摘要 | Diagnosis / Causal | 对照摘要 |
|---|---|---|---|---|---|
| 1 | `REC-DP-20260314-00162` | 高压除鳞泵 | 正常 / 正常 / P3 | available / available | 故障类型 consistent；状态因 L2 UNCERTAIN→insufficient |
| 2 | `REC-MA-20260314-00008` | 主轧机电机 | 报警 / 轴承磨损 / 电机转子 / P1 | available / available | 存在 conflicting（根因模板 vs L4 unconfirmed） |
| 3 | `REC-DP-20260314-00021` | 高压除鳞泵 | 预警 / 轴承磨损 / 非驱动端轴承 / P2 | unavailable / available | 降级可读；预测仍完整 |
| 4 | `REC-MA-20260314-00011` | 主轧机电机 | 预警 / 轴承磨损 / 电机转子 / P3 | unavailable / available | 同上 |
| 5 | `REC-MA-20260314-00008` + LLM | 同上 | 同上 | available + Synthesis available | LLM：conflicting；不覆盖预测 |

---

## G. 故障位置与根因错误分析

详见 `docs/L5_P3_ERROR_ANALYSIS.json`（internal_val 重算混淆，**不替换** L5P-v1）。

| 目标 | mean Accuracy | mean Macro-F1 | 最弱设备 |
|---|---|---|---|
| 故障位置 | 0.746 | 0.528 | 主减速机/齿轮箱 |
| 根因分析 | 0.851 | 0.699 | 主轧机电机 |

主要模式：同故障不同部件混淆；相似根因模板重叠；设备专有表述；“-”/正常多数类主导。  
后续类别权重 / 分层位置等实验必须在独立分支，固定划分对比，不得直接替换冻结包。

---

## H. 六类设备十字段覆盖率

无答案测试导出 `data/l5_exports/L5P-v1_test_predictions/`：

| 设备类别 | 行数 |
|---|---|
| 主轧机电机 | 4000 |
| 主减速机/齿轮箱 | 4000 |
| 冷却水循环泵 | 4000 |
| 加热炉助燃风机 | 4000 |
| 高压除鳞泵 | 4000 |
| 连铸机扇形段驱动电机 | 4000 |
| **合计** | **24000** |

列：记录标识 + 十个目标字段 + `model_id` + `input_fingerprint`。覆盖率 **6/6**。

---

## I. 时间留出指标与无答案导出

主指标仍为 L5P-v1 `internal_val`（时间留出）：

| 字段 | mean Acc | mean Macro-F1 |
|---|---|---|
| 设备状态 | 0.992 | 0.950 |
| 故障类型 | 0.995 | 0.993 |
| 故障严重度 | 0.930 | 0.805 |
| 故障位置 | 0.746 | 0.528 |
| 频谱描述 | 0.997 | 0.997 |
| 根因分析 | 0.851 | 0.699 |
| 建议措施 | 0.970 | 0.909 |
| 优先级 | 0.899 | 0.762 |
| 影响程度 | 0.971 | 0.928 |

知识一致性计数与准确率**分列报告**；“Agent 认为一致”≠“预测正确”。  
协调器**不用**真实答案挑选预测源。`fit_repro` / `blind_test` 通道定义不变。

---

## J. 测试与验收

`unittest l5.synthesis.tests.test_synthesis l5.prediction.tests.test_prediction` → **12 OK**。

覆盖要点：60 头可加载；L2 CONFLICT≠四级状态；冲突不覆盖；缺 L3/L4 降级；overlay 保持 unconfirmed；伪造引用拒绝；特征契约；无答案推理；时间划分无 ID 重叠。

### 特别问题

| # | 问题 | 结论 |
|---|---|---|
| 1 | 是否真正复用 L3/L4 知识推理？ | **是**（读已存 ReasoningRun + Evidence/知识引用） |
| 2 | 是否真实跨角色交接？ | **是**（`handoff` + 结构化摘要，非改名） |
| 3 | 是否隔离数据集预测与物理因果确认？ | **是**（confirmation 快照 + 双通道命名） |
| 4 | LLM 失败是否保留基础预测？ | **是** |
| 5 | 是否完成十字段报告？ | **是**（报告中心三区） |
| 6 | 是否导出六类测试预测？ | **是**（24000 行） |
| 7 | 是否存在标签泄漏？ | **否**（观测索引丢弃目标；导出忽略答案列；综合不用 GT） |
| 8 | 是否具备路演 E2E？ | **是**（L5 十字段 + 可选综合 + 导出 + 评测追溯） |

**验收判断：L5-P3 达成。L5P-v1 保持冻结；停止自动创建新模型或扩展业务体系。**
