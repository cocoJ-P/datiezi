# L4 Core v1.2｜结构化证据匹配、设备物理适用性与可追溯性修复

## A. 修改文件

| 模块 | 文件 | 核心变更 |
|---|---|---|
| 元数据 | `evidence_metadata.py`（新） | 只读解析 analyzer_id / feature_id / feature_group |
| 匹配 | `path_validator.py` | 删除 blob 子串 `analyzer_ok`；结构化角色分类 |
| 适用性 | `applicability.py` | Unknown≠Absent；本体/关联部件分层 |
| 资格 | `fault_eligibility.py` | excluded_faults 保留真实 contradicting_evidence_ids |
| 知识 | `catalog_v1_2.json` | feature_ids + same_layer_as_resulting_fault |
| 加载/版本 | `loader.py`, `__init__.py` | `l4_core_v1_2` / `l4_evidence_match_v1_2` |
| 候选/引擎/指纹 | `candidate_builder.py`, `engine.py`, `input_adapter.py` | 关联状态、applicability_version |
| 测试 | `tests/test_l4_core_v1_2.py` | 七类扰动测试 |
| 前端 | `L4Workbench.tsx` | 版本标识 v1.2 |

未改 L1/L2/L3、模型、graph、答案列；无 LLM。

## B. Evidence Matcher

**已删除：**

- `any(token in blob)` / `any(analyzer in blob)` 决定 cause_specific
- 将 `mech:` 前缀误当作 analyzer
- 用 feature/故障中文子串宽泛关联根因

**新逻辑：**

1. `adapt_evidence_item`：校验 `analyzer_id ∈ {mechanical_feature,statistical,threshold}`  
2. 精确解析 `mech|stat|thr` Evidence ID；`feature_group` 枚举校验  
3. `feature_name → canonical_feature_id` 映射表  
4. 元数据 unresolved → 最多 contextual，禁止 cause_specific  
5. 路径规则使用 `cause_specific_feature_ids` / `fault_manifestation_feature_ids` / `allowed_feature_groups` / `allowed_analyzer_ids`（集合精确匹配）  
6. `same_layer_as_resulting_fault=true`（接触疲劳、齿轮啮合）→ 特征只进 manif

## C. 证据角色（真实案例）

**REC-MA-20260314-00011**

- `CK-bearing-contact-fatigue`：BPFO/包络 → **fault_manifestation**（0 cause_specific）  
- `CK-lube-film-loss-bearing`：驱动/非驱动端温度 → **cause_specific**；振动 → contextual  

**REC-DP-20260314-00021**

- `CK-seal-leak-bearing-lube-fail`：BPFO → **manif**；supporting=[]  
- `CK-rotor-mass-imbalance`：1X → **manif**  

**REC-MA-20260316-00035**

- `CK-gear-mesh-abnormality`：啮合幅值 → **manif**；applicability=`associated_unconfirmed`  
- 油含水：reference_only（案例设备），无 oil_water_content → 无 cause_specific  

## D. 设备适用性

| 对象 | 判定 | 来源 |
|---|---|---|
| 主轧机电机 + 轴承/润滑 | applicable @ equipment_type | 类型白名单 `body_components`（明示非 BOM） |
| 主轧机电机 + 齿轮 | **associated_unconfirmed** | `associated_components`；保留候选，不宣称本体有齿轮 |
| 高压除鳞泵 + 机封 | applicable @ equipment_type | 类型白名单；明示非 BOM |
| 高压除鳞泵 + 齿轮 | **not_applicable** | 显式缺席 + nap |
| 未知设备类型 | **unresolved** | 不在 `KNOWN_EQUIPMENT_TYPES`；不得 not_applicable |

## E. 被排除故障

**REC-MA-20260314-00011 / 转子不平衡 CONTRADICTED**

- `exclusion_reason`：L3 CONTRADICTED  
- `contradicting_evidence_ids`：`mech:REC-MA-20260314-00011:unbalance_1x:1X转频幅值(mm/s)`  
- `evidence_citation_missing=false`  
- `l3_core_verdict=CONTRADICTED`  

**REC-DP-20260314-00162**

- 轴承磨损 conflict：含 BPFI 反对证据 ID；`needs_review=true`  
- 齿轮 NOT_APPLICABLE：无可用反对证据 ID → `evidence_citation_missing=true`（不编造）

## F. 扰动测试

| # | 输入变化 | 预期 | 实际 | 结果 |
|---|---|---|---|---|
| 1 | Evidence ID 插入 lubrication/bearing 噪声致解析失败 | 不得 cause_specific | 未进 supporting | PASS |
| 2 | 仅改温度/BPFO 结构化特征 | 仅依赖路径变化 | 润滑 supporting 随温度消失；疲劳无 cause_specific | PASS |
| 3 | equipment_type=unknown | unresolved | unresolved；无虚假 applicable | PASS |
| 4 | 电机+齿轮观测 | associated_unconfirmed；manif | 符合 | PASS |
| 5 | 删除温度特异证据 | cause_specific 清空；候选 unconfirmed | 符合 | PASS |
| 6 | CONTRADICTED/冲突 | 排除+证据 ID/缺失标记 | 符合 | PASS |
| 7 | 改 notes/status | 语义不变 | 符合 | PASS |

## G. 真实数据验证

定向 19：候选 67；cause_specific 28（较 v1.1 下降，因同层机理不再计入）；bpfo_seal/mesh_water/wrong_appl/bad_expand=0；全 unconfirmed；propagation unknown。

留出检查（00011/00043/00021/00041/00035）：err_sup=0；齿轮关联未过度删除；L3 未改写。

未知类型：0 applicable；reference 为 unresolved。

## H. 版本与兼容性

- `l4_core_v1_2` / `l4_causal_knowledge_v1_2` / `l4_evidence_match_v1_2` / `l4_applicability_v1_2`  
- fingerprint 含上述版本  
- 旧 `L4R-6fe2de06f689`（v1）可读  
- 同输入幂等复用通过  

## I. 测试结果

- `pytest l4/tests`：**37 passed**  
- `npm run build`：**成功**  
- 定向 19 / 留出真实记录 / 幂等：通过  

## J. 最终判断

1. **mech: 前缀误匹配**：已消除（精确解析 + 禁止 blob 子串）。  
2. **未知设备 unresolved**：是。  
3. **白名单不再冒充 BOM**：是（`structure_source=type_reference_whitelist`；关联部件单独状态）。  
4. **合理候选保留**：是（含 associated 齿轮）。  
5. **宽泛 Token 错误 cause_specific**：结构化路径下已知错配为 0；未映射特征不得 cause_specific。  
6. **适合冻结 L4 Core v1.2**：**是**（工程语义）。  
7. **剩余限制**：无资产 BOM/含水率等特异观测；温度→润滑仍为规则启发；无专家一致率；类型白名单仍人工维护。  

**停止于 L4 Core v1.2。不进入 Reasoning / L5 / L3 修改。**
