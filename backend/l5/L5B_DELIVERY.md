# L5b｜测量语义一致性与正式规则门控补充验收

## A. 修改文件（本轮最小补丁）

- `backend/l5/alerts/catalog/alert_rules_v1_1.json`（新版本；`v1.json` 保留不覆盖）
- `backend/l5/alerts/catalog/approvals_v1.json`（生产空批准日志）
- `backend/l5/alerts/semantics.py`
- `backend/l5/alerts/approvals.py`
- `backend/l5/alerts/applicability.py`
- `backend/l5/alerts/observations.py`
- `backend/l5/alerts/evaluator.py`
- `backend/l5/alerts/catalog.py`（默认指向 v1_1）
- `backend/l5/alerts/__init__.py`（`alert_rule_catalog_v1_1` / `alert_evaluation_v1_1`）
- `backend/l5/alerts/tests/test_alerts.py`（语义 + 隔离正式门控夹具）
- `backend/l5/alerts/L5B_SEMANTICS_TRACES.json`（三条真实路径）
- 本文档

**未改**：原始 PED、L1–L4 算法、L5a 十字段、历史 AlertRun 行（新 run 新 catalog_version）。

## B. 振动测量语义审计结论

| 问题 | 结论 |
|---|---|
| X/Y 是否明确 RMS？ | **否**。L1 仅定义速度；仅 `振动均方根` 字段声明 RMS |
| X/Y 测量类型？ | **振动速度**（mm/s），非位移；统计量 `unresolved` |
| 最大振动 ≡ RMS / X/Y？ | **否**。`vibration_velocity_max` + statistic=`max`，与分量不等价 |
| 统计量不明时是否比较？ | **否**。applicability=`unresolved` → evaluation=`indeterminate`，`comparison_holds=null` |
| REC-DP-00021 / REC-MA-00008 原 reference triggered？ | **测量语义依据不足**；v1_1 改为 `indeterminate`，不猜 RMS |

KB-03：给出 mm/s 限值，**未写 RMS**。YAML 振动阈值同步纠正为 `vibration_velocity` + `statistic=unresolved`。

## C. 正式规则门控（隔离夹具）

生产：`approved=0`，`approvals_v1.approvals=[]`。

夹具验证（不改生产目录）：

1. 设备/测点/单位/测量类型全匹配 + 批准审计 → 可正式告警  
2. 设备/单位/measurement_kind 不匹配 → 无正式告警  
3. 冲突存在时，即使 `approved`+审计 → 仍 `rule_conflict`，无正式告警  
4. `needs_review` → 仅参考比对  
5. 测量语义缺失（X/Y statistic unresolved）→ `indeterminate`，无正式告警  
6. 仅 `review_status=approved`、无授权主体/时间记录 → `reference_approved_without_audit`，非正式告警  

## D. 真实样本路径（≥3）

详见 `backend/l5/alerts/L5B_SEMANTICS_TRACES.json`。

| 记录 | 设备 | 关键路径 | 状态 |
|---|---|---|---|
| REC-DP-20260314-00021 | 除鳞泵 | X/Y → vibration_velocity / statistic unresolved → YAML-DP-VIB-* → applicability unresolved → **无比较** → `indeterminate`；温度规则 within_limit（参考） | formal=0 |
| REC-DP-20260314-00162 | 除鳞泵 | 同上；振动 indeterminate；温度 within_limit | formal=0 |
| REC-MA-20260314-00008 | 主轧电机 | X/Y → unresolved → KB/YAML vib → `indeterminate`；轴承温度 → `rule_conflict`（KB↔YAML） | formal=0 |

缺失元数据：X/Y **statistic**；大量频谱字段无 Schema measurement_kind 注册（本轮不猜测）。

## E. 回归

- KB-03 / YAML 温度冲突继续阻断：通过  
- 冷却泵 ↛ 除鳞泵：通过  
- 齿轮箱 ↛ 电机：通过  
- 无 risk_level / P0–P3 / 维修指令：保持  
- L5a 测试 6 passed；alerts 20 passed；合计 **26 passed**  
- 生产批准数仍为 0  

## F. 冻结判断

**不能无条件冻结 L5b 规则引擎为“业务阈值完备”。**

可确认冻结的是：**v1_1 门控行为**（语义不明不比较、正式告警需批准审计、冲突不可绕开、设备域隔离）。  
不可无条件冻结的原因：振动统计量仍 unresolved；生产无 approved+审计规则；除鳞泵无 KB-03 专用规程。

**停止，不进入 L5c。**
