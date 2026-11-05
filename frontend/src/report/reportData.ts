/** 静态演示数据（非 L5 引擎）。报告中心默认展示真实 L5Run；仅在「演示数据」模式读取本文件。 */

export type DiagnosticReport = {
  report_id: string
  title: string
  status: "released" | "draft" | "archived"
  version: string
  issued_at: string
  issued_by: string
  reviewed_by: string
  plant: string
  line: string
  equipment_name: string
  equipment_id: string
  equipment_type: string
  model: string
  collection_time: string
  diagnosis_run_id: string
  analysis_run_id: string
  screening_result: string
  preliminary_fault: string
  device_status: string
  fault_type: string
  root_cause: string
  evidence: Array<{ id: string; title: string; detail: string; source: string }>
  risk_level: string
  risk_rationale: string
  impact_scope: string
  impact_detail: string
  recommendations: Array<{ action: string; owner: string; window: string }>
  priority: string
  priority_rationale: string
  spectral_summary: string
  limitations: string[]
  knowledge_refs: string[]
}

export const DIAGNOSTIC_REPORTS: DiagnosticReport[] = [
  {
    report_id: "DR-2026-0314-017",
    title: "棒线主轧机电机驱动端轴承外圈剥落诊断报告",
    status: "released",
    version: "v1.2",
    issued_at: "2026-03-14 16:42:08",
    issued_by: "诊断引擎 L3 / 设备诊断专责 张衡",
    reviewed_by: "机电运维主管 刘凯",
    plant: "棒线厂",
    line: "棒线主传动区域",
    equipment_name: "棒线主轧机电机",
    equipment_id: "MM-01",
    equipment_type: "三相异步电动机",
    model: "YKK 800-4 / 5500 kW",
    collection_time: "2026-03-14 09:18:00 ~ 2026-03-14 15:06:00",
    diagnosis_run_id: "DR-7c2a91e04b18",
    analysis_run_id: "AR-5c5c1ec454e0",
    screening_result: "ABNORMAL",
    preliminary_fault: "轴承磨损",
    device_status: "异常运行（允许短期维持，需限期处理）",
    fault_type: "驱动端轴承外圈剥落（中期）",
    root_cause:
      "驱动端滚动轴承外圈滚道出现疲劳剥落。长期重载与润滑脂补给不足叠加，导致接触应力区反复塑性变形，剥落坑形成后诱发周期性冲击；包络谱中 BPFO 及其谐波增强，并调制在转频附近，与外圈故障机理一致。",
    evidence: [
      {
        id: "EV-01",
        title: "振动总值持续越限",
        detail:
          "驱动端水平向振动有效值由 2.1 mm/s 逐步升至 7.8 mm/s（近 3 个月），当前测点已超过厂内报警限 7.1 mm/s（参照 ISO 10816 与厂标规程）。",
        source: "L3 Threshold Analyzer · thr:REC-MA-20260314-00008:mill_vib_x_alarm",
      },
      {
        id: "EV-02",
        title: "轴承外圈特征频率突出",
        detail:
          "包络谱在 BPFO≈87.3 Hz 处出现稳定峰值，并可见 2×BPFO、3×BPFO 谐波；峭度与峰值因子同步抬升，符合外圈剥落冲击特征。",
        source: "L3 Mechanical Feature Analyzer · 包络谱标量特征",
      },
      {
        id: "EV-03",
        title: "温度伴随缓升",
        detail:
          "驱动端轴承温度由约 55°C 升至 68~72°C 区间波动，尚未达到紧急停机阈值，但与振动趋势同向，支持轴承工况劣化判断。",
        source: "过程测点 / L3 阈值复核",
      },
      {
        id: "EV-04",
        title: "知识与案例一致性",
        detail:
          "与 KB-02《轴承磨损》频谱特征及 EXP-2024-001（主电机驱动端轴承外圈剥落典型案例）描述一致：BPFO 主导 + 振动总值上升 + 中期可维护窗口。",
        source: "Knowledge Snapshot KS-757bbb5b681923d8 · EXP-2024-001",
      },
    ],
    risk_level: "高风险（橙色）",
    risk_rationale:
      "故障已进入中期剥落阶段。若继续满负荷运行，存在外圈剥落扩大、保持架损伤乃至突然抱死的风险；当前尚未出现瞬时跳停前兆，但振动已稳定处于报警区之上。",
    impact_scope: "本机驱动端轴承及联轴器侧；短时影响主轧机可开动率",
    impact_detail:
      "直接影响棒线主轧机电机驱动端轴承与附近联轴器对中状态。若恶化为突发停机，将导致本条棒线主传动短时停产；冷却水回路与下游轧机暂无直接传播证据，本次不判定为跨系统根因传播事件。",
    recommendations: [
      {
        action: "48 小时内安排驱动端轴承专项点检：复测包络谱、轴承温度与润滑脂加注记录，确认 BPFO 趋势斜率。",
        owner: "点检班 / 振动分析员",
        window: "0–48 h",
      },
      {
        action: "计划检修窗口更换驱动端轴承（建议同批次备件，安装后复测对中与润滑脂量）；更换前控制负荷尖峰，避免冲击轧制。",
        owner: "机电检修班",
        window: "本周计划检修（建议 ≤7 天）",
      },
      {
        action: "恢复后连续 72 小时加密监测振动与温度，确认 BPFO 峰值回落至基线；更新该电机润滑周期台账。",
        owner: "运维专责",
        window: "更换后 0–72 h",
      },
    ],
    priority: "P1（高优先级）",
    priority_rationale:
      "设备处于主传动关键路径，振动已越限且机理证据指向轴承中期损伤；可维护窗口仍在，但延迟将显著抬高突发停机概率，故列为当周优先处置项目。",
    spectral_summary:
      "主导异常为轴承外圈特征频率（BPFO）及其谐波；转频 1X 有抬升但未形成单独确诊不平衡的充分条件（缺相位/2X 完整证据）。频谱描述区分：BPFO/谐波为实测标量支持；案例比对为知识参考。",
    limitations: [
      "缺少同步相位稳定性与完整 2X 占比曲线，不对转子不平衡作并列确诊。",
      "未取得拆检照片前，剥落程度按频谱与趋势推断为中期，最终以解体验证为准。",
      "本报告影响范围仅覆盖当前设备及短时产线可开动率，不含跨厂级经济评估。",
    ],
    knowledge_refs: [
      "KB-02 故障模式诊断知识库 / 轴承磨损",
      "KB-03 操作规程与技术手册 / 振动限值",
      "KB-04 EXP-2024-001 主电机驱动端轴承外圈剥落典型案例",
      "Knowledge Snapshot：KS-757bbb5b681923d8",
    ],
  },
]
