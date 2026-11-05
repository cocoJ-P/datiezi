# L5B｜设备测量语义与规程适用性缺口清单

> 性质：只读核验材料，非正式批准文件。  
> 目录基准：`alert_rule_catalog_v1_1`（19 条）。  
> 原则：不猜测 RMS/峰值换算；不完整一律 `unresolved`。

---

## 0. 证据分层约定

| 等级 | 含义 |
|---|---|
| **A** | 仓库已有直接证据，可确认 |
| **B** | 有间接信息，不足以确认 |
| **C** | 仓库没有，须外部补充 |

---

## 1. 六类设备能力事实（不以有表名伪装分析能力）

| # | 设备 | 官方 Excel 表 | L2 EquipmentSpec | L3 thresholds YAML | L5 AlertRule | KB-03 专章 | PED 快照目录 |
|---|---|---|---|---|---|---|---|
| 1 | 主轧机电机 | 有（`主轧机电机`） | **有** `main_mill_motor` | **有** | KB03-MM-* / YAML-MM-* | **有** | `data/runtime/l2_prepared` **空** |
| 2 | 高压除鳞泵 | 有 | **有** `high_pressure_descaling_pump` | **有** | YAML-DP-* | **无**（全文无“除鳞”） | 同上空；模型引用 PED id 但目录无文件 |
| 3 | 主减速机/齿轮箱 | 有（表名`主减速机_齿轮箱`） | **无** | **无** | KB03-GB-* | **有**（齿轮箱） | 无 |
| 4 | 冷却水循环泵 | 有 | **无** | **无** | KB03-CWP-* | **有** | 无 |
| 5 | 加热炉助燃风机 | 有 | **无** | **无** | **无** | **无** | 无 |
| 6 | 连铸机扇形段驱动电机 | 有 | **无**（运行态见 `eq_a387080507`） | **无** | **无** | **无** | 无本地 PED 文件 |

**原始数据源（A）：** `data/official_dataset/钢铁设备数据集_开发集.xlsx`（六表同构 34 列）。  
**KB-03（A）：** `data/official/KB-03_操作规程与技术手册.docx`；衍生 `data/processed/markdown/KB-03_操作规程与技术手册.md`。  
**独立传感器手册 / 采样日志 / 数据字典文件：** **ABSENT（C）**。

---

## 2. 官方数据集公共列（六表相同）

源：开发集 xlsx 各表第 1 行（A）。

标识：`记录编号`, `设备编号`, `设备类别`, `采集时间`  
过程量：`转速(rpm)`, `转频(Hz)`, `电机电流(A)`  
振动相关：`X向振动(mm/s)`, `Y向振动(mm/s)`, `最大振动(mm/s)`, `振动均方根`（列名**无单位**）  
温度：`驱动端轴承温度(°C)`, `非驱动端轴承温度(°C)`  
频谱：`包络谱峰值(g)`, `BPFO/BPFI/BSF幅值(g)`, `啮合频率幅值(g)`, `1X转频幅值(mm/s)`, 对应频率列  
标签/诊断输出列：状态、故障类型等（非本告警核验重点）

**缺口：** 六表均**无** `油温(°C)` / `齿轮箱油温(°C)` 列 → KB-03 齿轮箱油温规则在现网 PED 字段上**无法对应**（见下）。

---

## 3. L1 Schema 对测量字段的已确认 / unresolved

源：`backend/app/services/l1_schema/definitions.py`（A）。  
`StandardField` **无 statistic 属性**。

