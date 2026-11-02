import { useSyncExternalStore } from "react"
import { DEMO_LINE } from "../overview/demoData"
import { equipmentOf } from "../overview/model"
import type {
  DatasetBatch,
  DatasetRole,
  DiagnosticRun,
  DiagnosticState,
  DiagnosticTaskStatus,
  EngineRuntime,
  FailurePolicy,
  MonitoringPlan,
  StageId,
  StageSnapshot,
  TriggerType,
} from "./types"

const STAGES: StageId[] = ["l1", "l2", "l3", "l4", "l5"]

export function equipmentLabel(id: string): string {
  const item = equipmentOf(DEMO_LINE, id)
  return item ? `${item.name} ${item.code}` : id
}

function stageNote(stage: StageId, state: StageSnapshot["state"]): string {
  if (state === "pending") return "尚未开始"
  if (state === "waiting") return "等待前序或调度"
  if (state === "skipped") return "本轮未执行"
  if (state === "failed") return "演示步骤失败，没有产出已确认故障"
  if (state === "running") return stage === "l3" ? "演示映射到五大引擎，引擎服务本身未部署" : "演示执行中"
  return "演示步骤已结束，不是已确认故障"
}

export function buildStages(status: DiagnosticTaskStatus, current?: StageId, failedStage?: StageId): StageSnapshot[] {
  return STAGES.map((stage) => {
    if (status === "queued" || status === "cancelled") return { stage, state: "pending", note: stageNote(stage, "pending") }
    if (status === "completed") return { stage, state: "completed", note: stageNote(stage, "completed") }
    if (status === "failed") {
      const failAt = failedStage ?? "l2"
      const failIndex = STAGES.indexOf(failAt)
      const index = STAGES.indexOf(stage)
      if (index < failIndex) return { stage, state: "completed", note: stageNote(stage, "completed") }
      if (index === failIndex) return { stage, state: "failed", note: stageNote(stage, "failed") }
      return { stage, state: "skipped", note: stageNote(stage, "skipped") }
    }
    const cursor = STAGES.indexOf(current ?? "l1")
    const index = STAGES.indexOf(stage)
    if (index < cursor) return { stage, state: "completed", note: stageNote(stage, "completed") }
    if (index === cursor) return { stage, state: "running", note: stageNote(stage, "running") }
    if (index === cursor + 1) return { stage, state: "waiting", note: stageNote(stage, "waiting") }
    return { stage, state: "pending", note: stageNote(stage, "pending") }
  })
}

function run(partial: Omit<DiagnosticRun, "demo" | "stages" | "reportHandoff" | "cycleNote" | "inputNote"> & Partial<Pick<DiagnosticRun, "cycleNote" | "inputNote" | "reportHandoff">> & { failedStage?: StageId }): DiagnosticRun {
  const { failedStage, ...rest } = partial
  return {
    ...rest,
    demo: true,
    reportHandoff: partial.reportHandoff ?? "none",
    cycleNote: partial.cycleNote ?? "L2 可以根据 L4 的证据缺口再次调用 L3。当前轨迹是演示快照。",
    inputNote: partial.inputNote ?? "演示任务没有把答案字段送入模型。",
    stages: buildStages(partial.status, partial.currentStage, failedStage),
  }
}

function plan(partial: Omit<MonitoringPlan, "demo" | "equipmentLabel">): MonitoringPlan {
  return { ...partial, demo: true, equipmentLabel: equipmentLabel(partial.equipmentId) }
}

