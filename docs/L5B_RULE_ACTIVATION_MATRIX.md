# L5B｜规则启用条件表

> 性质：核验矩阵，**不是**批准文件；不得据此自动 `approved`。  
> 统计来源：`alert_rules_v1_1.json`（19 条）+ `approvals_v1.json`（0 条）+ `conflict_detector` 实跑结果。  
> 目录版本：`alert_rule_catalog_v1_1`。

---

## 1. 统计口径（必须区分）

| 指标 | 口径 | 数量 |
|---|---|---|
| 规则总数 | 目录实数 | **19** |
| 测量语义“门控完整” | `measurement_kind`+`unit`+`statistic` 均非 unresolved（足以过 L5b 比较门，**不等于**采集规范完备） | **10** |
| 采集定义完备 | 传感器+窗口+方法+测点图均有 A 级证据 | **0** |
| 设备类型有 L2 配置 | `main_mill_motor` / `high_pressure_descaling_pump` | **12** |
| 测点适用性完备 | 安装位置/部件有工程证明 | **0** |
| 阻断性冲突规则 | `conflicting_rule_ids` ∪ `review_status=conflict` | **4** |
| `ready_for_review` | 测量门控完整 + KB 可核 + 无冲突 + 有 L2 管道 + 非仅 legacy | **0** |
| 真实有效 approved 授权 | `review_status=approved` 且 approvals 审计命中 | **0** |
| `approved_active` | — | **0** |

`activation_readiness` 分布（实统计）：

| readiness | 条数 |
|---|---|
| blocked_by_measurement | 9 |
| blocked_by_conflict | 4 |
| blocked_by_applicability | 4 |
| blocked_by_source | 2 |
| ready_for_review | **0** |
| approved_active | **0** |

---

## 2. 必查冲突裁定（只陈述事实，不择优）

### 2.1 主轧机电机轴承温度

| 来源 | 限值 | 级别 |
|---|---|---|
| KB-03 | 75°C | 报警 |
| KB-03 | 85°C | 危险 |
| YAML `mill_bearing_temp_*` | 85°C | upper_alarm |

- 设备类型：同为 `main_mill_motor`（A）。  
- 测点：同为驱动端/非驱动端轴承温度列（A：列名一致；C：物理安装是否同一点 unresolved）。  
- 工况：双方均未给出可核运行条件证明（unresolved）。  
- **结论：在相同设备类型与同名测点下，级别语义冲突成立** → 规则  
  `KB03-MM-BEARING-TEMP-ALARM/DANGER`、`YAML-MM-BEARING-TEMP-DE/NDE-AS-ALARM`  
  全部 `blocked_by_conflict`。  
- **不得**自动选用 75 或 85。

### 2.2 高压除鳞泵 vs 冷却水循环泵

- `NOTE-CWP-VS-DESCALING-NOT-EQUIVALENT`：非等价设备（A）。  
- 冷却泵规则 **not_applicable** 于除鳞泵；不得替代专用规程。

### 2.3 齿轮箱 vs 主轧机电机

- `NOTE-GEARBOX-VS-MOTOR-NOT-EQUIVALENT`（A）。  
- 齿轮箱限值不得套用电机本体。

---

## 3. 全量规则矩阵

说明：

- `measurement_semantics`：门控层完整/不完整。  
- `equipment_applicability`：类型作用域；`*_no_l2_ped` = 无 L2/PED 管道。  
- `measurement_location`：当前均为 `from_feature_name_only`。  
- `operating_conditions`：均为 `stated_in_rule_unverified_against_runtime`。  
- `approval_status`：全部 `no_valid_approval`。