| 原始列名 | standard_field_id | 物理量(dimension) | 单位 | 统计量 | 方向 | 证据等级 |
|---|---|---|---|---|---|---|
| X向振动(mm/s) | vibration_x | velocity（描述：X向振动速度） | mm/s | **unresolved** | X（列名） | A：速度；C：RMS/peak |
| Y向振动(mm/s) | vibration_y | velocity | mm/s | **unresolved** | Y | 同上 |
| 最大振动(mm/s) | vibration_max | velocity（最大振动速度） | mm/s | **unresolved（Schema）**；L5b 标注 `max` 为**解释性**登记，非采集规范证明 | unknown | B |
| 振动均方根 | vibration_rms | velocity；描述含「均方根」；`eligibility=requires_review` | mm/s（Schema）；PED 配置 `unit: null` | 名称像 RMS（B），**无窗口/仪器证明** → 采集定义 **unresolved** | unknown | B≠A |
| 驱动端/非驱动端轴承温度 | bearing_temperature_* | temperature | °C | instantaneous/point（无 Schema 字段；L5b 记 `point`） | n/a；位置仅列名 | A：温/单位；C：传感器与安装点 |
| 油温 | — | — | — | — | — | **L1 ABSENT** |

**明确禁止升级：** 不得因列名含「均方根」或映射桥 `rms_from_axes`（`equipment_mappings.yaml`，B）认定现场 X/Y 即为规程 RMS。

---

## 4. 分设备测量字段清单（告警相关子集）

### 4.1 主轧机电机（`main_mill_motor`）

| 字段 | canonical / feature_id | 能否对拍 KB-03 | 统计量 | 测点/传感器/采样 | 结论 |
|---|---|---|---|---|---|
| X/Y 振动 | `X/Y向振动(mm/s)` / vibration_x\|y | KB 写 X/Y mm/s，**未写 RMS** | unresolved | 仅列名方向；传感器/频率/窗口 **C** | **不可**在语义完整前提下启用振动正式告警 |
| 最大振动 | 有列；L2 MM 配置**未列**为 required 特征 | KB 电机章写 X/Y，未单独定义“最大” | unresolved | C | 与 X/Y/RMS **不得等价** |
| 振动均方根 | 开发集有列；L2 MM **未纳入** features | KB 未写 RMS | 名称 B | C | 与阈值对应 **unresolved** |
| DE/NDE 轴承温度 | 有；L2 有 | KB 75 alarm / 85 danger | point（L5b） | 位置仅列名 | 语义门控可比较，但与 YAML **冲突阻断** |

### 4.2 高压除鳞泵（`high_pressure_descaling_pump`）

| 字段 | 对拍规程 | 结论 |
|---|---|---|
| X/Y / 最大 / 振动均方根 / DE 温度 | **无 KB-03 除鳞专章**；仅有 legacy YAML | 振动统计量 unresolved；YAML 来源未运营核实；**禁止**用冷却泵规程替代 |

L2 含 `振动均方根` 且 `unit: null`；另有 feature bridge 用 X/Y 合成 RMS（B，非采集规范）。

### 4.3 主减速机/齿轮箱

| 事实 | 等级 |
|---|---|
| 开发集有同构振动/轴承温度列 | A |
| KB-03 有振动 mm/s 与**油温**限值 | A |
| L2/L3/PED 管道 **无** | A（缺失） |
| 开发集**无油温列** → `KB03-GB-OIL-TEMP-*` 特征无法从现表观测 | A |
| 振动统计量 | unresolved（C） |

### 4.4 冷却水循环泵

同 4.3：有原始表与 KB-03；无 L2/L3；振动 statistic unresolved；温度规则语义可门控但无分析管道。

### 4.5 加热炉助燃风机 / 4.6 连铸扇形段驱动电机

| 事实 | 等级 |
|---|---|
| 开发集有表与设备编号样例（如 FF-01 / CC-01） | A |
| KB-03 **无**对应专章 | A |
| L5 AlertRule **0** 条 | A |
| L2 正式 equipment_type **无**（连铸仅见 onboarding `eq_a387080507`） | A |
| 测量语义（统计量/传感器等） | 全部 **unresolved / C** |
| **不得**声称已具备 L5b 规程告警能力 | — |

---

## 5. 与 KB-03 / YAML 阈值对应总表

