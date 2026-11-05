# L4 Core v1.1｜因果语义可靠性修复与回归验收

## A. 修改文件

| 模块 | 文件 | 变更 |
|---|---|---|
| 版本 | `l4/__init__.py` | `l4_core_v1_1` / `l4_causal_knowledge_v1_1` / `l4_evidence_match_v1_1` |
| P0-1 | `l4/fault_eligibility.py` | Fault Eligibility：SUPPORTED/INCONCLUSIVE 可展开；CONTRADICTED/NOT_APPLICABLE/冲突排除 |
| P0-3 | `l4/applicability.py` | `*` ≠ applicable；部件/案例设备/显式不适用 |
| 知识 | `l4/causal_knowledge/catalog_v1_1.json` | 8 条条目收紧 scope + `evidence_matching` 结构化规则；`catalog_v1.json` 保留可读 |
| 加载 | `l4/causal_knowledge/loader.py` | 默认加载 v1.1 |
| 候选 | `l4/candidate_builder.py` | 先资格过滤，再适用性；区分 active / reference / excluded |
| 证据 | `l4/path_validator.py` | cause_specific / fault_manifestation / contextual 三分类 |
| 影响 | `l4/impact_analyzer.py` | upstream / downstream 分列；标题「潜在关联范围」 |
| 引擎 | `l4/engine.py` | 汇总 excluded/reference/rules_version |
| 指纹 | `l4/input_adapter.py` | fingerprint 含 core/知识/规则版本 |
| 服务 | `l4/service.py` | 状态说明升至 v1.1 |
| 前端 | `frontend/src/diagnosis/l4/L4Workbench.tsx` | 证据分类展示；潜在关联范围；排除/参考列表 |
| 测试 | `l4/tests/test_l4_core_v1_1.py` 等 | 单元 + 回归脚本 |

未修改：L1/L2/L3、模型、graph.json、KnowledgeSnapshot、Excel 答案列。无 LLM。

## B. 故障候选过滤

| 记录 | 修复前（约） | 修复后 |
|---|---|---|
| REC-DP-20260314-00162 | 轴承磨损 CONTRADICT / 齿轮 NOT_APPLICABLE 仍展开 → ~10 候选 | 排除 2（轴承 conflict/否定、齿轮 NA）；有效候选 4（仅 INCONCLUSIVE 的转子/润滑） |
| REC-DP-20260314-03404 | CONFLICT 下否定项仍展开 → 5 候选 | 轴承磨损 excluded_contradicted；保留转子不平衡 INCONCLUSIVE → 1 候选 |
| 定向 19 合计 | 120 候选 | **67** 有效 + 10 reference；**bad_expand=0** |
| 批量 200 | ~85/200 对否定故障展开 | **bad_expand=0** |

## C. 证据语义修正

- `supporting_evidence_ids` = 仅 cause_specific
- `fault_manifestation_evidence_ids` / `contextual_evidence_ids` 内部分类
- REC-MA-20260316-00035：啮合幅值 → gear_mesh 的 cause_specific；油含水为 reference_only，啮合仅 manif，**不进 supporting**
- 1X → 不平衡故障表现层，cause_specific 为空
- BPFO→机封 supporting：**0**（定向+批量）

## D. 设备适用性

| 知识 | main_mill_motor | high_pressure_descaling_pump |
|---|---|---|
| 齿轮啮合 | applicable | **not_applicable** |
| 油含水（齿轮箱案例） | **reference_only** | **not_applicable** |
| 机封泄漏 | **not_applicable** | applicable |
| 润滑油膜/润滑故障 | applicable | applicable |

`equipment_scope=*` 已从目录移除或不再自动 applicable。wrong_appl：**0**。

## E. 影响范围（REC-MA-20260314-00008）

标题：**潜在关联范围**；`propagation_status=unknown`；`reference_topology_only=true`

| 方向 | 节点 | 类别 |
|---|---|---|
| in | 主电源柜 / 高压供电系统 / 稀油站 | upstream_dependencies |
| out | 棒线主减速机/齿轮箱 / 棒线轧辊系统 / 棒线联轴器 | downstream_connections |

6 节点方向正确；不表述为「已影响设备」。

## F. 版本和持久化

- 新版本：`l4_core_v1_1` + `l4_causal_knowledge_v1_1` + `l4_evidence_match_v1_1`
- content_key / input_fingerprint 含版本 → 不与 v1 结果混淆
- 旧 L4Run `L4R-6fe2de06f689`：**可读**
- 相同输入幂等复用：**通过**
- 不覆盖历史 RootCauseAssessment

## G. 19 条定向复测

- 候选 120 → **67**；reference **10**；排除故障累计 **10**
- bpfo_seal / mesh_water / wrong_appl / bad_expand 均为 **0**
- 全部 unconfirmed；propagation unknown
- 剩余：INCONCLUSIVE 润滑在泵上仍可保守展开机封候选（无 cause_specific）；属预期保守行为，非错误 supporting

## H. 200 条批量复测

- 成功 **200/200**；耗时约 **6.8s**
- bad_expand / bpfo_seal / mesh_water / wrong_appl：**0**
- 全部 unconfirmed；propagation unknown
- 候选总量 706（较 v1 下降，因过滤与适用性收紧）
- 新 run：`L4R-6819e31506c8`

## I. 测试

- `pytest l4/tests`：**24 passed**
- `npm run build`：**成功**
- 无 DeepSeek / LLM 调用
- 兼容：旧 L4Run 可读；L3 记录未改写

## J. 冻结条件

| 条件 | 结论 |
|---|---|
| C.1–C.3 是否已修复 | **是** |
| 是否引入新的错误过滤 | **否**（否定项排除；INCONCLUSIVE/SUPPORTED 保留） |
| 是否保留合理根因候选 | **是** |
| 是否仍有误导 supporting | **否**（已知错配归零） |
| 影响范围表述错误 | **已消除** |
| 建议正式冻结 L4 Core v1.1 | **是** |

**停止于此。不进入 L4 Reasoning / LLM / L5。**
