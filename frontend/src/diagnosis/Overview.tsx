import { useEffect, useMemo, useRef, useState } from "react"
import type { DiagnosisTabId } from "../events/diagnosisNav"
import { L1ApiError, getDataset, runFullL1Pipeline } from "./l1/api"
import { useL1Dataset } from "./l1/datasetContext"
import type { PipelineStage } from "./l1/types"
import {
  getDiagnosisRoutingSummary,
  getDiagnosisRun,
  getDiagnosisWorkflow,
  resumeL2Workflow,
  type DiagnosisRoutingSummary,
  type DiagnosisRunSummary,
  type RoutingGroupSummary,
  type WorkflowResponse,
} from "./l2/api"
import { saveL2HandoffFocus } from "./l2/handoffFocus"
import { DiagnosticFlowCanvas, type DownstreamStageStats, type L5StageStats } from "./overview/DiagnosticFlowCanvas"
import {
  aggregateRouting,
  l1Passed,
  progressFromRuns,
  resolveFlowPhase,
  resolveL2Main,
  toneForL2,
  type EquipmentItem,
} from "./overview/flowModel"
import { getAnalysisRunByDiagnosis, type AnalysisRunSummary } from "./l3/api"
import {
  createL4ReasoningRun,
  createL4Run,
  getL4ByAnalysis,
  getL4ReasoningByL4,
  listL4ReasoningResults,
  listL4Results,
} from "./l4/api"
import {
  createL5Run,
  createSynthesisRun,
  getPredictionStatus,
  listL5Records,
  listL5Runs,
  listSynthesisRuns,
} from "../report/api"
import { saveL3HandoffFocus } from "./overview/l3HandoffFocus"
import { saveL5ReportFocus } from "./overview/l5HandoffFocus"
import { useRoute } from "../routing"

const L1_STEP_IDS = ["p0", "p1", "p2", "p3", "p4", "p5", "p6", "p7", "p8"]

function isL3Done(analysis: AnalysisRunSummary | null | undefined): boolean {
  return analysis?.status === "succeeded" || analysis?.status === "partial"
}

function userMessage(raw: string | null | undefined, fallback: string): string {
  if (!raw) return fallback
  if (raw.includes("NO_ACTIVE_MODEL") || raw.includes("没有兼容") || raw.includes("暂无")) {
    return "当前设备暂无可用诊断模型"
  }
  if (raw.includes("EQUIPMENT_CONFIG") || raw.includes("设备配置") || raw.includes("未确认") || raw.includes("新的设备")) {
    return "检测到新的设备类别，请确认训练特征和故障标签"
  }
  if (raw.includes("DATASET_NOT_READY") || raw.includes("标签") || raw.includes("训练条件")) {
    return "当前数据尚未满足诊断或训练条件"
  }
  if (raw.includes("SCHEMA")) {
    return "当前数据与模型特征结构不兼容"
  }
  return raw
}

