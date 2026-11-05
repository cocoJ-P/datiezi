import { useEffect, useMemo, useRef, useState } from "react"
import { getTrainJob, getTrainJobLogs, listDatasets, listEquipmentTypes, listTrainJobs, mapL2Error, submitTrainJob } from "./api"
import { jobStatusLabel, sourceTypeLabel, trainModeLabel } from "./format"
import type { L2HandoffFocus } from "./handoffFocus"
import { OnboardingPanel } from "./OnboardingPanel"
import type { DatasetSummary, EquipmentTypeSummary, TrainingJob, TrainingJobLogs } from "./types"

type Props = {
  initialFocus?: L2HandoffFocus | null
  focusJobId?: string | null
  onFocusJobConsumed?: () => void
  onOpenModels: () => void
}

const ACTIVE_STATUSES = new Set(["queued", "running"])

function jobTrainMode(job: TrainingJob): string {
  return job.train_mode === "retrain" ? "retrain" : "new"
}

export function TrainingPanel({
  initialFocus = null,
  focusJobId = null,
  onFocusJobConsumed,
  onOpenModels,
}: Props) {
  const [equipment, setEquipment] = useState<EquipmentTypeSummary[]>([])
  const [datasets, setDatasets] = useState<DatasetSummary[]>([])
  const [equipmentType, setEquipmentType] = useState("")
  const [datasetId, setDatasetId] = useState("")
  const [jobs, setJobs] = useState<TrainingJob[]>([])
  const [selectedJobId, setSelectedJobId] = useState("")
  const [selectedJob, setSelectedJob] = useState<TrainingJob | null>(null)
  const [logs, setLogs] = useState<TrainingJobLogs | null>(null)
  const [logsOpen, setLogsOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const [notice, setNotice] = useState("")
  const [showOnboarding, setShowOnboarding] = useState(() =>
    Boolean(initialFocus?.onboarding_id || (initialFocus?.reason && initialFocus.reason.includes("新的设备"))),
  )
  const focusApplied = useRef(false)
  const selectedJobIdRef = useRef(selectedJobId)
  selectedJobIdRef.current = selectedJobId

  const equipmentDatasets = datasets.filter((item) => item.equipment_type === equipmentType)
  const selectedDataset = equipmentDatasets.find(
    (item) => item.dataset_id === datasetId || item.prepared_dataset_id === datasetId,
  )
  const selectedEquipment = equipment.find((item) => item.equipment_type === equipmentType)
  const hasActiveJobs = useMemo(() => jobs.some((job) => ACTIVE_STATUSES.has(job.status)), [jobs])

  useEffect(() => {
    Promise.all([listEquipmentTypes(), listDatasets(), listTrainJobs({ limit: 30 })])
      .then(([eq, ds, jobBody]) => {
        setEquipment(eq)
        setDatasets(ds)
        setJobs(jobBody.jobs)
        if (initialFocus?.equipment_type && eq.some((item) => item.equipment_type === initialFocus.equipment_type)) {
          setEquipmentType(initialFocus.equipment_type)
          focusApplied.current = true
          const ped = initialFocus.prepared_dataset_id || initialFocus.ped_id
          if (ped) setDatasetId(ped)
          if (initialFocus.reason) setNotice(`来自总览：${initialFocus.reason}`)
        } else {
          const preferredEq = eq.find((item) => item.equipment_type === "high_pressure_descaling_pump") || eq[0]
          if (preferredEq) setEquipmentType(preferredEq.equipment_type)
        }
      })
      .catch((err) => setError(mapL2Error(err)))
  }, [initialFocus])

  useEffect(() => {
    const next = datasets.filter((item) => item.equipment_type === equipmentType)
    if (focusApplied.current) {
      const ped = initialFocus?.prepared_dataset_id || initialFocus?.ped_id
      const hit = next.find((item) => item.dataset_id === ped || item.prepared_dataset_id === ped)
      if (hit) {
        setDatasetId(hit.dataset_id)
        focusApplied.current = false
        return
      }
      focusApplied.current = false
    }
    const preferred = next.find((item) => item.training_ready) || next[0]
    setDatasetId(preferred?.dataset_id || "")
  }, [equipmentType, datasets, initialFocus])

  useEffect(() => {
    if (!focusJobId) return
    let cancelled = false
    const targetJobId = focusJobId
    void listTrainJobs({ limit: 30 })
      .then(async (jobBody) => {
        if (cancelled) return
        setJobs(jobBody.jobs)
        setSelectedJobId(targetJobId)
        const job = await getTrainJob(targetJobId)
        if (cancelled) return
        setSelectedJob(job)
        setNotice(`已定位到任务 ${targetJobId}。`)
        onFocusJobConsumed?.()
      })
      .catch((err) => {
        if (!cancelled) setError(mapL2Error(err))
      })
    return () => {
      cancelled = true
    }
    // 仅在 focusJobId 变化时定位；回调由父组件在定位后清空 focus。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusJobId])

  useEffect(() => {
    if (!selectedJobId) {
      setSelectedJob(null)
      setLogs(null)
      setLogsOpen(false)
      return
    }
    let cancelled = false
    getTrainJob(selectedJobId)
      .then((job) => {
        if (!cancelled) setSelectedJob(job)
      })
      .catch((err) => {
        if (!cancelled) setError(mapL2Error(err))
      })
    return () => {
      cancelled = true
    }
  }, [selectedJobId])

  useEffect(() => {
    if (!hasActiveJobs) return
    let cancelled = false
    const timer = window.setInterval(() => {
      void listTrainJobs({ limit: 30 })
        .then(async (jobBody) => {
          if (cancelled) return
          setJobs(jobBody.jobs)
          const currentId = selectedJobIdRef.current
          if (!currentId) return
          const fromList = jobBody.jobs.find((item) => item.job_id === currentId)
          const job = fromList || (await getTrainJob(currentId))
          if (cancelled) return
          setSelectedJob(job)
          if (job.status === "succeeded") {
            setNotice(`训练完成，Candidate ${job.model_bundle_id || "—"} 尚未发布。`)
          }
        })
        .catch((err) => {
          if (!cancelled) setError(mapL2Error(err))
        })
    }, 2500)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [hasActiveJobs])

  async function submit() {
    if (!selectedDataset || !equipmentType) return
    if (!selectedDataset.training_ready) {
      setError("该数据集不可用于训练。")
      return
    }
    setBusy(true)
    setError("")
    setNotice("")
    try {
      const payload =
        selectedDataset.source_type === "l1_prepared"
          ? {
              equipment_type: equipmentType,
              prepared_dataset_id: selectedDataset.prepared_dataset_id || selectedDataset.dataset_id,
            }
          : {
              equipment_type: equipmentType,
              dataset_id: selectedDataset.dataset_id,
            }
      const created = await submitTrainJob(payload)
      setNotice(`已提交新增训练 ${created.job_id}，状态 ${jobStatusLabel(created.status)}。`)
      const jobBody = await listTrainJobs({ limit: 30 })
      setJobs(jobBody.jobs)
      setSelectedJobId(created.job_id)
      const job = await getTrainJob(created.job_id)
      setSelectedJob(job)
    } catch (err) {
      setError(mapL2Error(err))
    } finally {
      setBusy(false)
    }
  }

  async function loadLogs() {
    if (!selectedJobId) return
    setLogsOpen(true)
    try {
      const body = await getTrainJobLogs(selectedJobId, 200)
      setLogs(body)
    } catch (err) {
      setError(mapL2Error(err))
    }
  }

  return (
    <div className="l2-stack">
      {showOnboarding && initialFocus ? (
        <OnboardingPanel
          focus={initialFocus}
          onCompleted={(result) => {
            const ped = result.ped as { prepared_dataset_id?: string; equipment_type?: string } | undefined
            const registered = result.register as { equipment_type?: string } | undefined
            if (registered?.equipment_type) setEquipmentType(registered.equipment_type)
            if (ped?.prepared_dataset_id) setDatasetId(ped.prepared_dataset_id)
            setNotice(String((result.next_action as { message?: string } | undefined)?.message || "设备配置已保存。"))
            setShowOnboarding(false)
            void listEquipmentTypes().then(setEquipment)
            void listDatasets().then(setDatasets)
          }}
        />
      ) : null}
    <div className="l2-split">
      <aside className="l2-side">
        <h3>新增训练</h3>
        {!showOnboarding && initialFocus?.onboarding_id ? (
          <button type="button" className="l2-link" onClick={() => setShowOnboarding(true)}>
            重新打开设备配置
          </button>
        ) : null}
        <label className="l2-field">
          <span>设备</span>
          <select value={equipmentType} onChange={(event) => setEquipmentType(event.target.value)}>
            {equipment.map((item) => (
              <option key={item.equipment_type} value={item.equipment_type}>
                {item.display_name}
              </option>
            ))}
          </select>
        </label>
        <label className="l2-field">
          <span>数据集</span>
          <select value={datasetId} onChange={(event) => setDatasetId(event.target.value)}>
            {equipmentDatasets.map((item) => (
              <option key={item.dataset_id} value={item.dataset_id}>
                {sourceTypeLabel(item.source_type)} · {item.dataset_id}
                {item.training_ready ? "" : "（不可训练）"}
              </option>
            ))}
          </select>
        </label>
        {selectedDataset ? (
          <div className="l2-meta-block">
            <p>
              {sourceTypeLabel(selectedDataset.source_type)} · 修订{" "}
              {selectedDataset.revision_id || initialFocus?.dataset_revision_id || "—"}
            </p>
            <p>
              记录 {selectedDataset.record_count ?? "—"} · 特征 {selectedDataset.feature_count ?? "—"} · 类别{" "}
              {selectedDataset.label_count ?? "—"}
            </p>
            <p>{selectedDataset.training_ready ? "可用于训练" : "当前不可训练"}</p>
            {initialFocus?.dataset_id ? <p>来源数据集 {initialFocus.dataset_id}</p> : null}
          </div>
        ) : null}
        {selectedEquipment ? (
          <div className="l2-meta-block">
            <p>二分类 RandomForestClassifier</p>
            <p>多分类 CatBoostClassifier</p>
            <p>
              特征 {selectedEquipment.feature_count} · 类别 {selectedEquipment.class_count}
            </p>
            <p>划分 70/15/15 · 二分类 Platt 校准</p>
            <p className="l2-note">首版不提供超参数编辑。再训练入口在模型管理。</p>
          </div>
        ) : null}
        <button
          type="button"
          className="l2-primary"
          disabled={busy || !selectedDataset?.training_ready}
          onClick={() => void submit()}
        >
          {busy ? "提交中…" : "提交新增训练"}
        </button>
        {error ? <p className="l2-note is-error">{error}</p> : null}
        {notice ? <p className="l2-note is-ok">{notice}</p> : null}
        <div className="l2-record-list">
          {jobs.map((job) => (
            <button
              key={job.job_id}
              type="button"
              className={job.job_id === selectedJobId ? "is-selected" : undefined}
              onClick={() => setSelectedJobId(job.job_id)}
            >
              <strong>
                {job.job_id} · {jobStatusLabel(job.status)}
              </strong>
              <span>
                {trainModeLabel(jobTrainMode(job))}
                {jobTrainMode(job) === "retrain" && job.parent_model_bundle_id
                  ? ` · 父模型 ${job.parent_model_bundle_id}`
                  : ""}
              </span>
              <span>
                {job.equipment_type} · {job.dataset_id}
              </span>
            </button>
          ))}
        </div>
      </aside>

      <section className="l2-main">
        {!selectedJob ? (
          <div className="l2-empty">
            <h3>任务详情</h3>
            <p>提交新增训练后可查看真实 job 状态。再训练任务也会出现在同一列表。后端无细粒度百分比时不会伪造进度。</p>
          </div>
        ) : (
          <article className="l2-card">
            <header>
              <h4>{selectedJob.job_id}</h4>
              <span>{jobStatusLabel(selectedJob.status)}</span>
            </header>
            <div className="l2-provenance">
              <p>
                模式 {trainModeLabel(jobTrainMode(selectedJob))}
                {jobTrainMode(selectedJob) === "retrain" ? " · 整包再训练（非 warm start）" : ""}
              </p>
              <p>父模型 {selectedJob.parent_model_bundle_id || "—"}</p>
              <p>设备 {selectedJob.equipment_type}</p>
              <p>
                数据 {selectedJob.dataset_id} · {selectedJob.dataset_revision_id || "—"}
              </p>
              <p>创建 {selectedJob.created_at || "—"}</p>
              <p>开始 {selectedJob.started_at || "—"} · 结束 {selectedJob.finished_at || "—"}</p>
              <p>Candidate {selectedJob.model_bundle_id || "尚未生成"}</p>
              {selectedJob.error_message ? <p className="l2-note is-error">{selectedJob.error_message}</p> : null}
              {selectedJob.status === "succeeded" ? (
                <p className="l2-note is-ok">训练完成，模型尚未发布。如需启用请前往模型管理单独确认激活。</p>
              ) : null}
            </div>
            <div className="l2-actions">
              <button type="button" onClick={() => void loadLogs()}>
                {logsOpen ? "刷新日志" : "展开日志"}
              </button>
              {selectedJob.model_bundle_id ? (
                <button type="button" onClick={onOpenModels}>
                  查看模型
                </button>
              ) : null}
            </div>
            {logsOpen ? (
              <pre className="l2-log">{(logs?.lines || []).join("\n") || "暂无日志行。"}</pre>
            ) : null}
          </article>
        )}
      </section>
    </div>
    </div>
  )
}
