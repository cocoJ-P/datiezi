export const DIAGNOSIS_GROUPS = [
  {
    group: "overview",
    groupLabel: "运行控制",
    items: [
      {
        id: "overview",
        label: "诊断与控制总览",
        crumb: "运行控制",
        title: "诊断与控制总览",
        summary: "数据接入入口 · L1–L5 流程状态 · 跳转技术诊断体系",
      },
    ],
  },
  {
    group: "architecture",
    groupLabel: "诊断技术体系",
    items: [
      {
        id: "l1",
        prefix: "L1",
        label: "数据",
        crumb: "诊断技术体系 / L1",
        title: "数据与知识",
        summary: "数据接入 · 原始数据检视 · 数据治理调试",
      },
      {
        id: "l2",
        prefix: "L2",
        label: "调度",
        crumb: "诊断技术体系 / L2",
        title: "智能推理与调度",
        summary: "任务编排 · 工具选择 · 推理轮次",
      },
      {
        id: "l3",
        prefix: "L3",
        label: "引擎",
        crumb: "诊断技术体系 / L3",
        title: "多引擎分析",
        summary: "物理规则 · 统计分析 · 机器学习 · 概率推断 · Transformer",
      },
      {
        id: "l4",
        prefix: "L4",
        label: "证据",
        crumb: "诊断技术体系 / L4",
        title: "诊断证据融合与验证",
        summary: "支持证据 · 反证 · 物理一致性 · 不确定性",
      },
      {
        id: "l5",
        prefix: "L5",
        label: "反馈",
        crumb: "诊断技术体系 / L5",
        title: "诊断交付与知识反馈",
        summary: "复核 · 知识回写 · 交付交接",
      },
    ],
  },
  {
    group: "system",
    groupLabel: "系统能力",
    items: [
      {
        id: "harness",
        label: "执行保障",
        crumb: "系统能力 / Harness",
        title: "诊断执行保障",
        summary: "任务状态 · 执行轨迹 · 异常恢复 · 审计",
      },
      {
        id: "strategy",
        label: "策略优化",
        crumb: "系统能力 / 策略",
        title: "策略与学习",
        summary: "Jev · Bandit · 强化学习",
      },
    ],
  },
] as const

type DiagnosisGroup = (typeof DIAGNOSIS_GROUPS)[number]
export type DiagnosisTab = DiagnosisGroup["items"][number]
export type DiagnosisTabId = DiagnosisTab["id"]

export const DIAGNOSIS_TABS: DiagnosisTab[] = DIAGNOSIS_GROUPS.flatMap((group) => [...group.items])

const TAB_IDS = new Set<string>(DIAGNOSIS_TABS.map((item) => item.id))

export function diagnosisTabId(value: string | null | undefined): DiagnosisTabId {
  if (value === "result") return "overview"
  if (value && TAB_IDS.has(value)) return value as DiagnosisTabId
  return "overview"
}

/** 默认「诊断运行总览」不写入地址。旧的 result 参数也回到总览。 */
export function diagnosisTabQuery(value: string | null | undefined): string | undefined {
  const id = diagnosisTabId(value)
  return id === "overview" ? undefined : id
}

export function diagnosisTabById(id: DiagnosisTabId): DiagnosisTab {
  return DIAGNOSIS_TABS.find((item) => item.id === id) ?? DIAGNOSIS_TABS[0]
}