| 规程限值（KB-03 原文要点） | 目录规则 | PED 字段能否对应 | 统计量可否证明 | 备注 |
|---|---|---|---|---|
| 电机 X/Y ≤7.1 报警 / >11.2 危险 | KB03-MM-VIB-XY-* | 有 X/Y 列 | **否** | 原文仅 mm/s |
| 电机轴承 75/85 | KB03-MM-BEARING-TEMP-* | 有 DE/NDE | 温度 point 可门控 | **与 YAML 85=alarm 冲突** |
| 齿轮箱振动 8.5/12.5 | KB03-GB-VIB-* | 有 X/Y 列但无 L2 | **否** | 不得套电机 |
| 齿轮箱油温 85/90 | KB03-GB-OIL-TEMP-* | **无油温列** | n/a | 字段缺失 |
| 冷却泵振动 ≤6.5 报警 | KB03-CWP-VIB-* | 有列无 L2 | **否** | 无危险级振动；↛除鳞泵 |
| 冷却泵温 72/82 | KB03-CWP-BEARING-TEMP-* | 有列无 L2 | point 可门控 | ↛除鳞泵 |
| YAML 电机温 85 alarm | YAML-MM-BEARING-TEMP-*-AS-ALARM | 有 | point | **conflict** |
| YAML 除鳞泵振动/温度 | YAML-DP-* | 有 | 振动 unresolved；max/temp 可门控 | 非 KB-03；来源未核实 |

---

## 6. 已确认 vs unresolved 摘要

### 已确认（A）

- X/Y/最大振动的**量纲为速度**、单位 mm/s（L1）。  
- 轴承温度为温度、单位 °C（L1）。  
- KB-03 三章限值原文存在且可定位。  
- 电机温度 KB vs YAML 为同设备、同 DE/NDE 测点名义下的**级别定义冲突**（见矩阵）。  
- 冷却泵 ≠ 除鳞泵；齿轮箱 ≠ 电机本体（冲突检测器已记录）。  
- 生产 `approvals_v1.json` 批准记录数 = **0**。

### unresolved（不得猜测）

- X/Y 是否 RMS / peak / 其它。  
- `振动均方根` 的计算窗口、滤波、是否速度 RMS、单位是否已换算。  
- 最大振动的聚合定义与传感器通道。  
- 全部频谱幅值的统计与仪器定义。  
- 传感器型号、安装位置精确描述、采样率、记录频率。  
- 工况（转速/负载）是否满足规程适用条件。  
- 除鳞泵 / 风机 / 连铸的正式规程版本。  
- 齿轮箱油温在现网数据中的字段来源。

---

## 7. 仓库能解决什么（任务四对照）

### A. 可直接确认

- L1 字段量纲与目标单位：`definitions.py`。  
- 六表列名：官方开发集 xlsx。  
- KB-03 限值段落：markdown/docx。  
- 现网规则与冲突：`alert_rules_v1_1.json`、`conflict_detector.py`。  
- L5b 语义登记：`semantics.py`（**登记≠采集证明**）。

### B. 间接不足

- 列名「振动均方根」、L1 描述「振动速度均方根」。  
- `rms_from_axes` 开发桥接。  
- YAML `source_reference`（ISO/plant-guideline-internal）。  
- L5b 将温度记为 `point`、最大振动记为 `max`。

### C. 必须外部

- 采集系统：统计量定义、传感器、窗口、换算。  
- 设备工程师：型号、测点图、工况。  
- 规程/运营：权威版本、75/85 裁决、除鳞泵专用限值、批准人与审计。

---

## 8. 本文件结论

当前**没有任何**设备的振动 X/Y 具备可证明的统计量定义；  
温度类在 L5b 门控层可比较，但主轧电机被冲突阻断，齿轮箱/冷却泵缺 L2 管道，除鳞泵缺 KB-03 与批准。  
风机与连铸：**无规程规则、无 L5 目录条目**，仅有原始表，**不构成告警能力**。