export function DiagnosticOverview({ onOpenLayer }: { onOpenLayer: (id: DiagnosisTabId) => void }) {
  const { navigate } = useRoute()
  const { selectedId, filename, status, addFile } = useL1Dataset()
  const [encoding, setEncoding] = useState("auto")
  const [delimiter, setDelimiter] = useState("auto")
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState("")
  const [failed, setFailed] = useState(false)
  const [stages, setStages] = useState<PipelineStage[]>([])
  const [workflow, setWorkflow] = useState<WorkflowResponse | null>(null)
  const [runSummaries, setRunSummaries] = useState<Record<string, DiagnosisRunSummary>>({})
  const [routingSummaries, setRoutingSummaries] = useState<DiagnosisRoutingSummary[]>([])
  const [routingLoading, setRoutingLoading] = useState(false)
  const [l3Runs, setL3Runs] = useState<Record<string, AnalysisRunSummary | null>>({})
  const [l4StatsByBranch, setL4StatsByBranch] = useState<Record<string, DownstreamStageStats>>({})
  const [l5Stats, setL5Stats] = useState<L5StageStats | null>(null)
  const [reasonOpen, setReasonOpen] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)
  /** 已成功触发过的自动步骤，避免轮询重复 POST */
  const autoDoneRef = useRef<Set<string>>(new Set())
  /** 正在进行中的自动步骤 */
  const autoInflightRef = useRef<Set<string>>(new Set())

  const l1Ok = l1Passed(stages, L1_STEP_IDS)
  const handoff = workflow?.l2?.handoff
  const l2Status = workflow?.l2?.status || handoff?.status
  const equipmentItems = useMemo(
    () => (handoff?.equipment_items || []) as EquipmentItem[],
    [handoff?.equipment_items],
  )
  const equipmentKey = useMemo(
    () =>
      equipmentItems
        .map((item) => `${item.equipment_type || ""}:${item.diagnosis_run_id || ""}:${item.status || ""}`)
        .join("|"),
    [equipmentItems],
  )
  const primaryItem = equipmentItems[0]
  const l2Main = resolveL2Main(l2Status, l1Ok)
  const phase = resolveFlowPhase(l1Ok, l2Main)
  const l2Tone = toneForL2(l2Main)

  useEffect(() => {
    if (!selectedId) {
      setStages((prev) => (prev.length ? [] : prev))
      return
    }
    const controller = new AbortController()
    getDataset(selectedId, controller.signal)
      .then((detail) => {
        if (!controller.signal.aborted) setStages(detail.stages ?? [])
      })
      .catch(() => {
        if (!controller.signal.aborted) setStages((prev) => (prev.length ? [] : prev))
      })
    return () => controller.abort()
  }, [selectedId, notice])

  useEffect(() => {
    if (!selectedId || !l1Ok) {
      setWorkflow((prev) => (prev ? null : prev))
      return
    }
    let cancelled = false
    const poll = async () => {
      try {
        let body = await getDiagnosisWorkflow(selectedId)
        const st = body.l2?.status
        if (!body.l2?.handoff || st === "waiting_for_l1") {
          try {
            await resumeL2Workflow(selectedId)
            body = await getDiagnosisWorkflow(selectedId)
          } catch {
            /* 补偿失败时继续展示现有状态 */
          }
        }
        if (cancelled) return
        setWorkflow(body)
      } catch {
        /* 轮询失败不打断总览 */
      }
    }
    void poll()
    const timer = window.setInterval(() => void poll(), 2500)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [selectedId, l1Ok, notice])

  useEffect(() => {
    const runIds = equipmentItems.map((item) => item.diagnosis_run_id).filter(Boolean) as string[]
    if (!runIds.length) {
      setRunSummaries((prev) => (Object.keys(prev).length ? {} : prev))
      return
    }
    let cancelled = false
    Promise.all(
      runIds.map(async (runId) => {
        try {
          return [runId, await getDiagnosisRun(runId)] as const
        } catch {
          return null
        }
      }),
    ).then((rows) => {
      if (cancelled) return
      const next: Record<string, DiagnosisRunSummary> = {}
      for (const row of rows) {
        if (row) next[row[0]] = row[1]
      }
      setRunSummaries(next)
    })
    return () => {
      cancelled = true
    }
  }, [equipmentKey, equipmentItems])

  useEffect(() => {
    if (l2Main !== "已完成") {
      setRoutingSummaries((prev) => (prev.length ? [] : prev))
      setRoutingLoading(false)
      return
    }
    const runIds = equipmentItems
      .filter((item) => item.status === "completed" && item.diagnosis_run_id)
      .map((item) => item.diagnosis_run_id as string)
    if (!runIds.length) {
      setRoutingSummaries((prev) => (prev.length ? [] : prev))
      return
    }
    let cancelled = false
    setRoutingLoading(true)
    Promise.all(
      runIds.map(async (runId) => {
        try {
          return await getDiagnosisRoutingSummary(runId)
        } catch {
          return null
        }
      }),
    ).then((rows) => {
      if (cancelled) return
      setRoutingSummaries(rows.filter(Boolean) as DiagnosisRoutingSummary[])
      setRoutingLoading(false)
    })
    return () => {
      cancelled = true
    }
  }, [equipmentKey, equipmentItems, l2Main])

  useEffect(() => {
    if (l2Main !== "已完成" || !routingSummaries.length) {
      setL3Runs((prev) => (Object.keys(prev).length ? {} : prev))
      return
    }
    const runIds = routingSummaries.map((item) => item.diagnosis_run_id)
    let cancelled = false
    async function pollL3() {
      const next: Record<string, AnalysisRunSummary | null> = {}
      await Promise.all(
        runIds.map(async (runId) => {
          try {
            const body = await getAnalysisRunByDiagnosis(runId)
            next[runId] = body.analysis_run
          } catch {
            next[runId] = null
          }
        }),
      )
      if (!cancelled) setL3Runs(next)
    }
    void pollL3()
    const timer = window.setInterval(() => void pollL3(), 2500)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [routingSummaries, l2Main])

  const analysisIdKey = useMemo(
    () =>
      Object.values(l3Runs)
        .map((run) => run?.analysis_run_id)
        .filter(Boolean)
        .sort()
        .join("|"),
    [l3Runs],
  )
  const diagnosisIdKey = useMemo(
    () =>
      equipmentItems
        .map((item) => item.diagnosis_run_id)
        .filter(Boolean)
        .sort()
        .join("|"),
    [equipmentItems],
  )

  const branchFocusKey = useMemo(() => {
    return routingSummaries
      .flatMap((summary) =>
        (summary.groups || [])
          .filter((g) => g.l3_review_count > 0)
          .map((g) => `${g.group_key}@${summary.diagnosis_run_id}`),
      )
      .sort()
      .join("|")
  }, [routingSummaries])

  useEffect(() => {
    if (l2Main !== "已完成") {
      setL4StatsByBranch((prev) => (Object.keys(prev).length ? {} : prev))
      setL5Stats((prev) => (prev ? null : prev))
      return
    }
    let cancelled = false
    const analysisIds = analysisIdKey ? analysisIdKey.split("|") : []
    const diagnosisIds = new Set(diagnosisIdKey ? diagnosisIdKey.split("|") : [])
    const branchPairs = routingSummaries.flatMap((summary) =>
      (summary.groups || [])
        .filter((g) => g.l3_review_count > 0)
        .map((branch) => ({ branch, summary })),
    )

    async function loadDownstream() {
      type L4Cache = {
        l4RunId: string
        items: Array<{
          record_id: string
          equipment_id?: string
          assessment?: { root_cause_candidates?: Array<Record<string, unknown>>; root_cause_confirmation?: string }
        }>
        reasoningDone: number
      }
      const cache = new Map<string, L4Cache | null>()
      const autoBusyL4 = new Set<string>()
      const autoFailedL4 = new Map<string, string>()

      async function loadL4ForAnalysis(arId: string): Promise<L4Cache | null> {
        if (cache.has(arId)) return cache.get(arId) || null
        try {
          const body = await getL4ByAnalysis(arId)
          if (!body.l4_run) {
            cache.set(arId, null)
            return null
          }
          const results = await listL4Results(body.l4_run.l4_run_id)
          let reasoningDone = 0
          try {
            const rr = await getL4ReasoningByL4(body.l4_run.l4_run_id)
            if (rr.reasoning_run) {
              const rrItems = await listL4ReasoningResults(rr.reasoning_run.reasoning_run_id)
              reasoningDone = rrItems.total || rrItems.items.length
            }
          } catch {
            /* optional */
          }
          const packed: L4Cache = {
            l4RunId: body.l4_run.l4_run_id,
            items: results.items || [],
            reasoningDone,
          }
          cache.set(arId, packed)
          return packed
        } catch {
          cache.set(arId, null)
          return null
        }
      }

      // 按 AnalysisRun 聚合记录，供自动创建 L4
      const recordsByAnalysis = new Map<string, string[]>()
      for (const { branch, summary } of branchPairs) {
        const analysis = l3Runs[summary.diagnosis_run_id]
        const arId = analysis?.analysis_run_id
        if (!arId || !isL3Done(analysis)) continue
        const prev = recordsByAnalysis.get(arId) || []
        for (const rid of branch.l3_record_ids || []) {
          if (!prev.includes(rid)) prev.push(rid)
        }
        recordsByAnalysis.set(arId, prev)
      }

      // L3 完成后自动创建 L4（及 L4 Reasoning）
      for (const [arId, recordIds] of recordsByAnalysis) {
        let packed = await loadL4ForAnalysis(arId)
        if (packed) {
          const reasonKey = `l4r:${packed.l4RunId}`
          if (!packed.reasoningDone && !autoDoneRef.current.has(reasonKey) && !autoInflightRef.current.has(reasonKey)) {
            autoInflightRef.current.add(reasonKey)
            try {
              await createL4ReasoningRun({
                l4_run_id: packed.l4RunId,
                force_new: false,
                max_records: 5,
                record_ids: recordIds.slice(0, 5),
              })
              autoDoneRef.current.add(reasonKey)
              cache.delete(arId)
              packed = await loadL4ForAnalysis(arId)
            } catch {
              /* 推理失败可下次重试 */
            } finally {
              autoInflightRef.current.delete(reasonKey)
            }
          }
          continue
        }
        const l4Key = `l4:${arId}`
        if (autoInflightRef.current.has(l4Key)) {
          autoBusyL4.add(arId)
          continue
        }
        autoInflightRef.current.add(l4Key)
        autoBusyL4.add(arId)
        try {
          const created = await createL4Run({
            analysis_run_id: arId,
            force_new: false,
            max_records: 20,
            record_ids: recordIds.length ? recordIds.slice(0, 20) : undefined,
          })
          cache.delete(arId)
          const after = await loadL4ForAnalysis(arId)
          if (after?.l4RunId || created.l4_run?.l4_run_id) {
            const l4RunId = after?.l4RunId || created.l4_run.l4_run_id
            const reasonKey = `l4r:${l4RunId}`
            if (!autoDoneRef.current.has(reasonKey) && !autoInflightRef.current.has(reasonKey)) {
              autoInflightRef.current.add(reasonKey)
              try {
                await createL4ReasoningRun({
                  l4_run_id: l4RunId,
                  force_new: false,
                  max_records: 5,
                  record_ids: recordIds.slice(0, 5),
                })
                autoDoneRef.current.add(reasonKey)
                cache.delete(arId)
                await loadL4ForAnalysis(arId)
              } catch {
                /* optional */
              } finally {
                autoInflightRef.current.delete(reasonKey)
              }
            }
          }
          autoBusyL4.delete(arId)
        } catch (exc) {
          autoBusyL4.delete(arId)
          autoFailedL4.set(arId, exc instanceof Error ? exc.message : String(exc))
        } finally {
          autoInflightRef.current.delete(l4Key)
        }
        if (cancelled) return
      }

      const nextBranch: Record<string, DownstreamStageStats> = {}
      const l4ByDiagnosis = new Map<string, { analysisRunId: string; l4RunId: string; recordIds: string[] }>()

      for (const { branch, summary } of branchPairs) {
        const analysis = l3Runs[summary.diagnosis_run_id]
        const arId = analysis?.analysis_run_id
        if (!arId) {
          nextBranch[branch.group_key] = {
            statusLabel: analysis ? "等待 L3 完成" : "待分析",
            tone: "idle",
            analyzedRecords: 0,
            candidateCount: 0,
            reasoningDone: 0,
            insufficientOrConflict: 0,
            l4RunId: null,
            knowledgeNote: "L3 AnalysisRun 就绪并完成后将自动启动 L4",
          }
          continue
        }
        if (!isL3Done(analysis)) {
          nextBranch[branch.group_key] = {
            statusLabel: "等待 L3 完成",
            tone: "busy",
            analyzedRecords: 0,
            candidateCount: 0,
            reasoningDone: 0,
            insufficientOrConflict: 0,
            l4RunId: null,
            knowledgeNote: `L3 状态 ${analysis?.status || "unknown"} · 完成后自动执行 L4`,
          }
          continue
        }
        if (autoBusyL4.has(arId)) {
          nextBranch[branch.group_key] = {
            statusLabel: "L4 自动执行中",
            tone: "busy",
            analyzedRecords: 0,
            candidateCount: 0,
            reasoningDone: 0,
            insufficientOrConflict: 0,
            l4RunId: null,
            knowledgeNote: "L3 已完成，正在自动创建 L4Run…",
          }
          continue
        }
        if (autoFailedL4.has(arId)) {
          nextBranch[branch.group_key] = {
            statusLabel: "L4 自动失败",
            tone: "fail",
            analyzedRecords: 0,
            candidateCount: 0,
            reasoningDone: 0,
            insufficientOrConflict: 0,
            l4RunId: null,
            knowledgeNote: autoFailedL4.get(arId) || "自动创建 L4 失败，可进入 L4 手动重试",
          }
          continue
        }
        const packed = await loadL4ForAnalysis(arId)
        if (!packed) {
          nextBranch[branch.group_key] = {
            statusLabel: "L4 待自动执行",
            tone: "idle",
            analyzedRecords: 0,
            candidateCount: 0,
            reasoningDone: 0,
            insufficientOrConflict: 0,
            l4RunId: null,
            knowledgeNote: "即将自动创建 L4Run",
          }
          continue
        }
        const idSet = new Set(branch.l3_record_ids || [])
        let items = packed.items
        if (idSet.size) {
          const filtered = items.filter((it) => idSet.has(it.record_id))
          if (filtered.length) items = filtered
          else if (branch.equipment_id) {
            const byEquip = packed.items.filter((it) => String(it.equipment_id || "") === String(branch.equipment_id))
            if (byEquip.length) items = byEquip
          }
        } else if (branch.equipment_id) {
          const byEquip = packed.items.filter((it) => String(it.equipment_id || "") === String(branch.equipment_id))
          if (byEquip.length) items = byEquip
        }
        let candidates = 0
        let insufficient = 0
        for (const item of items) {
          const cands = item.assessment?.root_cause_candidates || []
          candidates += cands.length
          const conf = String(item.assessment?.root_cause_confirmation || "unconfirmed")
          if (conf !== "confirmed") insufficient += 1
          for (const c of cands) {
            const status = String((c as Record<string, unknown>).assessment_status || "")
            if (status.includes("insufficient") || status.includes("conflict")) insufficient += 1
          }
        }
        nextBranch[branch.group_key] = {
          statusLabel: "已有 L4Run",
          tone: "pass",
          analyzedRecords: items.length,
          candidateCount: candidates,
          reasoningDone: packed.reasoningDone,
          insufficientOrConflict: insufficient,
          l4RunId: packed.l4RunId,
          knowledgeNote: `${branch.display_name} · 已自动衔接 L4`,
        }
        const prev = l4ByDiagnosis.get(summary.diagnosis_run_id)
        const recs = [...(branch.l3_record_ids || [])]
        if (prev) {
          for (const rid of recs) if (!prev.recordIds.includes(rid)) prev.recordIds.push(rid)
        } else {
          l4ByDiagnosis.set(summary.diagnosis_run_id, {
            analysisRunId: arId,
            l4RunId: packed.l4RunId,
            recordIds: recs,
          })
        }
      }
      if (!cancelled) setL4StatsByBranch(nextBranch)

      try {
        let [pred, l5page, syn] = await Promise.all([
          getPredictionStatus().catch(() => ({ model_id: "", equipment_categories: [] as string[], n_models: 0 })),
          listL5Runs(50).catch(() => ({ total: 0, items: [] as Array<{
            l5_run_id: string
            diagnosis_run_id?: string
            analysis_run_id?: string
            l4_run_id?: string
            selected_record_count?: number
          }> })),
          listSynthesisRuns(50).catch(() => ({ total: 0, items: [] })),
        ])
        if (cancelled) return

        // L4 完成后自动创建 L5（及 Synthesis）
        let autoL5Busy = false
        let autoL5Note = ""
        for (const [diagnosisRunId, binding] of l4ByDiagnosis) {
          const existing = (l5page.items || []).find(
            (run) =>
              run.diagnosis_run_id === diagnosisRunId ||
              run.analysis_run_id === binding.analysisRunId ||
              run.l4_run_id === binding.l4RunId,
          )
          if (existing) {
            const synKey = `syn:${existing.l5_run_id}`
            if (!autoDoneRef.current.has(synKey) && !autoInflightRef.current.has(synKey)) {
              autoInflightRef.current.add(synKey)
              try {
                await createSynthesisRun({
                  l5_run_id: existing.l5_run_id,
                  force_new: false,
                  max_records: 5,
                  record_ids: binding.recordIds.slice(0, 5),
                  allow_llm_synthesis: false,
                })
                autoDoneRef.current.add(synKey)
              } catch {
                /* synthesis 可下次重试 */
              } finally {
                autoInflightRef.current.delete(synKey)
              }
            }
            continue
          }
          const l5Key = `l5:${diagnosisRunId}`
          if (autoInflightRef.current.has(l5Key)) {
            autoL5Busy = true
            autoL5Note = "L5 自动执行中…"
            continue
          }
          autoInflightRef.current.add(l5Key)
          autoL5Busy = true
          autoL5Note = "L4 已完成，正在自动创建 L5…"
          try {
            const created = await createL5Run({
              diagnosis_run_id: diagnosisRunId,
              analysis_run_id: binding.analysisRunId,
              l4_run_id: binding.l4RunId,
              force_new: false,
              max_records: 20,
              record_ids: binding.recordIds.length ? binding.recordIds.slice(0, 20) : undefined,
            })
            const l5RunId = created.l5_run?.l5_run_id
            if (l5RunId) {
              const synKey = `syn:${l5RunId}`
              if (!autoDoneRef.current.has(synKey) && !autoInflightRef.current.has(synKey)) {
                autoInflightRef.current.add(synKey)
                try {
                  await createSynthesisRun({
                    l5_run_id: l5RunId,
                    force_new: false,
                    max_records: 5,
                    record_ids: binding.recordIds.slice(0, 5),
                    allow_llm_synthesis: false,
                  })
                  autoDoneRef.current.add(synKey)
                } catch {
                  /* optional */
                } finally {
                  autoInflightRef.current.delete(synKey)
                }
              }
            }
            l5page = await listL5Runs(50).catch(() => l5page)
            syn = await listSynthesisRuns(50).catch(() => syn)
            autoL5Busy = false
            autoL5Note = ""
          } catch (exc) {
            autoL5Busy = false
            autoL5Note = exc instanceof Error ? `L5 自动失败：${exc.message}` : "L5 自动失败"
          } finally {
            autoInflightRef.current.delete(l5Key)
          }
          if (cancelled) return
        }

        const relevant = (l5page.items || []).filter(
          (run) =>
            (run.diagnosis_run_id && diagnosisIds.has(run.diagnosis_run_id)) ||
            (run.analysis_run_id && analysisIds.includes(run.analysis_run_id)) ||
            [...l4ByDiagnosis.values()].some((b) => b.l4RunId && run.l4_run_id === b.l4RunId),
        )
        const useRuns = relevant.length ? relevant : l5page.items || []
        let recordCount = useRuns.reduce((s, r) => s + (r.selected_record_count || 0), 0)
        let primaryL5: string | null = useRuns[0]?.l5_run_id || null
        let primaryRecord: string | null = null
        if (primaryL5) {
          try {
            const recs = await listL5Records(primaryL5, 20)
            if (!cancelled) {
              recordCount = recs.total || recs.items.length
              primaryRecord = recs.items[0]?.record_id || null
            }
          } catch {
            /* 用 run 摘要计数即可 */
          }
        }
        if (cancelled) return
        const cats = pred.equipment_categories || []
        const modelReady = Boolean(pred.model_id)
        const hasBound = relevant.length > 0
        setL5Stats({
          statusLabel: hasBound
            ? "已有报告"
            : autoL5Busy
              ? "L5 自动执行中"
              : autoL5Note.startsWith("L5 自动失败")
                ? "L5 自动失败"
                : l4ByDiagnosis.size
                  ? "L5 待自动执行"
                  : modelReady
                    ? "等待上游 L4"
                    : "未就绪",
          tone: hasBound ? "pass" : autoL5Busy ? "busy" : autoL5Note.startsWith("L5 自动失败") ? "fail" : "idle",
          modelId: pred.model_id || "L5P-v1",
          equipmentCategories: cats,
          reportCount: useRuns.length,
          reportRecords: recordCount,
          synthesisCompleted: syn.total || 0,
          predictionCoverageNote: pred.n_models
            ? `${pred.n_models} 预测头 · 覆盖 ${cats.length || 6} 类设备`
            : "六类设备预测能力以模型包为准",
          knowledgeNote: autoL5Note
            || (hasBound
              ? "L4 完成后已自动衔接综合诊断"
              : l4ByDiagnosis.size
                ? "检测到已完成的 L4，准备自动创建 L5"
                : "等待各分支 L4 完成后自动执行 L5"),
          primaryL5RunId: primaryL5,
          primaryRecordId: primaryRecord,
        })
      } catch (exc) {
        if (!cancelled) {
          setL5Stats({
            statusLabel: "加载失败",
            tone: "fail",
            modelId: "L5P-v1",
            equipmentCategories: [],
            reportCount: 0,
            reportRecords: 0,
            synthesisCompleted: 0,
            predictionCoverageNote: "无法读取 /l5 API（请确认开发代理已转发 /l5）",
            knowledgeNote: exc instanceof Error ? exc.message : "请稍后刷新",
          })
        }
      }
    }
    void loadDownstream()
    return () => {
      cancelled = true
    }
  }, [l2Main, analysisIdKey, diagnosisIdKey, branchFocusKey, l3Runs, routingSummaries])

  const progressText = useMemo(() => progressFromRuns(runSummaries, l2Main), [runSummaries, l2Main])
  const routing = useMemo(() => (routingSummaries.length ? aggregateRouting(routingSummaries) : null), [routingSummaries])

  const multiHint = useMemo(() => {
    if (equipmentItems.length <= 1) return null
    const done = equipmentItems.filter((item) => item.status === "completed").length
    const pendingTrain = equipmentItems.filter((item) => item.status === "training_confirmation_required").length
    return `${done}/${equipmentItems.length} 类设备已处理${pendingTrain ? `，${pendingTrain} 类等待训练` : ""}`
  }, [equipmentItems])

  const reasonText = useMemo(() => {
    const parts = [handoff?.error_message, primaryItem?.message].filter(Boolean) as string[]
    if (!parts.length) return "暂无更多说明。"
    return [...new Set(parts.map((item) => userMessage(item, item)))].join("；")
  }, [handoff?.error_message, primaryItem?.message])

  async function onFile(file: File | undefined) {
    if (!file) return
    setBusy(true)
    setFailed(false)
    setNotice("正在上传文件…")
    setWorkflow(null)
    setRunSummaries({})
    setRoutingSummaries([])
    setReasonOpen(false)
    try {
      const nextId = await addFile(file, { encoding, delimiter })
      const afterUpload = await getDataset(nextId)
      setStages(afterUpload.stages ?? [])
      setNotice("上传完成，正在自动执行 L1…")
      const { prepare } = await runFullL1Pipeline(
        nextId,
        setNotice,
        (detail) => setStages(detail.stages ?? []),
      )
      const detail = await getDataset(nextId)
      setStages(detail.stages ?? [])
      const ready = l1Passed(detail.stages ?? [], L1_STEP_IDS)
      setFailed(!ready)
      if (ready) {
        setNotice(`L1 已完成（${prepare.version_id || "—"}）。正在自动衔接 L2…`)
        try {
          await resumeL2Workflow(nextId)
          setWorkflow(await getDiagnosisWorkflow(nextId))
        } catch {
          /* Worker 会继续处理已入队任务 */
        }
      } else {
        setNotice(`L1 未全部通过（${prepare.status}）。可进入 L1 查看步骤。`)
      }
    } catch (error: unknown) {
      setFailed(true)
      setNotice(error instanceof L1ApiError || error instanceof Error ? error.message : "文件处理没有完成。")
    } finally {
      setBusy(false)
      if (fileRef.current) fileRef.current.value = ""
    }
  }

  function focusBase() {
    const run = primaryItem?.diagnosis_run_id ? runSummaries[primaryItem.diagnosis_run_id] : undefined
    return {
      dataset_id: selectedId || undefined,
      equipment_type: primaryItem?.equipment_type || undefined,
      ped_id: primaryItem?.ped_id || undefined,
      prepared_dataset_id: primaryItem?.ped_id || undefined,
      dataset_revision_id: run?.dataset_revision_id || primaryItem?.ped_revision_id || undefined,
      diagnosis_run_id: primaryItem?.diagnosis_run_id || undefined,
    }
  }

  function openResults() {
    saveL2HandoffFocus({ ...focusBase(), view: "diagnosis" })
    onOpenLayer("l2")
  }

  function openTraining() {
    saveL2HandoffFocus({
      ...focusBase(),
      view: "training",
      reason: userMessage(primaryItem?.message, "当前设备暂无可用诊断模型"),
    })
    onOpenLayer("l2")
  }

  function openConfigure() {
    const details = (primaryItem?.details || {}) as {
      onboarding_id?: string
      onboarding?: { display_name_candidate?: string }
    }
    saveL2HandoffFocus({
      ...focusBase(),
      view: "training",
      onboarding_id: details.onboarding_id,
      pd_revision_id: workflow?.l1?.pd_version_id || handoff?.pd_version_id || undefined,
      handoff_id: handoff?.handoff_id,
      handoff_item_id: primaryItem?.item_id,
      equipment_category: primaryItem?.equipment_category || undefined,
      reason: "检测到新的设备类别，请确认训练特征和故障标签。",
    })
    onOpenLayer("l2")
  }

  function retryL2() {
    if (!selectedId) return
    void resumeL2Workflow(selectedId)
      .then(async () => setWorkflow(await getDiagnosisWorkflow(selectedId)))
      .catch(() => setNotice("L2 重试未能启动，请稍后刷新。"))
  }

  function focusFromBranch(branch: RoutingGroupSummary, summary?: DiagnosisRoutingSummary) {
    return {
      contract_version: "l2_l3_handoff_v1",
      equipment_type: branch.equipment_type,
      equipment_id: branch.equipment_id,
      equipment_group_id: branch.equipment_group_id,
      display_name: branch.display_name,
      record_ids: branch.l3_record_ids,
      prepared_dataset_id: summary?.prepared_dataset_id,
      dataset_revision_id: summary?.dataset_revision_id,
      model_bundle_id: summary?.model_bundle_id,
      diagnosis_run_id: summary?.diagnosis_run_id,
      trigger_reason: "auto_review" as const,
      conflict_count: branch.conflict_count,
      uncertain_count: branch.uncertain_count,
      abnormal_count: branch.abnormal_count,
      l3_review_count: branch.l3_review_count,
      reason: "L2 分流建议进入 L3（ABNORMAL / CONFLICT / UNCERTAIN，auto_review）。",
    }
  }

  function openL3(branch: RoutingGroupSummary, summary?: DiagnosisRoutingSummary) {
    saveL3HandoffFocus(focusFromBranch(branch, summary))
    onOpenLayer("l3")
  }

  function openL4(branch?: RoutingGroupSummary, summary?: DiagnosisRoutingSummary) {
    if (branch) saveL3HandoffFocus(focusFromBranch(branch, summary))
    else if (routing?.branches[0]) {
      const b = routing.branches[0]
      const parent = routingSummaries.find((s) => s.equipment_type === b.equipment_type)
      saveL3HandoffFocus(focusFromBranch(b, parent))
    }
    onOpenLayer("l4")
  }

  function openL5() {
    if (routing?.branches[0]) {
      const b = routing.branches[0]
      const parent = routingSummaries.find((s) => s.equipment_type === b.equipment_type)
      saveL3HandoffFocus(focusFromBranch(b, parent))
    }
    if (l5Stats?.primaryL5RunId) {
      saveL5ReportFocus({
        l5_run_id: l5Stats.primaryL5RunId,
        record_id: l5Stats.primaryRecordId || undefined,
        reason: "from_overview",
      })
    }
    onOpenLayer("l5")
  }

  function openL5Report() {
    if (l5Stats?.primaryL5RunId) {
      saveL5ReportFocus({
        l5_run_id: l5Stats.primaryL5RunId,
        record_id: l5Stats.primaryRecordId || undefined,
        reason: "from_overview_report",
      })
      navigate({ page: "report", runId: l5Stats.primaryL5RunId })
      return
    }
    navigate({ page: "report" })
  }

  return (
    <section className="dx-control-overview" aria-label="诊断与控制总览">
      <header className="dx-control-head">
        <div>
          <h3>诊断与控制总览</h3>
          <p className="dx-control-meta">
            当前数据集：{filename || "尚未添加"}
            {status ? ` · ${status}` : ""}
          </p>
        </div>
        <div className="dx-control-upload">
          <select aria-label="文件编码" value={encoding} disabled={busy} onChange={(event) => setEncoding(event.target.value)}>
            <option value="auto">编码自动</option>
            <option value="utf-8">UTF-8</option>
            <option value="utf-8-sig">UTF-8 BOM</option>
            <option value="gb18030">GB18030</option>
            <option value="gbk">GBK</option>
          </select>
          <select aria-label="分隔符" value={delimiter} disabled={busy} onChange={(event) => setDelimiter(event.target.value)}>
            <option value="auto">分隔符自动</option>
            <option value="comma">逗号</option>
            <option value="tab">制表符</option>
            <option value="semicolon">分号</option>
            <option value="pipe">竖线</option>
          </select>
          <button type="button" className="dx-primary" disabled={busy} onClick={() => fileRef.current?.click()}>
            {busy ? "处理中…" : "添加新文件"}
          </button>
          <input
            ref={fileRef}
            type="file"
            accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            hidden
            aria-label="添加新文件"
            disabled={busy}
            onChange={(event) => void onFile(event.target.files?.[0])}
          />
        </div>
      </header>
      {notice ? (
        <p className={`dx-control-note${failed ? " is-alert" : ""}`} role="status">
          {notice}
        </p>
      ) : (
        <p className="dx-control-note" role="status">
          上传文件后将自动执行可完成的步骤。流程图：L1 → L2 → L3；L3 完成后自动执行 L4；L4 完成后自动执行 L5。
        </p>
      )}

      <DiagnosticFlowCanvas
        phase={phase}
        stages={stages}
        l1Ok={l1Ok}
        l1Busy={busy || Boolean(stages.length && !l1Ok && !failed)}
        l2Main={l2Main}
        l2Tone={l2Tone}
        progressText={progressText}
        multiHint={multiHint}
        routing={routing}
        routingLoading={routingLoading}
        summaries={routingSummaries}
        l3Runs={l3Runs}
        l4StatsByBranch={l4StatsByBranch}
        l5Stats={l5Stats}
        reasonText={reasonText}
        reasonOpen={reasonOpen}
        actions={{
          showViewResults: l2Main === "已完成",
          showGoTrain: l2Main === "待训练" || l2Status === "model_activation_required",
          showConfigure: l2Main === "需要首次配置",
          showViewReason:
            l2Main === "需要处理" || l2Main === "执行失败" || l2Main === "待训练" || l2Main === "需要首次配置",
          showRetry: l2Main === "执行失败",
          showEnterL2: l2Main === "需要处理" && l2Status !== "model_activation_required",
          showEnterL1: !l1Ok && stages.length > 0,
        }}
        onOpenResults={openResults}
        onOpenTraining={openTraining}
        onOpenConfigure={openConfigure}
        onOpenReason={() => setReasonOpen(true)}
        onRetry={retryL2}
        onEnterL2={() => onOpenLayer("l2")}
        onEnterL1={() => onOpenLayer("l1")}
        onEnterL3={openL3}
        onEnterL4={openL4}
        onEnterL5={openL5}
        onOpenL5Report={openL5Report}
      />
    </section>
  )
}