function seed(): DiagnosticState {
  return {
    plans: [
      plan({
        id: "MP-M01",
        equipmentId: "drv-motor",
        enabled: true,
        dataSource: "模拟快照，未连接采集服务",
        triggerType: "new_data",
        strategyId: "std",
        failurePolicy: "record",
        lastRunAt: "2026-10-30T18:20:00",
        lastStatus: "running",
      }),
      plan({
        id: "MP-G01",
        equipmentId: "drv-gear",
        enabled: true,
        dataSource: "模拟快照，未连接采集服务",
        triggerType: "anomaly",
        anomalyRule: "轴承温度高于演示快照中的关注值",
        strategyId: "rules",
        failurePolicy: "retry",
        lastRunAt: "2026-10-30T18:05:00",
        lastStatus: "queued",
      }),
      plan({
        id: "MP-SP01",
        equipmentId: "cw-spray",
        enabled: false,
        dataSource: "模拟快照，未连接采集服务",
        triggerType: "periodic",
        periodMinutes: 30,
        strategyId: "std",
        failurePolicy: "pause",
        lastRunAt: "2026-10-30T17:40:00",
        lastStatus: "completed",
      }),
    ],
    batches: [
      {
        id: "DS-07",
        fileName: "齿轮箱抽检.csv",
        rowCount: 128,
        equipmentIds: ["drv-gear"],
        datasetRole: "inference",
        uploadedAt: "2026-10-30T16:10:00",
        columns: ["设备编号", "采集时间", "振动均方根", "轴承温度"],
        withheldColumns: [],
      },
    ],
    runs: [
      run({
        id: "DR-M01-18",
        name: "主电机新数据检查",
        source: "automatic",
        equipmentIds: ["drv-motor"],
        equipmentLabel: equipmentLabel("drv-motor"),
        monitoringPlanId: "MP-M01",
        createdAt: "2026-10-30T18:20:00",
        startedAt: "2026-10-30T18:20:04",
        status: "running",
        currentStage: "l3",
        strategyId: "std",
        dataMode: "simulated",
        windowLabel: "2026-10-30 18:20:00",
      }),
      run({
        id: "DR-G01-12",
        name: "主减速机异常触发",
        source: "automatic",
        equipmentIds: ["drv-gear"],
        equipmentLabel: equipmentLabel("drv-gear"),
        monitoringPlanId: "MP-G01",
        createdAt: "2026-10-30T18:05:00",
        status: "queued",
        strategyId: "rules",
        dataMode: "simulated",
        windowLabel: "2026-10-30 18:05:00",
      }),
      run({
        id: "DR-M01-09",
        name: "主电机上一轮检查",
        source: "automatic",
        equipmentIds: ["drv-motor"],
        equipmentLabel: equipmentLabel("drv-motor"),
        monitoringPlanId: "MP-M01",
        createdAt: "2026-10-30T15:02:00",
        startedAt: "2026-10-30T15:02:03",
        completedAt: "2026-10-30T15:06:40",
        status: "completed",
        currentStage: "l5",
        strategyId: "std",
        dataMode: "simulated",
        windowLabel: "2026-10-30 15:02:00",
        cycleNote: "演示轨迹：L4 证据不足后，L2 再次调用了 L3，然后结束。没有已确认故障。",
      }),
      run({
        id: "DR-SP-03",
        name: "轧辊冷却温差推断",
        source: "event",
        equipmentIds: ["cw-spray"],
        equipmentLabel: equipmentLabel("cw-spray"),
        eventId: "al-spray",
        diagnosisId: "dx-al-spray",
        createdAt: "2026-10-30T18:28:40",
        startedAt: "2026-10-30T18:28:40",
        completedAt: "2026-10-30T18:29:10",
        status: "completed",
        currentStage: "l5",
        strategyId: "std",
        dataMode: "simulated",
        windowLabel: "2026-10-30 18:28:40",
        inputNote: "来源是事件日志中的未确认推断，不是引擎确诊。",
      }),
      run({
        id: "DR-IMP-07",
        name: "齿轮箱抽检导入",
        source: "manual",
        equipmentIds: ["drv-gear"],
        equipmentLabel: equipmentLabel("drv-gear"),
        datasetId: "DS-07",
        createdAt: "2026-10-30T16:12:00",
        startedAt: "2026-10-30T16:12:08",
        status: "failed",
        currentStage: "l2",
        strategyId: "std",
        dataMode: "imported",
        windowLabel: "导入文件时间范围未完整",
        failedStage: "l2",
        inputNote: "导入预览已完成。失败发生在演示调度，没有调用真实模型。",
      }),
      run({
        id: "DR-C01-02",
        name: "联轴器手动抽查",
        source: "manual",
        equipmentIds: ["drv-coupling"],
        equipmentLabel: equipmentLabel("drv-coupling"),
        createdAt: "2026-10-30T11:16:00",
        status: "cancelled",
        strategyId: "rules",
        dataMode: "simulated",
        windowLabel: "2026-10-30 11:16:00",
      }),
    ],
  }
}

