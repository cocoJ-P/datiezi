import type { DiagnosisResult, ProbabilityMap } from "./types"

export function formatPercent(value: number | undefined | null): string {
  if (value == null || Number.isNaN(value)) return "—"
  const pct = value * 100
  if (pct > 0 && pct < 0.01) return "<0.01%"
  return `${pct.toFixed(2)}%`
}

export function sortedProbabilities(map: ProbabilityMap | undefined | null): { label: string; value: number }[] {
  if (!map) return []
  return Object.entries(map)
    .map(([label, value]) => ({ label, value: Number(value) }))
    .sort((a, b) => b.value - a.value)
}

export function decisionTone(result?: DiagnosisResult | null): "ok" | "warn" | "danger" | "muted" {
  const code = result?.decision?.screening_result || result?.decision?.status || ""
  if (code === "NORMAL" || code === "l2_complete") return "ok"
  if (code === "CONFLICT") return "danger"
  if (code === "UNCERTAIN") return "warn"
  if (code === "ABNORMAL") return "warn"
  return "muted"
}

export function decisionTitle(result?: DiagnosisResult | null): string {
  const screening = result?.decision?.screening_result
  if (screening === "NORMAL") return "L2 初判正常"
  if (screening === "ABNORMAL") return "L2 初判异常"
  if (screening === "CONFLICT") return "模型判断冲突"
  if (screening === "UNCERTAIN") return "诊断不确定"
  return result?.decision?.status || "尚未诊断"
}

export function screeningActionLabel(result?: DiagnosisResult | null): string {
  const screening = result?.decision?.screening_result
  if (screening === "NORMAL") return "筛查完成"
  if (screening === "ABNORMAL" || screening === "CONFLICT" || screening === "UNCERTAIN") return "建议 L3 复核"
  if (result?.decision?.route === "l2_complete") return "L2 筛查已完成"
  if (result?.decision?.route === "l3_review_recommended") return "建议 L3 复核"
  return "—"
}

export function calibrationHint(status: string | null | undefined): string | null {
  if (!status) return null
  if (status === "unverified") return "该分类器的概率校准尚未验证，分数仅供模型判断参考。"
  if (status === "partial") return "概率校准仅部分完成，解释时请结合双模型结果。"
  if (status === "applied") return null
  return `校准状态：${status}`
}

export function sourceTypeLabel(sourceType: string | undefined | null): string {
  if (sourceType === "l1_prepared") return "L1 PreparedDataset"
  if (sourceType === "legacy_csv") return "Legacy CSV"
  return sourceType || "未知来源"
}

export function jobStatusLabel(status: string): string {
  const map: Record<string, string> = {
    queued: "排队中",
    running: "训练中",
    succeeded: "已完成",
    failed: "失败",
    interrupted: "已中断",
  }
  return map[status] || status
}

export function trainModeLabel(mode: string | undefined | null): string {
  if (mode === "retrain") return "再训练"
  return "新增"
}

export function modelStatusLabel(status: string): string {
  const map: Record<string, string> = {
    active: "Active",
    candidate: "Candidate",
    archived: "Archived",
  }
  return map[status] || status
}

export function metricValue(value: number | null | undefined, digits = 4): string {
  if (value == null || Number.isNaN(value)) return "暂无数据"
  return value.toFixed(digits)
}
