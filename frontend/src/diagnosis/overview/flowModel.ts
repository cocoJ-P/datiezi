import type { DiagnosisRoutingSummary, DiagnosisRunSummary, WorkflowResponse } from "../l2/api"
import type { PipelineStage } from "../l1/types"

export type FlowPhase = "l1_only" | "l1_l2" | "l2_routing"

export type L2MainStatus =
  | "未开始"
  | "进行中"
  | "已完成"
  | "待训练"
  | "需要首次配置"
  | "需要处理"
  | "执行失败"

export type FlowTone = "pass" | "busy" | "warn" | "fail" | "idle" | "review"

const L1_PASS = new Set(["completed", "completed_with_warnings", "partial"])

export const L2_RUNNING = new Set([
  "queued",
  "preparing_dataset",
  "matching_model",
  "inference_queued",
  "inference_running",
  "training_queued",
  "training_running",
])

export type EquipmentItem = NonNullable<NonNullable<WorkflowResponse["l2"]>["handoff"]>["equipment_items"] extends
  | Array<infer T>
  | undefined
  ? T
  : never

export function stagePassed(stages: PipelineStage[], stageId: string): boolean {
  const stage = stages.find((item) => item.stage_id === stageId)
  return Boolean(stage && L1_PASS.has(stage.status))
}

export function l1Passed(stages: PipelineStage[], stepIds: string[]): boolean {
  return stepIds.every((id) => stagePassed(stages, id))
}

export function resolveL2Main(status: string | undefined, l1Ok: boolean): L2MainStatus {
  if (!l1Ok) return "未开始"
  if (!status || status === "waiting_for_l1") return "未开始"
  if (status === "completed") return "已完成"
  if (status === "failed") return "执行失败"
  if (status === "training_confirmation_required") return "待训练"
  if (status === "equipment_config_required") return "需要首次配置"
  if (status === "dataset_not_ready" || status === "model_activation_required" || status === "skipped") {
    return "需要处理"
  }
  if (L2_RUNNING.has(status)) return "进行中"
  return "进行中"
}

export function resolveFlowPhase(l1Ok: boolean, l2Main: L2MainStatus): FlowPhase {
  if (!l1Ok) return "l1_only"
  if (l2Main === "已完成") return "l2_routing"
  return "l1_l2"
}

export function toneForL2(main: L2MainStatus): FlowTone {
  switch (main) {
    case "已完成":
      return "pass"
    case "进行中":
      return "busy"
    case "待训练":
    case "需要首次配置":
    case "需要处理":
      return "warn"
    case "执行失败":
      return "fail"
    default:
      return "idle"
  }
}

export function aggregateRouting(
  summaries: DiagnosisRoutingSummary[],
): {
  total: number
  l2Complete: number
  l3Review: number
  groups: DiagnosisRoutingSummary["groups"]
  branches: DiagnosisRoutingSummary["groups"]
} {
  const groups = summaries.flatMap((item) => item.groups || [])
  const total = summaries.reduce((sum, item) => sum + (item.total_records || 0), 0)
  const l2Complete = summaries.reduce((sum, item) => sum + (item.l2_complete_count || 0), 0)
  const l3Review = summaries.reduce((sum, item) => sum + (item.l3_review_count || 0), 0)
  const branches = groups.filter((group) => group.l3_review_count > 0)
  return { total, l2Complete, l3Review, groups, branches }
}

export function progressFromRuns(runSummaries: Record<string, DiagnosisRunSummary>, l2Main: L2MainStatus): string | null {
  const runs = Object.values(runSummaries)
  if (!runs.length || l2Main !== "进行中") return null
  const processed = runs.reduce((sum, run) => sum + (run.processed_records || 0), 0)
  const total = runs.reduce((sum, run) => sum + (run.total_records || 0), 0)
  if (!total) return null
  return `${processed} / ${total}`
}
