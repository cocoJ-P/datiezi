import type { DiagnosticTaskSource, DiagnosticTaskStatus, EngineAvailability, EngineExecution, FailurePolicy, StageId, StageState, TriggerType } from "./types"

export const TASK_STATUS_LABEL: Record<DiagnosticTaskStatus, string> = {
  queued: "等待中",
  running: "执行中",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
}

export const SOURCE_LABEL: Record<DiagnosticTaskSource, string> = {
  automatic: "自动",
  manual: "手动",
  event: "事件",
}

export const STAGE_LABEL: Record<StageId, string> = {
  l1: "L1 数据检查",
  l2: "L2 分析调度",
  l3: "L3 多引擎分析",
  l4: "L4 证据验证",
  l5: "L5 交付反馈",
}

export const STAGE_STATE_LABEL: Record<StageState, string> = {
  pending: "未开始",
  waiting: "等待",
  running: "运行中",
  completed: "已完成",
  failed: "失败",
  skipped: "已跳过",
}

export const TRIGGER_LABEL: Record<TriggerType, string> = {
  periodic: "周期触发",
  new_data: "新数据触发",
  anomaly: "异常事件触发",
}

export const FAILURE_LABEL: Record<FailurePolicy, string> = {
  record: "只记录失败",
  retry: "演示重试一次",
  pause: "暂停该设备的演示配置",
}

export const AVAILABILITY_LABEL: Record<EngineAvailability, string> = {
  available: "已部署",
  unavailable: "不可用",
  not_deployed: "未部署",
}

export const EXECUTION_LABEL: Record<EngineExecution, string> = {
  none: "无运行实例",
  demo_running: "演示执行中",
  demo_completed: "演示已完成",
  demo_failed: "演示失败",
}

export const STRATEGIES = [
  { id: "std", label: "标准多引擎分析" },
  { id: "rules", label: "规则优先" },
] as const

export function strategyLabel(id: string): string {
  return STRATEGIES.find((item) => item.id === id)?.label ?? id
}

export function formatStamp(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  const pad = (value: number) => String(value).padStart(2, "0")
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}