| rule_id | source_status | measurement_semantics | equipment_applicability | conflict_status | activation_readiness | missing_requirements（摘要） | evidence_source |
|---|---|---|---|---|---|---|---|
| KB03-MM-VIB-XY-ALARM | kb03_text_present | incomplete | type_scoped_with_l2_config | none | **blocked_by_measurement** | X/Y statistic unresolved | KB-03 电机振动表；L1 velocity；semantics.py |
| KB03-MM-VIB-XY-DANGER | kb03_text_present | incomplete | type_scoped_with_l2_config | none | **blocked_by_measurement** | 同上 | 同上 |
| KB03-MM-BEARING-TEMP-ALARM | kb03_text_present | complete* | type_scoped_with_l2_config | **blocking** | **blocked_by_conflict** | 与 YAML 85=alarm 冲突 | KB-03；conflict_detector |
| KB03-MM-BEARING-TEMP-DANGER | kb03_text_present | complete* | type_scoped_with_l2_config | **blocking** | **blocked_by_conflict** | 同上 | 同上 |
| KB03-GB-VIB-ALARM | kb03_text_present | incomplete | kb03_only_no_l2_ped | none | **blocked_by_measurement** | statistic + 无 L2 | KB-03 齿轮箱 |
| KB03-GB-VIB-DANGER | kb03_text_present | incomplete | kb03_only_no_l2_ped | none | **blocked_by_measurement** | 同上 | 同上 |
| KB03-GB-OIL-TEMP-ALARM | kb03_text_present | complete* | kb03_only_no_l2_ped | none | **blocked_by_applicability** | 无 L2；开发集**无油温列** | KB-03 油温；xlsx 列清单 |
| KB03-GB-OIL-TEMP-DANGER | kb03_text_present | complete* | kb03_only_no_l2_ped | none | **blocked_by_applicability** | 同上 | 同上 |
| KB03-CWP-VIB-ALARM | kb03_text_present | incomplete | kb03_only_no_l2_ped | none | **blocked_by_measurement** | statistic + 无 L2 | KB-03 冷却泵 |
| KB03-CWP-BEARING-TEMP-ALARM | kb03_text_present | complete* | kb03_only_no_l2_ped | none | **blocked_by_applicability** | 无 L2/PED | KB-03 |
| KB03-CWP-BEARING-TEMP-DANGER | kb03_text_present | complete* | kb03_only_no_l2_ped | none | **blocked_by_applicability** | 无 L2/PED | KB-03 |
| YAML-MM-VIB-X-ALARM | legacy_yaml_unverified | incomplete | type_scoped_with_l2_config | none | **blocked_by_measurement** | statistic；来源未核实 | L3 YAML |
| YAML-MM-VIB-Y-ALARM | legacy_yaml_unverified | incomplete | type_scoped_with_l2_config | none | **blocked_by_measurement** | 同上 | L3 YAML |
| YAML-MM-BEARING-TEMP-DE-AS-ALARM | legacy_yaml_unverified | complete* | type_scoped_with_l2_config | **blocking** | **blocked_by_conflict** | conflict；YAML 未核实 | L3 YAML；conflict |
| YAML-MM-BEARING-TEMP-NDE-AS-ALARM | legacy_yaml_unverified | complete* | type_scoped_with_l2_config | **blocking** | **blocked_by_conflict** | 同上 | 同上 |
| YAML-DP-VIB-X-ALARM | legacy_yaml_unverified | incomplete | type_scoped_with_l2_config | none | **blocked_by_measurement** | statistic；无 KB-03 除鳞章 | L3 YAML |
| YAML-DP-VIB-Y-ALARM | legacy_yaml_unverified | incomplete | type_scoped_with_l2_config | none | **blocked_by_measurement** | 同上 | L3 YAML |
| YAML-DP-VIB-MAX-ALARM | legacy_yaml_unverified | complete* | type_scoped_with_l2_config | none | **blocked_by_source** | 规程/版本未运营核实；max 定义仅 B | L3 YAML；semantics |
| YAML-DP-BEARING-TEMP-DE-ALARM | legacy_yaml_unverified | complete* | type_scoped_with_l2_config | none | **blocked_by_source** | 非 KB-03；来源未核实 | L3 YAML |

\*complete = **仅** L5b 比较门控层（kind/unit/statistic 已填）。采集/传感器/工况仍为 unresolved，**不**等于可批准。

---

## 4. 三组划分（任务六）

### 第一组：技术依据已完整 → 可进入人工审核准备

**空集（0 条）。**  
如实报告：无任何规则同时满足「门控语义完整 + 来源可核为正式规程 + 无阻断冲突 + 设备/测点管道可落地 + 观测字段存在」。

### 第二组：有参考价值但依据不完整（仅参考比对）

| 规则 | 保留原因 | 不可正式告警原因 |
|---|---|---|
| 全部 KB 振动规则（MM/GB/CWP） | KB 原文有限值 | statistic unresolved |
| YAML-MM/DP 振动 X/Y | 与历史 L3 一致可参考 | statistic + 来源 |
| YAML-DP-VIB-MAX / YAML-DP-BEARING-TEMP | 有 L2 与数值 | 来源未核实；无批准 |
| KB03-CWP 温度 / KB03-GB 油温 | KB 原文 | 无 L2；油温无列 |

### 第三组：明确冲突或不适用（保持阻断）

| 规则 | 原因 |
|---|---|
| KB03-MM-BEARING-TEMP-ALARM/DANGER | 与 YAML 冲突，整组阻断 |
| YAML-MM-BEARING-TEMP-DE/NDE-AS-ALARM | `review_status=conflict` |
| （适用性禁令，非目录删除）CWP↛DP；GB↛MM | conflict_detector notes + applicability |

风机 / 连铸：**无 AlertRule** → 不适用本矩阵启用，属能力未建。

---

## 5. 与生产状态

- `approvals_v1.json` → `approvals: []`  
- 目录中 `review_status=approved` 条数 = **0**  
- 因此 **当前不可安全启用任何正式安全告警**（formal_alerts 为空为正确行为）。

技术条件满足也**不得**自动批准。
