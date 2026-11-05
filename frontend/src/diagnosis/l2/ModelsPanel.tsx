import { useEffect, useMemo, useState } from "react"
import {
  activateModel,
  getActiveStatus,
  getModelDetail,
  listDatasets,
  listEquipmentTypes,
  listModels,
  mapL2Error,
  rollbackModel,
  submitTrainJob,
} from "./api"
import { metricValue, modelStatusLabel, sourceTypeLabel } from "./format"
import type { ActiveStatus, DatasetSummary, EquipmentTypeSummary, ModelDetail, ModelSummary } from "./types"

type Props = {
  onOpenTraining: (jobId: string) => void
}

export function ModelsPanel({ onOpenTraining }: Props) {
  const [equipment, setEquipment] = useState<EquipmentTypeSummary[]>([])
  const [actives, setActives] = useState<ActiveStatus[]>([])
  const [equipmentType, setEquipmentType] = useState("")
  const [models, setModels] = useState<ModelSummary[]>([])
  const [selectedId, setSelectedId] = useState("")
  const [detail, setDetail] = useState<ModelDetail | null>(null)
  const [confirmAction, setConfirmAction] = useState<"activate" | "rollback" | null>(null)
  const [retrainOpen, setRetrainOpen] = useState(false)
  const [datasets, setDatasets] = useState<DatasetSummary[]>([])
  const [retrainDatasetId, setRetrainDatasetId] = useState("")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const [notice, setNotice] = useState("")
  const [loading, setLoading] = useState(true)

  const active = actives.find((item) => item.equipment_type === equipmentType)
  const selected = models.find((item) => item.model_bundle_id === selectedId)
  const retrainDatasets = datasets.filter(
    (item) => item.equipment_type === equipmentType && item.training_ready,
  )
  const selectedRetrainDataset = retrainDatasets.find(
    (item) => item.dataset_id === retrainDatasetId || item.prepared_dataset_id === retrainDatasetId,
  )

  async function refreshAll(nextEquipment = equipmentType) {
    setLoading(true)
    setError("")
    try {
      const [eq, activeBody, modelBody] = await Promise.all([
        listEquipmentTypes(),
        getActiveStatus(),
        nextEquipment ? listModels({ equipment_type: nextEquipment, limit: 100 }) : Promise.resolve({ total: 0, models: [] }),
      ])
      setEquipment(eq)
      const list = Array.isArray((activeBody as { equipment?: ActiveStatus[] }).equipment)
        ? ((activeBody as { equipment: ActiveStatus[] }).equipment)
        : activeBody && "equipment_type" in activeBody
          ? [activeBody as ActiveStatus]
          : []
      setActives(list)
      if (!nextEquipment && eq[0]) {
        setEquipmentType(eq[0].equipment_type)
        const modelsForFirst = await listModels({ equipment_type: eq[0].equipment_type, limit: 100 })
        setModels(modelsForFirst.models)
        const preferred = modelsForFirst.models.find((item) => item.status === "active") || modelsForFirst.models[0]
        setSelectedId(preferred?.model_bundle_id || "")
      } else {
        setModels(modelBody.models)
        if (!modelBody.models.some((item) => item.model_bundle_id === selectedId)) {
          const preferred = modelBody.models.find((item) => item.status === "active") || modelBody.models[0]
          setSelectedId(preferred?.model_bundle_id || "")
        }
      }
    } catch (err) {
      setError(mapL2Error(err))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void refreshAll()
  }, [])

  useEffect(() => {
    if (!equipmentType) return
    let cancelled = false
    listModels({ equipment_type: equipmentType, limit: 100 })
      .then((body) => {
        if (cancelled) return
        setModels(body.models)
        const preferred = body.models.find((item) => item.status === "active") || body.models[0]
        setSelectedId(preferred?.model_bundle_id || "")
        setDetail(null)
      })
      .catch((err) => {
        if (!cancelled) setError(mapL2Error(err))
      })
    return () => {
      cancelled = true
    }
  }, [equipmentType])

  useEffect(() => {
    if (!selectedId) {
      setDetail(null)
      return
    }
    let cancelled = false
    getModelDetail(selectedId)
      .then((body) => {
        if (!cancelled) setDetail(body)
      })
      .catch((err) => {
        if (!cancelled) setError(mapL2Error(err))
      })
    return () => {
      cancelled = true
    }
  }, [selectedId])

  const compareRows = useMemo(() => {
    const activeModel = models.find((item) => item.model_bundle_id === active?.active_model_bundle_id)
    if (!activeModel || !selected || selected.model_bundle_id === activeModel.model_bundle_id) return null
    const keys = ["binary_accuracy", "multiclass_accuracy", "binary_macro_f1", "multiclass_macro_f1", "binary_log_loss", "multiclass_log_loss", "binary_brier", "multiclass_brier"] as const
    return keys.map((key) => ({
      key,
      active: activeModel.metrics_summary?.[key] ?? null,
      selected: selected.metrics_summary?.[key] ?? null,
    }))
  }, [models, active, selected])

  async function openRetrain() {
    if (!selectedId || !equipmentType) return
    setError("")
    setNotice("")
    setRetrainOpen(true)
    try {
      const list = await listDatasets()
      setDatasets(list)
      const ready = list.filter((item) => item.equipment_type === equipmentType && item.training_ready)
      setRetrainDatasetId(ready[0]?.dataset_id || "")
    } catch (err) {
      setError(mapL2Error(err))
      setRetrainOpen(false)
    }
  }

  async function runRetrain() {
    if (!selectedId || !equipmentType || !selectedRetrainDataset) return
    setBusy(true)
    setError("")
    setNotice("")
    try {
      const payload =
        selectedRetrainDataset.source_type === "l1_prepared"
          ? {
              equipment_type: equipmentType,
              prepared_dataset_id: selectedRetrainDataset.prepared_dataset_id || selectedRetrainDataset.dataset_id,
              parent_model_bundle_id: selectedId,
            }
          : {
              equipment_type: equipmentType,
              dataset_id: selectedRetrainDataset.dataset_id,
              parent_model_bundle_id: selectedId,
            }
      const created = await submitTrainJob(payload)
      setRetrainOpen(false)
      setNotice(`已提交再训练 ${created.job_id}，正在跳转到训练管理。`)
      onOpenTraining(created.job_id)
    } catch (err) {
      setError(mapL2Error(err))
      setNotice("")
    } finally {
      setBusy(false)
    }
  }

  async function runConfirm() {
    if (!confirmAction || !selectedId) return
    setBusy(true)
    setError("")
    setNotice("")
    try {
      if (confirmAction === "activate") {
        await activateModel(selectedId)
        setNotice(`已激活 ${selectedId}。请确认 Runtime 与 Registry 一致。`)
      } else {
        await rollbackModel({ equipment_type: equipmentType, target_model_bundle_id: selectedId })
        setNotice(`已回滚到 ${selectedId}。`)
      }
      setConfirmAction(null)
      await refreshAll(equipmentType)
      const activeBody = await getActiveStatus(equipmentType)
      if ("active_model_bundle_id" in activeBody && activeBody.active_model_bundle_id !== selectedId) {
        setError("操作已返回，但 Active 与目标不一致，请刷新核对。")
      }
    } catch (err) {
      setError(mapL2Error(err))
      setNotice("")
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="l2-split">
      <aside className="l2-side">
        <h3>模型仓库</h3>
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
        {loading ? <p className="l2-note">加载中…</p> : null}
        {error ? <p className="l2-note is-error">{error}</p> : null}
        {notice ? <p className="l2-note is-ok">{notice}</p> : null}
        <div className="l2-meta-block">
          <p>Registry Active {active?.active_model_bundle_id || "无"}</p>
          <p>Runtime {active?.runtime_model_bundle_id || "未加载"}</p>
          <p>{active?.consistent ? "Registry 与 Runtime 一致" : "状态不一致或兼容模式"}</p>
        </div>
        <div className="l2-record-list">
          {models.map((item) => (
            <button
              key={item.model_bundle_id}
              type="button"
              className={item.model_bundle_id === selectedId ? "is-selected" : undefined}
              onClick={() => setSelectedId(item.model_bundle_id)}
            >
              <strong>
                {item.model_bundle_id} · {modelStatusLabel(item.status)}
              </strong>
              <span>
                {item.dataset_id || "—"} / {item.dataset_revision_id || "—"}
              </span>
              {item.parent_model_bundle_id ? <span>父模型 {item.parent_model_bundle_id}</span> : null}
            </button>
          ))}
        </div>
      </aside>

      <section className="l2-main">
        {!selected ? (
          <div className="l2-empty">
            <h3>模型详情</h3>
            <p>选择一个模型版本查看评测摘要与操作入口。</p>
          </div>
        ) : (
          <>
            <article className="l2-card">
              <header>
                <h4>{selected.model_bundle_id}</h4>
                <span>{modelStatusLabel(selected.status)}</span>
              </header>
              <div className="l2-provenance">
                <p>Schema {selected.feature_schema_version || "—"}</p>
                <p>数据 {selected.dataset_id || "—"} · {selected.dataset_revision_id || "—"}</p>
                <p>训练任务 {selected.training_job_id || "—"}</p>
                <p>父模型 {selected.parent_model_bundle_id || detail?.parent_model_bundle_id || "—"}</p>
                <p>创建 {selected.created_at || "—"} · 激活 {selected.activated_at || "—"}</p>
                <p>
                  算法 {detail?.algorithms?.binary || "RandomForestClassifier"} /{" "}
                  {detail?.algorithms?.multiclass || "CatBoostClassifier"}
                </p>
              </div>
              <div className="l2-metrics">
                <Metric label="Binary Acc" value={selected.metrics_summary?.binary_accuracy} />
                <Metric label="Multi Acc" value={selected.metrics_summary?.multiclass_accuracy} />
                <Metric label="Binary F1" value={selected.metrics_summary?.binary_macro_f1} />
                <Metric label="Multi F1" value={selected.metrics_summary?.multiclass_macro_f1} />
                <Metric label="Binary LogLoss" value={selected.metrics_summary?.binary_log_loss} />
                <Metric label="Multi LogLoss" value={selected.metrics_summary?.multiclass_log_loss} />
                <Metric label="Binary Brier" value={selected.metrics_summary?.binary_brier} />
                <Metric label="Multi Brier" value={selected.metrics_summary?.multiclass_brier} />
              </div>
              <p className="l2-note">
                校准{" "}
                {selected.calibration_status
                  ? Object.entries(selected.calibration_status)
                      .map(([key, value]) => `${key}:${value}`)
                      .join(" · ")
                  : "暂无数据"}
              </p>
              <div className="l2-actions">
                <button type="button" disabled={selected.status === "active" || busy} onClick={() => setConfirmAction("activate")}>
                  激活为 Active
                </button>
                <button
                  type="button"
                  disabled={selected.status === "active" || !active?.active_model_bundle_id || busy}
                  onClick={() => setConfirmAction("rollback")}
                >
                  回滚到此版本
                </button>
                <button type="button" disabled={!selectedId || busy} onClick={() => void openRetrain()}>
                  基于此版本再训练
                </button>
                <button type="button" onClick={() => void refreshAll(equipmentType)}>
                  刷新
                </button>
              </div>
            </article>

            {compareRows ? (
              <article className="l2-card">
                <header>
                  <h4>轻量对比</h4>
                  <span>
                    Active {active?.active_model_bundle_id} ↔ {selected.model_bundle_id}
                  </span>
                </header>
                <table className="l2-table">
                  <thead>
                    <tr>
                      <th>指标</th>
                      <th>Active</th>
                      <th>选中</th>
                    </tr>
                  </thead>
                  <tbody>
                    {compareRows.map((row) => (
                      <tr key={row.key}>
                        <td>{row.key}</td>
                        <td>{metricValue(row.active)}</td>
                        <td>{metricValue(row.selected)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </article>
            ) : null}
          </>
        )}
      </section>

      {confirmAction ? (
        <div className="l2-modal" role="dialog" aria-modal="true" aria-label="确认模型操作">
          <div className="l2-modal-card">
            <h3>{confirmAction === "activate" ? "确认激活" : "确认回滚"}</h3>
            <p>
              当前 Active：{active?.active_model_bundle_id || "无"}
              <br />
              目标模型：{selectedId}
            </p>
            <p className="l2-note">不会在点击后假定成功；将等待后端返回并刷新 Active / Runtime。</p>
            <div className="l2-actions">
              <button type="button" className="l2-primary" disabled={busy} onClick={() => void runConfirm()}>
                {busy ? "处理中…" : "确认"}
              </button>
              <button type="button" disabled={busy} onClick={() => setConfirmAction(null)}>
                取消
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {retrainOpen ? (
        <div className="l2-modal" role="dialog" aria-modal="true" aria-label="确认再训练">
          <div className="l2-modal-card">
            <h3>基于此版本再训练</h3>
            <p>
              父模型：{selectedId}
              <br />
              设备：{equipmentType}
            </p>
            <p className="l2-note">
              再训练仍是完整整包训练，仅记录谱系；不会 warm start / 加载父模型权重。成功后生成 Candidate，需在本页单独激活。
            </p>
            <label className="l2-field">
              <span>训练数据集（同设备 · training_ready）</span>
              <select value={retrainDatasetId} onChange={(event) => setRetrainDatasetId(event.target.value)}>
                {retrainDatasets.map((item) => (
                  <option key={item.dataset_id} value={item.dataset_id}>
                    {sourceTypeLabel(item.source_type)} · {item.dataset_id}
                  </option>
                ))}
              </select>
            </label>
            {retrainDatasets.length === 0 ? <p className="l2-note is-error">当前设备没有可用训练集。</p> : null}
            {error ? <p className="l2-note is-error">{error}</p> : null}
            <div className="l2-actions">
              <button
                type="button"
                className="l2-primary"
                disabled={busy || !selectedRetrainDataset}
                onClick={() => void runRetrain()}
              >
                {busy ? "提交中…" : "确认提交再训练"}
              </button>
              <button type="button" disabled={busy} onClick={() => setRetrainOpen(false)}>
                取消
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  )
}

function Metric({ label, value }: { label: string; value: number | null | undefined }) {
  return (
    <div>
      <small>{label}</small>
      <b>{metricValue(value)}</b>
    </div>
  )
}
