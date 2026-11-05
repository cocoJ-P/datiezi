import { useCallback, useEffect, useMemo, useState } from "react"
import { useRoute } from "../../routing"
import {
  getPredictionStatus,
  listL5Records,
  listL5Runs,
  listSynthesisRuns,
  type FinalDiagnosisAssessment,
  type FieldSlot,
  type L5RunSummary,
} from "../../report/api"
import { saveL5ReportFocus } from "../overview/l5HandoffFocus"
import { peekL3HandoffFocus } from "../overview/l3HandoffFocus"
import "./l5.css"

const FIELD_ORDER = [
  ["equipment_status", "设备状态"],
  ["fault_type", "故障类型"],
  ["fault_severity", "故障严重度"],
  ["fault_location", "故障位置"],
  ["diagnosis_confidence", "诊断置信度"],
  ["spectral_description", "频谱描述"],
  ["root_cause_analysis", "根因分析"],
  ["recommended_actions", "建议措施"],
  ["priority", "优先级"],
  ["impact_degree", "影响程度"],
] as const

function formatSlot(slot?: FieldSlot): string {
  if (!slot) return "—"
  if (slot.value == null) return `未评估${slot.null_reason ? `（${slot.null_reason}）` : ""}`
  if (slot.display) return String(slot.display)
  if (typeof slot.value === "string" || typeof slot.value === "number") return String(slot.value)
  try {
    return JSON.stringify(slot.value)
  } catch {
    return String(slot.value)
  }
}

