export type DiagnosticTaskSource = "automatic" | "manual" | "event"

export type DiagnosticTaskStatus = "queued" | "running" | "completed" | "failed" | "cancelled"

export type EngineAvailability = "available" | "unavailable" | "not_deployed"

export type EngineExecution = "none" | "demo_running" | "demo_completed" | "demo_failed"

export type DataMode = "live" | "simulated" | "imported"

export type StageId = "l1" | "l2" | "l3" | "l4" | "l5"

export type StageState = "pending" | "waiting" | "running" | "completed" | "failed" | "skipped"

export type TriggerType = "periodic" | "new_data" | "anomaly"

export type DatasetRole = "training" | "validation" | "inference"

export type FailurePolicy = "record" | "retry" | "pause"

export type ReportHandoff = "none" | "pending"

export interface StageSnapshot {
  stage: StageId
  state: StageState
  note: string
}

export interface DiagnosticRun {
  id: string
  name: string
  source: DiagnosticTaskSource
  equipmentIds: string[]
  equipmentLabel: string
  datasetId?: string
  monitoringPlanId?: string
  eventId?: string
  diagnosisId?: string
  createdAt: string
  startedAt?: string
  completedAt?: string
  status: DiagnosticTaskStatus
  currentStage?: StageId
  stages: StageSnapshot[]
  strategyId: string
  dataMode: DataMode
  windowLabel: string
  demo: true
  cycleNote: string
  reportHandoff: ReportHandoff
  inputNote: string
}

export interface EngineRuntime {
  engineId: string
  name: string
  shortName: string
  availability: EngineAvailability
  execution: EngineExecution
  runningTaskCount: number
  lastExecutedAt?: string
  lastResult: string
  demo: true
}

export interface MonitoringPlan {
  id: string
  equipmentId: string
  equipmentLabel: string
  enabled: boolean
  dataSource: string
  triggerType: TriggerType
  periodMinutes?: number
  anomalyRule?: string
  strategyId: string
  failurePolicy: FailurePolicy
  lastRunAt?: string
  lastStatus?: DiagnosticTaskStatus
  demo: true
}

export interface DatasetBatch {
  id: string
  fileName: string
  rowCount: number
  equipmentIds: string[]
  datasetRole: DatasetRole
  uploadedAt: string
  columns: string[]
  withheldColumns: string[]
}

export interface DiagnosticState {
  runs: DiagnosticRun[]
  plans: MonitoringPlan[]
  batches: DatasetBatch[]
}
