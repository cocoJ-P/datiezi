import { useEffect, useMemo, useState } from "react"
import type { L3HandoffFocus } from "../overview/l3HandoffFocus"
import {
  createAnalysisRun,
  getAnalysisRun,
  getAnalysisRunByDiagnosis,
  listAnalysisResults,
  type AnalysisRunSummary,
  type EvidenceBundle,
} from "./api"
import { KnowledgePanel } from "./KnowledgePanel"
import { ReasoningPanel } from "./ReasoningPanel"
import { ReasoningTraceView } from "./ReasoningTraceView"
import "./l3.css"

export function L3Workbench({ handoff }: { handoff: L3HandoffFocus | null }) {
  const [run, setRun] = useState<AnalysisRunSummary | null>(null)
  const [items, setItems] = useState<
    Array<{
      record_id: string
      equipment_id?: string | null
      status: string
      screening_result?: string
      evidence: EvidenceBundle
      error_message?: string | null
    }>
  >([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [error, setError] = useState("")
  const [busy, setBusy] = useState(false)

  const diagnosisRunId = handoff?.diagnosis_run_id

  useEffect(() => {
    if (!diagnosisRunId) return
    let cancelled = false
    async function refresh() {
      try {
        let current = await getAnalysisRunByDiagnosis(diagnosisRunId!)
        if (!current.analysis_run) {
          const created = await createAnalysisRun({ diagnosis_run_id: diagnosisRunId! })
          if (cancelled) return
          setRun(created)
        } else {
          const detail = await getAnalysisRun(current.analysis_run.analysis_run_id)
          if (cancelled) return
          setRun(detail)
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err))
      }
    }
    void refresh()
    const timer = window.setInterval(() => void refresh(), 2500)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [diagnosisRunId])

  useEffect(() => {
    if (!run?.analysis_run_id) return
    let cancelled = false
    listAnalysisResults(run.analysis_run_id, {
      limit: 100,
      equipment_id: handoff?.equipment_id || undefined,
    })
      .then((page) => {
        if (cancelled) return
        setItems(page.items)
        setSelectedId((prev) => prev || page.items[0]?.record_id || null)
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err))
      })
    return () => {
      cancelled = true
    }
  }, [run?.analysis_run_id, run?.processed_record_count, handoff?.equipment_id])

  const selected = useMemo(() => items.find((item) => item.record_id === selectedId) || null, [items, selectedId])
  const evidence = selected?.evidence

  async function reanalyze() {
    if (!diagnosisRunId) return
    setBusy(true)
    setError("")
    try {
      const created = await createAnalysisRun({ diagnosis_run_id: diagnosisRunId, force_new: true })
      setRun(created)
      setItems([])
      setSelectedId(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  if (!handoff?.diagnosis_run_id) {
    return (
      <div className="l3-shell">
        <p className="l3-empty">尚未从总览选择需复核对象。请先在诊断与控制总览进入 L3 分支。</p>
      </div>
    )
  }

  return (
    <div className="l3-shell" aria-label="L3 深度分析工作台">
      <header className="l3-head">
        <div>
          <p className="l3-kicker">L3 Core v1</p>
          <h2>{handoff.display_name || handoff.equipment_type || "深度故障分析"}</h2>
          <p>
            DiagnosisRun {handoff.diagnosis_run_id}
            {run ? ` · AnalysisRun ${run.analysis_run_id}` : ""}
            {run ? ` · ${run.status}` : ""}
          </p>
        </div>
        <div className="l3-actions">
          <button type="button" disabled={busy || !diagnosisRunId} onClick={() => void reanalyze()}>
            {busy ? "提交中…" : "重新分析（新版本）"}
          </button>
        </div>
      </header>

      {error ? <p className="l3-note is-error">{error}</p> : null}

      {run ? (
        <div className="l3-metrics" aria-label="分析进度">
          <div>
            <small>待分析</small>
            <b>{run.selected_record_count}</b>
          </div>
          <div>
            <small>已处理</small>
            <b>{run.processed_record_count}</b>
          </div>
          <div>
            <small>成功</small>
            <b>{run.succeeded_record_count}</b>
          </div>
          <div>
            <small>不可判定</small>
            <b>{run.inconclusive_record_count}</b>
          </div>
          <div>
            <small>失败</small>
            <b>{run.failed_record_count}</b>
          </div>
        </div>
      ) : (
        <p className="l3-note">正在连接 AnalysisRun…</p>
      )}

      <div className="l3-layout">
        <aside className="l3-list" aria-label="记录列表">
          {items.length === 0 ? (
            <p className="l3-note">尚无结果。任务排队或执行中时请稍候。</p>
          ) : (
            <ul>
              {items.map((item) => (
                <li key={item.record_id}>
                  <button
                    type="button"
                    className={item.record_id === selectedId ? "is-active" : undefined}
                    onClick={() => setSelectedId(item.record_id)}
                  >
                    <strong>{item.record_id}</strong>
                    <span>
                      {item.screening_result || "—"} · {item.status}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </aside>

        <section className="l3-detail" aria-label="证据详情">
          {!evidence ? (
            <p className="l3-empty">选择左侧记录查看 EvidenceBundle。</p>
          ) : (
            <>
              <article className="l3-card">
                <header>
                  <h3>L2 初判</h3>
                  <span>{String((evidence.l2_context as { screening_result?: string } | undefined)?.screening_result || "—")}</span>
                </header>
                <p>初步故障 {String((evidence.l2_context as { preliminary_fault?: string } | undefined)?.preliminary_fault || "—")}</p>
                <p className="l3-note">
                  触发 {evidence.trigger_reason || handoff.trigger_reason || "—"} · 充分性{" "}
                  {evidence.evidence_sufficiency || "—"} · 历史 {evidence.history_sample_count ?? 0}（
                  {evidence.history_note || "—"}）
                </p>
              </article>

              <article className="l3-card">
                <header>
                  <h3>参与分析器</h3>
                </header>
                <p>{(evidence.covering_analyzers || []).join(" · ") || "—"}</p>
              </article>

              <article className="l3-card">
                <header>
                  <h3>故障类型复核</h3>
                </header>
                <ul className="l3-plain">
                  {(evidence.fault_type_review || []).map((item, index) => (
                    <li key={index}>
                      {String(item.fault_mode || "—")} · {String(item.verdict || "—")}（支持{" "}
                      {String(item.support_count ?? 0)} / 反对 {String(item.contradict_count ?? 0)}）
                    </li>
                  ))}
                </ul>
              </article>

              <article className="l3-card">
                <header>
                  <h3>严重度迹象</h3>
                </header>
                <p>
                  severity_level ={" "}
                  {evidence.severity_assessment && (evidence.severity_assessment as { severity_level?: unknown }).severity_level != null
                    ? String((evidence.severity_assessment as { severity_level?: unknown }).severity_level)
                    : "null（无正式分级）"}
                </p>
                <p className="l3-note">
                  越限{" "}
                  {Array.isArray((evidence.severity_assessment as { threshold_exceedances?: unknown[] } | undefined)?.threshold_exceedances)
                    ? (evidence.severity_assessment as { threshold_exceedances: unknown[] }).threshold_exceedances.length
                    : 0}{" "}
                  · 趋势信号{" "}
                  {Array.isArray((evidence.severity_assessment as { trend_assessment?: unknown[] } | undefined)?.trend_assessment)
                    ? (evidence.severity_assessment as { trend_assessment: unknown[] }).trend_assessment.length
                    : 0}
                </p>
              </article>

              <article className="l3-card">
                <header>
                  <h3>位置候选</h3>
                </header>
                <p className="l3-note">
                  {((evidence.location_candidates as { limitations?: string[] } | undefined)?.limitations || []).join("；") ||
                    "无可用部件映射"}
                </p>
              </article>

              <article className="l3-card">
                <header>
                  <h3>频谱特征</h3>
                </header>
                <p>
                  已观测标量：
                  {(
                    (evidence.spectral_feature_assessment as { observed_scalar_features?: string[] } | undefined)
                      ?.observed_scalar_features || []
                  ).join(" · ") || "—"}
                </p>
                <p className="l3-note">
                  {(evidence.spectral_feature_assessment as { note?: string } | undefined)?.note ||
                    "典型模式参考不等于已验证频谱。"}
                </p>
              </article>

              <KnowledgePanel
                analysisRunId={run?.analysis_run_id}
                recordId={selected?.record_id}
                equipmentType={handoff.equipment_type || evidence.equipment_type}
              />

              <ReasoningPanel
                analysisRunId={run?.analysis_run_id}
                recordId={selected?.record_id}
                screeningResult={String(
                  (evidence.l2_context as { screening_result?: string } | undefined)?.screening_result ||
                    selected?.screening_result ||
                    "",
                )}
                preliminaryFault={String(
                  (evidence.l2_context as { preliminary_fault?: string } | undefined)?.preliminary_fault || "",
                )}
              />

              <ReasoningTraceView
                evidence={evidence}
                l2Label={`${String((evidence.l2_context as { screening_result?: string } | undefined)?.screening_result || "—")} / ${String((evidence.l2_context as { preliminary_fault?: string } | undefined)?.preliminary_fault || "—")}`}
              />

              <article className="l3-card">
                <header>
                  <h3>支持证据</h3>
                  <span>{(evidence.support_evidence || []).length}</span>
                </header>
                <ul className="l3-plain">
                  {(evidence.support_evidence || []).slice(0, 12).map((item) => (
                    <li key={String(item.evidence_id)}>
                      {String(item.feature_name)} = {String(item.observed_value)} · {String(item.calculation_description || "")}
                    </li>
                  ))}
                </ul>
              </article>

              <article className="l3-card">
                <header>
                  <h3>反对证据</h3>
                  <span>{(evidence.contradict_evidence || []).length}</span>
                </header>
                <ul className="l3-plain">
                  {(evidence.contradict_evidence || []).slice(0, 12).map((item) => (
                    <li key={String(item.evidence_id)}>
                      {String(item.feature_name)} = {String(item.observed_value)} · {String(item.calculation_description || "")}
                    </li>
                  ))}
                  {(evidence.contradict_evidence || []).length === 0 ? <li className="l3-note">无</li> : null}
                </ul>
              </article>

              <article className="l3-card">
                <header>
                  <h3>缺失与限制</h3>
                </header>
                <p>{(evidence.missing_evidence || []).join(" · ") || "无缺失特征记录"}</p>
                <p className="l3-note">{(evidence.analysis_limitations || []).join("；") || "—"}</p>
              </article>

              <article className="l3-card">
                <header>
                  <h3>L4 交接预留</h3>
                </header>
                <p className="l3-note">
                  {(evidence.l4_handoff_stub as { note?: string } | undefined)?.note ||
                    "L4 尚未实现，不展示虚假根因。"}
                </p>
                <p className="l3-note">
                  根因 / 建议措施 / 优先级 / 影响程度：
                  {JSON.stringify(evidence.deferred_fields || { root_cause: null, recommended_actions: null })}
                </p>
              </article>
            </>
          )}
        </section>
      </div>
    </div>
  )
}
