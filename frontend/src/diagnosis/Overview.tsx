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
import { DiagnosticFlowCanvas } from "./overview/DiagnosticFlowCanvas"
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
import { saveL3HandoffFocus } from "./overview/l3HandoffFocus"

const L1_STEP_IDS = ["p0", "p1", "p2", "p3", "p4", "p5", "p6", "p7", "p8"]

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
  const [reasonOpen, setReasonOpen] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)

  const l1Ok = l1Passed(stages, L1_STEP_IDS)
  const handoff = workflow?.l2?.handoff
  const l2Status = workflow?.l2?.status || handoff?.status
  const equipmentItems = (handoff?.equipment_items || []) as EquipmentItem[]
  const primaryItem = equipmentItems[0]
  const l2Main = resolveL2Main(l2Status, l1Ok)
  const phase = resolveFlowPhase(l1Ok, l2Main)
  const l2Tone = toneForL2(l2Main)

  useEffect(() => {
    if (!selectedId) {
      setStages([])
      return
    }
    const controller = new AbortController()
    getDataset(selectedId, controller.signal)
      .then((detail) => {
        if (!controller.signal.aborted) setStages(detail.stages ?? [])
      })
      .catch(() => {
        if (!controller.signal.aborted) setStages([])
      })
    return () => controller.abort()
  }, [selectedId, notice])

  useEffect(() => {
    if (!selectedId || !l1Ok) {
      setWorkflow(null)
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
      setRunSummaries({})
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
  }, [equipmentItems])

  useEffect(() => {
    if (l2Main !== "已完成") {
      setRoutingSummaries([])
      setRoutingLoading(false)
      return
    }
    const runIds = equipmentItems
      .filter((item) => item.status === "completed" && item.diagnosis_run_id)
      .map((item) => item.diagnosis_run_id as string)
    if (!runIds.length) {
      setRoutingSummaries([])
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
  }, [equipmentItems, l2Main])

  useEffect(() => {
    if (l2Main !== "已完成" || !routingSummaries.length) {
      setL3Runs({})
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

  function openL3(branch: RoutingGroupSummary, summary?: DiagnosisRoutingSummary) {
    saveL3HandoffFocus({
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
      trigger_reason: "auto_review",
      conflict_count: branch.conflict_count,
      uncertain_count: branch.uncertain_count,
      abnormal_count: branch.abnormal_count,
      l3_review_count: branch.l3_review_count,
      reason: "L2 分流建议进入 L3（ABNORMAL / CONFLICT / UNCERTAIN，auto_review）。",
    })
    onOpenLayer("l3")
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
          上传文件后将自动执行可完成的步骤。流程图按实际阶段展开：L1 数据治理 → L2 筛查 → 需复核对象分流到 L3。
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
      />
    </section>
  )
}