let state: DiagnosticState = seed()
const listeners = new Set<() => void>()
let sequence = 30

function emit() {
  state = { runs: [...state.runs], plans: [...state.plans], batches: [...state.batches] }
  listeners.forEach((listener) => listener())
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function getDiagnosticState(): DiagnosticState {
  return state
}

export function useDiagnosticStore(): DiagnosticState {
  return useSyncExternalStore(subscribe, getDiagnosticState, getDiagnosticState)
}

export function nextId(prefix: string): string {
  sequence += 1
  return `${prefix}-${sequence}`
}

export function engineBoard(runs: DiagnosticRun[]): EngineRuntime[] {
  const engines = [
    { engineId: "e1", name: "物理规则引擎", shortName: "E1 物理规则" },
    { engineId: "e2", name: "统计分析引擎", shortName: "E2 统计分析" },
    { engineId: "e3", name: "机器学习引擎", shortName: "E3 机器学习" },
    { engineId: "e4", name: "概率推断引擎", shortName: "E4 概率推断" },
    { engineId: "e5", name: "Transformer 引擎", shortName: "E5 Transformer" },
  ]
  const running = runs.filter((item) => item.status === "running" && item.currentStage === "l3")
  const latest = [...runs].filter((item) => item.completedAt).sort((a, b) => (b.completedAt ?? "").localeCompare(a.completedAt ?? ""))[0]
  const failed = runs.find((item) => item.status === "failed")
  return engines.map((engine) => ({
    ...engine,
    availability: "not_deployed" as const,
    execution: running.length > 0 ? "demo_running" : latest ? "demo_completed" : failed ? "demo_failed" : "none",
    runningTaskCount: running.length,
    lastExecutedAt: latest?.completedAt ?? running[0]?.startedAt,
    lastResult: running.length > 0 ? "演示任务映射到该引擎，服务未部署" : latest ? "演示快照已结束，没有已确认故障" : "没有演示执行记录",
    demo: true as const,
  }))
}

export function orderedRuns(runs: DiagnosticRun[]): DiagnosticRun[] {
  const rank: Record<DiagnosticTaskStatus, number> = { running: 0, failed: 1, queued: 2, completed: 3, cancelled: 4 }
  return [...runs].sort((a, b) => rank[a.status] - rank[b.status] || b.createdAt.localeCompare(a.createdAt))
}

export function advanceDemo(id: string) {
  const current = state.runs.find((item) => item.id === id)
  if (!current) return
  const index = current.currentStage ? STAGES.indexOf(current.currentStage) : -1
  let next: DiagnosticRun
  if (current.status === "queued" || current.status === "cancelled" || current.status === "failed") {
    next = {
      ...current,
      status: "running",
      currentStage: "l1",
      startedAt: new Date().toISOString(),
      completedAt: undefined,
      stages: buildStages("running", "l1"),
    }
  } else if (current.status === "running" && index < STAGES.length - 1) {
    const stage = STAGES[index + 1]
    next = { ...current, currentStage: stage, stages: buildStages("running", stage) }
  } else {
    const completedAt = new Date().toISOString()
    next = {
      ...current,
      status: "completed",
      currentStage: "l5",
      completedAt,
      stages: buildStages("completed", "l5"),
    }
  }
  state = { ...state, runs: state.runs.map((item) => (item.id === id ? next : item)) }
  emit()
}

export function markReportHandoff(id: string) {
  state = {
    ...state,
    runs: state.runs.map((item) => (item.id === id ? { ...item, reportHandoff: "pending" } : item)),
  }
  emit()
}

export function savePlan(input: {
  id?: string
  equipmentId: string
  enabled: boolean
  dataSource: string
  triggerType: TriggerType
  periodMinutes?: number
  anomalyRule?: string
  strategyId: string
  failurePolicy: FailurePolicy
}) {
  const next = plan({
    id: input.id || nextId("MP"),
    equipmentId: input.equipmentId,
    enabled: input.enabled,
    dataSource: input.dataSource.trim(),
    triggerType: input.triggerType,
    periodMinutes: input.triggerType === "periodic" ? input.periodMinutes : undefined,
    anomalyRule: input.triggerType === "anomaly" ? input.anomalyRule?.trim() : undefined,
    strategyId: input.strategyId,
    failurePolicy: input.failurePolicy,
    lastRunAt: state.plans.find((item) => item.id === input.id)?.lastRunAt,
    lastStatus: state.plans.find((item) => item.id === input.id)?.lastStatus,
  })
  const exists = state.plans.some((item) => item.id === next.id)
  state = { ...state, plans: exists ? state.plans.map((item) => (item.id === next.id ? next : item)) : [next, ...state.plans] }
  emit()
  return next.id
}

export function createManualRun(input: { name: string; equipmentId: string; strategyId: string; windowLabel: string }): string {
  const id = nextId("DR")
  const created: DiagnosticRun = run({
    id,
    name: input.name.trim(),
    source: "manual",
    equipmentIds: [input.equipmentId],
    equipmentLabel: equipmentLabel(input.equipmentId),
    createdAt: new Date().toISOString(),
    status: "queued",
    strategyId: input.strategyId,
    dataMode: "simulated",
    windowLabel: input.windowLabel,
    inputNote: "使用模拟快照中的测点，没有执行诊断算法。",
  })
  state = { ...state, runs: [created, ...state.runs] }
  emit()
  return id
}

export function createImportedRuns(input: {
  fileName: string
  columns: string[]
  rows: string[][]
  equipmentColumn: string | null
  withheldColumns: string[]
  role: DatasetRole
  taskName: string
  strategyId: string
  windowLabel: string
}): string[] {
  const groups = new Map<string, number>()
  if (input.equipmentColumn) {
    const index = input.columns.indexOf(input.equipmentColumn)
    for (const row of input.rows) {
      const key = (row[index] || "").trim() || "未识别设备"
      groups.set(key, (groups.get(key) ?? 0) + 1)
    }
  } else {
    groups.set(input.taskName.trim(), input.rows.length)
  }
  const batch: DatasetBatch = {
    id: nextId("DS"),
    fileName: input.fileName,
    rowCount: input.rows.length,
    equipmentIds: [...groups.keys()],
    datasetRole: input.role,
    uploadedAt: new Date().toISOString(),
    columns: input.columns,
    withheldColumns: input.withheldColumns,
  }
  const createdAt = new Date().toISOString()
  const ids: string[] = []
  const runs = [...groups.entries()].map(([label, count]) => {
    const id = nextId("DR")
    ids.push(id)
    const known = DEMO_LINE.equipment.find((item) => item.code === label || item.name === label || item.id === label)
    return run({
      id,
      name: groups.size > 1 ? `${input.taskName.trim()} · ${label}` : input.taskName.trim(),
      source: "manual",
      equipmentIds: known ? [known.id] : [],
      equipmentLabel: known ? equipmentLabel(known.id) : label,
      datasetId: batch.id,
      createdAt,
      status: "queued",
      strategyId: input.strategyId,
      dataMode: "imported",
      windowLabel: input.windowLabel,
      inputNote:
        input.role === "inference"
          ? `待预测数据 ${count} 行。答案字段 ${input.withheldColumns.length ? input.withheldColumns.join("、") : "无"} 未进入特征。算法未执行。`
          : `${input.role === "training" ? "训练" : "验证"}数据 ${count} 行已登记。算法未执行。`,
    })
  })
  state = { ...state, batches: [batch, ...state.batches], runs: [...runs, ...state.runs] }
  emit()
  return ids
}