export function L5Workbench() {
  const { navigate } = useRoute()
  const [runs, setRuns] = useState<L5RunSummary[]>([])
  const [selectedRunId, setSelectedRunId] = useState("")
  const [records, setRecords] = useState<
    Array<{ record_id: string; outcome?: string; assessment?: FinalDiagnosisAssessment }>
  >([])
  const [selectedRecordId, setSelectedRecordId] = useState("")
  const [modelId, setModelId] = useState("")
  const [synTotal, setSynTotal] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const handoff = useMemo(() => peekL3HandoffFocus(), [])

  const load = useCallback(async () => {
    setBusy(true)
    setError(null)
    try {
      const [page, pred, syn] = await Promise.all([
        listL5Runs(50),
        getPredictionStatus().catch(() => ({ model_id: "" })),
        listSynthesisRuns(50).catch(() => ({ total: 0, items: [] })),
      ])
      setModelId(pred.model_id || "")
      setSynTotal(syn.total || 0)
      let items = page.items || []
      // 若有 L3 handoff，优先展示同 DiagnosisRun / AnalysisRun 的报告
      if (handoff?.diagnosis_run_id) {
        const matched = items.filter((r) => r.diagnosis_run_id === handoff.diagnosis_run_id)
        if (matched.length) items = [...matched, ...items.filter((r) => r.diagnosis_run_id !== handoff.diagnosis_run_id)]
      }
      setRuns(items)
      if (!selectedRunId && items[0]) setSelectedRunId(items[0].l5_run_id)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc))
      setRuns([])
    } finally {
      setBusy(false)
    }
  }, [handoff?.diagnosis_run_id, selectedRunId])

  useEffect(() => {
    void load()
  }, [load])

  useEffect(() => {
    if (!selectedRunId) return
    let cancelled = false
    ;(async () => {
      try {
        const page = await listL5Records(selectedRunId, 100)
        if (cancelled) return
        let items = page.items || []
        if (handoff?.record_ids?.length) {
          const prefer = new Set(handoff.record_ids)
          items = [...items.filter((r) => prefer.has(r.record_id)), ...items.filter((r) => !prefer.has(r.record_id))]
        }
        setRecords(items)
        const preferId = handoff?.record_ids?.find((id) => items.some((r) => r.record_id === id))
        setSelectedRecordId(preferId || items[0]?.record_id || "")
      } catch (exc) {
        if (!cancelled) setError(exc instanceof Error ? exc.message : String(exc))
      }
    })()
    return () => {
      cancelled = true
    }
  }, [selectedRunId, handoff?.record_ids])

  const assessment = records.find((r) => r.record_id === selectedRecordId)?.assessment
  const selectedRun = runs.find((r) => r.l5_run_id === selectedRunId)
  const hasOverlay = Boolean(assessment?.supervised_prediction || assessment?.fields?.equipment_status?.source_layer === "l5_prediction")
  const knowledgeNote =
    selectedRun?.analysis_run_id || selectedRun?.l4_run_id
      ? "已绑定 L3/L4 Run（知识增强支线可用）"
      : "知识增强分析未运行（L5 预测仍可独立使用）"

  function openReportCenter() {
    if (!selectedRunId) return
    saveL5ReportFocus({
      l5_run_id: selectedRunId,
      record_id: selectedRecordId || undefined,
      diagnosis_run_id: selectedRun?.diagnosis_run_id || undefined,
      analysis_run_id: selectedRun?.analysis_run_id || undefined,
      l4_run_id: selectedRun?.l4_run_id || undefined,
      equipment_type: (selectedRun?.source_versions?.equipment_type as string | undefined) || undefined,
      reason: "from_l5_workbench",
    })
    navigate({ page: "report", runId: selectedRunId })
  }

  return (
    <div className="l5w-shell" aria-label="L5 综合诊断工作台">
      <header className="l5w-head">
        <div>
          <p className="l5w-kicker">L5 / SYNTHESIS</p>
          <h2>综合诊断与十字段预测</h2>
          <p className="l5w-meta">
            模型 {modelId || "L5P-v1"} · Synthesis 已完成 {synTotal} 次
            {busy ? " · 加载中" : ""}
          </p>
          <p className="l5w-note">
            十字段主输出默认来自监督预测；L3/L4 为知识增强支线。左侧「报告中心」保留完整追溯与导出。
            反馈/知识回写能力仍可在报告交接中使用，与本页综合诊断分离。
          </p>
        </div>
        <div className="l5w-actions">
          <button type="button" className="dx-primary" disabled={!selectedRunId} onClick={openReportCenter}>
            打开报告中心
          </button>
          <button type="button" disabled={busy} onClick={() => void load()}>
            刷新
          </button>
        </div>
      </header>

      {error ? <p className="l5w-note is-error">{error}</p> : null}

      {!runs.length ? (
        <p className="l5w-empty">
          暂无 L5Run。可通过 API 绑定 Diagnosis/Analysis/L4 Run 生成报告；L5P-v1 也可对观测输入独立预测，不要求先跑完 L3/L4。
        </p>
      ) : (
        <div className="l5w-layout">
          <aside className="l5w-list">
            <p className="l5w-list-title">L5Run</p>
            <ul>
              {runs.map((run) => (
                <li key={run.l5_run_id}>
                  <button
                    type="button"
                    className={run.l5_run_id === selectedRunId ? "is-active" : undefined}
                    onClick={() => setSelectedRunId(run.l5_run_id)}
                  >
                    <strong>{run.l5_run_id}</strong>
                    <span>
                      {run.status} · 记录 {run.selected_record_count ?? "—"}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
            {selectedRunId ? (
              <>
                <p className="l5w-list-title">记录</p>
                <ul>
                  {records.map((rec) => (
                    <li key={rec.record_id}>
                      <button
                        type="button"
                        className={rec.record_id === selectedRecordId ? "is-active" : undefined}
                        onClick={() => setSelectedRecordId(rec.record_id)}
                      >
                        <strong>{rec.record_id}</strong>
                        <span>{rec.outcome || "—"}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              </>
            ) : null}
          </aside>

          <section className="l5w-detail">
            <article className="l5w-card">
              <header>
                <h3>运行绑定</h3>
                <span className={`l5w-badge ${hasOverlay ? "is-ok" : "is-muted"}`}>
                  {hasOverlay ? "十字段=L5P 预测" : "聚合/未叠加"}
                </span>
              </header>
              <p className="l5w-note">{knowledgeNote}</p>
              <dl className="l5w-dl">
                <div>
                  <dt>DiagnosisRun</dt>
                  <dd>{selectedRun?.diagnosis_run_id || "—"}</dd>
                </div>
                <div>
                  <dt>AnalysisRun</dt>
                  <dd>{selectedRun?.analysis_run_id || "未运行"}</dd>
                </div>
                <div>
                  <dt>L4Run</dt>
                  <dd>{selectedRun?.l4_run_id || "未运行"}</dd>
                </div>
                <div>
                  <dt>设备</dt>
                  <dd>{String(selectedRun?.source_versions?.equipment_type || "—")}</dd>
                </div>
              </dl>
            </article>

            {!assessment ? (
              <p className="l5w-empty">选择记录查看十字段。</p>
            ) : (
              <article className="l5w-card">
                <header>
                  <h3>十字段预测（数据集十字段）</h3>
                </header>
                <p className="l5w-note">
                  诊断置信度为数据集回归目标，不等于校准后的正确概率。根因模板 ≠ 已确认物理根因（
                  {assessment.root_cause_confirmation || "unconfirmed"}）。
                </p>
                <table className="l5w-table">
                  <thead>
                    <tr>
                      <th>字段</th>
                      <th>预测值</th>
                      <th>来源</th>
                    </tr>
                  </thead>
                  <tbody>
                    {FIELD_ORDER.map(([key, label]) => {
                      const slot = assessment.fields?.[key]
                      return (
                        <tr key={key}>
                          <td>{label}</td>
                          <td>{formatSlot(slot)}</td>
                          <td>
                            {slot?.source_layer || "—"}
                            {slot?.extras?.source_model_id ? ` · ${String(slot.extras.source_model_id)}` : ""}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
                <div className="l5w-actions" style={{ marginTop: 12 }}>
                  <button type="button" className="dx-primary" onClick={openReportCenter}>
                    查看最终报告与 Synthesis
                  </button>
                </div>
              </article>
            )}
          </section>
        </div>
      )}
    </div>
  )
}
