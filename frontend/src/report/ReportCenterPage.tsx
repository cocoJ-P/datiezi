import { useCallback, useEffect, useMemo, useState } from "react"
import {
  createAlertRun,
  createSynthesisRun,
  getAlertRecord,
  getAlertRule,
  getAlertsByL5,
  getL5Provenance,
  getPredictionStatus,
  listL5Records,
  listL5Runs,
  listSynthesisRecords,
  type AlertAssessment,
  type FieldSlot,
  type FinalDiagnosisAssessment,
  type L5RunSummary,
  type SynthesisAssessment,
} from "./api"
import { DIAGNOSTIC_REPORTS, type DiagnosticReport } from "./reportData"
import "./report.css"

const FIELD_ORDER = [
  "equipment_status",
  "fault_type",
  "fault_severity",
  "fault_location",
  "diagnosis_confidence",
  "spectral_description",
  "root_cause_analysis",
  "recommended_actions",
  "priority",
  "impact_degree",
] as const

function reviewBadge(status?: string) {
  const label =
    status === "reviewed" ? "已审核" : status === "incomplete" ? "不完整" : status === "review_required" ? "待审核" : "草稿"
  const tone = status === "reviewed" ? "released" : status === "incomplete" ? "archived" : "draft"
  return <span className={`rp-badge rp-badge--${tone}`}>{label}</span>
}

function formatSlotValue(slot?: FieldSlot): string {
  if (!slot) return "—"
  if (slot.value == null) {
    return `未评估${slot.null_reason ? `（${slot.null_reason}）` : ""}`
  }
  if (slot.display) return String(slot.display)
  if (typeof slot.value === "string" || typeof slot.value === "number") return String(slot.value)
  try {
    return JSON.stringify(slot.value, null, 0)
  } catch {
    return String(slot.value)
  }
}

function AlertPanel({
  alert,
  onRule,
  ruleDetail,
}: {
  alert?: AlertAssessment | null
  onRule: (ruleId: string) => void
  ruleDetail?: Record<string, unknown> | null
}) {
  if (!alert) {
    return <p className="rp-body">尚未加载规程阈值评估。可点击「运行阈值评估」生成独立 AlertRun。</p>
  }
  const rows = [...(alert.formal_alerts || []), ...(alert.reference_comparisons || [])]
  return (
    <section className="rp-section">
      <h2>规程阈值与告警依据（L5b）</h2>
      <p className="rp-body">
        正式告警 {alert.formal_alert_count ?? 0} · 参考比对 {alert.reference_count ?? 0} · catalog{" "}
        {alert.catalog_version || "—"}。本模块不改写十字段，不生成风险等级/P0–P3。
      </p>
      {rows.length ? (
        <table className="rp-table">
          <thead>
            <tr>
              <th>通道</th>
              <th>测点</th>
              <th>实测</th>
              <th>比较</th>
              <th>限值</th>
              <th>级别</th>
              <th>状态</th>
              <th>审核</th>
              <th>规则</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, idx) => (
              <tr key={idx}>
                <td>{r.formal ? "正式" : "参考"}</td>
                <td>{String(r.canonical_feature_id || "")}</td>
                <td>
                  {String(r.observed_value)} {String(r.observed_unit || "")}
                </td>
                <td>{String(r.comparison_operator)}</td>
                <td>
                  {String(r.threshold_value)} {String(r.threshold_unit || "")}
                </td>
                <td>{String(r.alert_level)}</td>
                <td>{String(r.evaluation_status)}</td>
                <td>{String(r.review_status)}</td>
                <td>
                  <button type="button" onClick={() => onRule(String(r.rule_id))}>
                    {String(r.rule_id)}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="rp-body">无比较行（可能缺少观测或规则未适用）。</p>
      )}
      {(alert.conflicts || []).length ? (
        <>
          <span className="rp-label">规则冲突 / 非等价说明</span>
          <ul className="rp-list">
            {(alert.conflicts || []).slice(0, 8).map((c) => (
              <li key={String(c.conflict_id)}>
                {String(c.conflict_id)} · {String(c.status)} · {String(c.summary)}
              </li>
            ))}
          </ul>
        </>
      ) : null}
      {ruleDetail ? (
        <pre className="rp-mono" style={{ whiteSpace: "pre-wrap", fontSize: 12 }}>
          {JSON.stringify(ruleDetail, null, 2)}
        </pre>
      ) : null}
      {(alert.limitations || []).length ? (
        <div className="rp-callout rp-callout--muted">
          <ul className="rp-list">
            {(alert.limitations || []).slice(0, 6).map((x) => (
              <li key={x}>{x}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  )
}

function SynthesisPanel({
  synthesis,
  onRun,
  busy,
}: {
  synthesis?: SynthesisAssessment | null
  onRun: (allowLlm: boolean) => void
  busy: boolean
}) {
  return (
    <section className="rp-section">
      <h2>第二部分：AI 知识增强诊断（L3/L4 + 对照）</h2>
      <p className="rp-body">
        默认仅确定性对照；多智能体 Synthesis Agent 需主动触发且默认关闭 LLM。缺少 L3/L4 时标记
        unavailable，不伪造结论，也不改写十字段监督预测。
      </p>
      <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
        <button type="button" disabled={busy} onClick={() => onRun(false)}>
          运行确定性综合对照
        </button>
        <button type="button" disabled={busy} onClick={() => onRun(true)}>
          按需触发 Synthesis Agent（LLM）
        </button>
      </div>
      {!synthesis ? (
        <p className="rp-body">尚未生成综合评估。可点击上方按钮（不会在普通浏览时自动调用 LLM）。</p>
      ) : (
        <>
          <div className="rp-grid">
            <div>
              <span className="rp-label">综合 Run</span>
              <p className="rp-mono">{String(synthesis.synthesis_run_id || "—")}</p>
            </div>
            <div>
              <span className="rp-label">一致性摘要</span>
              <p className="rp-mono">{JSON.stringify(synthesis.consistency_summary || {})}</p>
            </div>
            <div>
              <span className="rp-label">L4 确认快照</span>
              <p>{synthesis.l4_root_cause_confirmation_snapshot || "unconfirmed"}</p>
            </div>
            <div>
              <span className="rp-label">Agent 审计</span>
              <p className="rp-mono">{JSON.stringify((synthesis.agent_audit || [])[0] || {})}</p>
            </div>
          </div>
          <table className="rp-table">
            <thead>
              <tr>
                <th>字段</th>
                <th>一致性</th>
                <th>说明</th>
              </tr>
            </thead>
            <tbody>
              {(synthesis.field_comparisons || []).map((row, idx) => (
                <tr key={idx}>
                  <td>{String(row.field || "")}</td>
                  <td>{String(row.consistency || "")}</td>
                  <td>{String(row.detail || row.note || "")}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {(synthesis.limitations || []).length ? (
            <ul className="rp-list">
              {(synthesis.limitations || []).slice(0, 8).map((x) => (
                <li key={x}>{x}</li>
              ))}
            </ul>
          ) : null}
        </>
      )}
    </section>
  )
}

function LiveReportDetail({
  assessment,
  provenance,
  onTrace,
  activeField,
  alert,
  onRule,
  ruleDetail,
  synthesis,
  onSynthesis,
  busy,
  predictionModelId,
}: {
  assessment: FinalDiagnosisAssessment
  provenance?: Record<string, unknown> | null
  onTrace: (field: string) => void
  activeField?: string | null
  alert?: AlertAssessment | null
  onRule: (ruleId: string) => void
  ruleDetail?: Record<string, unknown> | null
  synthesis?: SynthesisAssessment | null
  onSynthesis: (allowLlm: boolean) => void
  busy: boolean
  predictionModelId?: string
}) {
  const labels = assessment.field_labels_zh || {}
  const fields = assessment.fields || {}
  const predMeta = assessment.supervised_prediction
  return (
    <article className="rp-doc">
      <header className="rp-doc__header">
        <div className="rp-doc__meta-row">
          <span className="rp-doc__id">{assessment.l5_run_id}</span>
          {reviewBadge(assessment.review_status)}
          <span className="rp-doc__ver">outcome {assessment.outcome || "—"}</span>
        </div>
        <h1 className="rp-doc__title">
          综合诊断报告 · {assessment.record_id}
        </h1>
        <div className="rp-doc__issue">
          <div>
            <span className="rp-label">设备</span>
            <strong>
              {assessment.equipment_type || "—"} / {assessment.equipment_id || "—"}
            </strong>
          </div>
          <div>
            <span className="rp-label">根因确认</span>
            <strong>{assessment.root_cause_confirmation || "unconfirmed"}</strong>
          </div>
          <div>
            <span className="rp-label">监督模型</span>
            <strong>{predMeta?.model_id || predictionModelId || "—"}</strong>
          </div>
        </div>
      </header>

      <section className="rp-section">
        <h2>第一部分：十字段预测（比赛主输出）</h2>
        <p className="rp-body">
          主值来自 L5 监督预测（CatBoost）；诊断置信度为数据集回归目标，不等于已校准正确概率；根因模板 ≠
          现场已确认根因。L2 CONFLICT/UNCERTAIN 不是四级设备状态。
        </p>
        <div className="rp-summary" style={{ gridTemplateColumns: "1fr" }}>
          {FIELD_ORDER.map((key) => {
            const slot = fields[key]
            const extras = slot?.extras || {}
            return (
              <div key={key} style={{ borderBottom: "1px solid var(--line, #e5e7eb)", paddingBottom: 10 }}>
                <span className="rp-label">{labels[key] || key}</span>
                <p>{formatSlotValue(slot)}</p>
                <p className="rp-inline-note">
                  来源：{slot?.source_layer || "—"} · 模型 {String(extras.source_model_id || predMeta?.model_id || "—")} ·
                  状态 {String(extras.prediction_status || "—")}
                </p>
                {slot?.uncertainty ? <p className="rp-inline-note">不确定性：{slot.uncertainty}</p> : null}
                <button type="button" className="rp-sidebar__item" style={{ marginTop: 4 }} onClick={() => onTrace(key)}>
                  查看证据追溯
                </button>
                {activeField === key && provenance ? (
                  <pre className="rp-mono" style={{ whiteSpace: "pre-wrap", fontSize: 12, marginTop: 8 }}>
                    {JSON.stringify(provenance, null, 2)}
                  </pre>
                ) : null}
              </div>
            )
          })}
        </div>
      </section>

      <SynthesisPanel synthesis={synthesis} onRun={onSynthesis} busy={busy} />

      <AlertPanel alert={alert} onRule={onRule} ruleDetail={ruleDetail} />

      <section className="rp-section">
        <h2>第三部分：评测与追溯</h2>
        <p className="rp-body">
          离线 internal_val 指标见模型包 metrics；本页展示证据追溯与局限性。评测标签不进入推理 API。
        </p>
        <p className="rp-mono">
          fingerprint {predMeta?.model_input_fingerprint || "—"} · L2{" "}
          {assessment.source_run_ids?.diagnosis_run_id || "—"} · L3 {assessment.source_run_ids?.analysis_run_id || "—"} ·
          L4 {assessment.source_run_ids?.l4_run_id || "—"}
        </p>
      </section>

      <section className="rp-section">
        <h2>验证建议</h2>
        {(assessment.verification_recommendations || []).length ? (
          <table className="rp-table">
            <thead>
              <tr>
                <th>ID</th>
                <th>类型</th>
                <th>说明</th>
                <th>缺失条件</th>
                <th>人工确认</th>
              </tr>
            </thead>
            <tbody>
              {(assessment.verification_recommendations || []).map((rec) => (
                <tr key={String(rec.recommendation_id)}>
                  <td className="rp-mono">{String(rec.recommendation_id)}</td>
                  <td>{String(rec.recommendation_type)}</td>
                  <td>{String(rec.description)}</td>
                  <td className="rp-mono">{String(rec.condition_id)}</td>
                  <td>{rec.requires_human_review ? "是" : "否"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="rp-body">当前记录无自动生成的验证建议。</p>
        )}
      </section>

      <section className="rp-section">
        <h2>4. 局限性</h2>
        <ul className="rp-list">
          {(assessment.limitations || []).map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      </section>

      <footer className="rp-doc__footer">
        L5a 真实引擎报告 · 非演示数据 · {assessment.record_id}
      </footer>
    </article>
  )
}

function DemoReportDetail({ report }: { report: DiagnosticReport }) {
  return (
    <article className="rp-doc">
      <header className="rp-doc__header">
        <div className="rp-doc__meta-row">
          <span className="rp-doc__id">{report.report_id}</span>
          <span className="rp-badge rp-badge--archived">演示数据</span>
        </div>
        <h1 className="rp-doc__title">{report.title}</h1>
        <p className="rp-body">
          当前为静态示例，不是 L5 引擎输出。风险等级 / P1 / 维修建议等均为硬编码演示，不得作为正式诊断依据。
        </p>
      </header>
      <section className="rp-section">
        <h2>示例字段（不可用于业务）</h2>
        <p>设备状态：{report.device_status}</p>
        <p>故障类型：{report.fault_type}</p>
        <p>风险等级（演示）：{report.risk_level}</p>
        <p>优先级（演示）：{report.priority}</p>
        <p>根因（演示）：{report.root_cause}</p>
      </section>
    </article>
  )
}

export function ReportCenterPage() {
  const [mode, setMode] = useState<"live" | "demo">("live")
  const [runs, setRuns] = useState<L5RunSummary[]>([])
  const [selectedRunId, setSelectedRunId] = useState("")
  const [records, setRecords] = useState<
    Array<{ record_id: string; outcome?: string; review_status?: string; assessment?: FinalDiagnosisAssessment }>
  >([])
  const [selectedRecordId, setSelectedRecordId] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [activeField, setActiveField] = useState<string | null>(null)
  const [provenance, setProvenance] = useState<Record<string, unknown> | null>(null)
  const [demoId, setDemoId] = useState(DIAGNOSTIC_REPORTS[0]?.report_id ?? "")
  const [alertRunId, setAlertRunId] = useState<string>("")
  const [alertAssessment, setAlertAssessment] = useState<AlertAssessment | null>(null)
  const [ruleDetail, setRuleDetail] = useState<Record<string, unknown> | null>(null)
  const [synthesis, setSynthesis] = useState<SynthesisAssessment | null>(null)
  const [predictionModelId, setPredictionModelId] = useState<string>("")

  const loadRuns = useCallback(async () => {
    setBusy(true)
    setError(null)
    try {
      const page = await listL5Runs(50)
      setRuns(page.items || [])
      if (!selectedRunId && page.items?.[0]) {
        setSelectedRunId(page.items[0].l5_run_id)
      }
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc))
      setRuns([])
    } finally {
      setBusy(false)
    }
  }, [selectedRunId])

  useEffect(() => {
    if (mode === "live") void loadRuns()
  }, [mode, loadRuns])

  useEffect(() => {
    if (mode !== "live") return
    void getPredictionStatus()
      .then((s) => setPredictionModelId(s.model_id || ""))
      .catch(() => setPredictionModelId(""))
  }, [mode])

  useEffect(() => {
    if (mode !== "live" || !selectedRunId) return
    let cancelled = false
    ;(async () => {
      setBusy(true)
      try {
        const page = await listL5Records(selectedRunId, 100)
        if (cancelled) return
        setRecords(page.items || [])
        setSelectedRecordId(page.items?.[0]?.record_id || "")
        setActiveField(null)
        setProvenance(null)
        setAlertAssessment(null)
        setRuleDetail(null)
        setSynthesis(null)
        const linked = await getAlertsByL5(selectedRunId)
        if (!cancelled) setAlertRunId(linked.alert_run?.alert_run_id || "")
      } catch (exc) {
        if (!cancelled) setError(exc instanceof Error ? exc.message : String(exc))
      } finally {
        if (!cancelled) setBusy(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [mode, selectedRunId])

  useEffect(() => {
    if (!alertRunId || !selectedRecordId) {
      setAlertAssessment(null)
      return
    }
    let cancelled = false
    ;(async () => {
      try {
        const rec = await getAlertRecord(alertRunId, selectedRecordId)
        if (!cancelled) setAlertAssessment(rec.assessment || null)
      } catch {
        if (!cancelled) setAlertAssessment(null)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [alertRunId, selectedRecordId])

  const selectedAssessment = useMemo(
    () => records.find((r) => r.record_id === selectedRecordId)?.assessment,
    [records, selectedRecordId],
  )

  const onTrace = async (field: string) => {
    if (!selectedRunId || !selectedRecordId) return
    setActiveField(field)
    try {
      const body = await getL5Provenance(selectedRunId, selectedRecordId, field)
      setProvenance(body)
    } catch (exc) {
      setProvenance({ error: exc instanceof Error ? exc.message : String(exc) })
    }
  }

  const onRunAlerts = async () => {
    if (!selectedRunId) return
    setBusy(true)
    setError(null)
    try {
      const out = await createAlertRun({ source_l5_run_id: selectedRunId, force_new: false })
      setAlertRunId(out.alert_run.alert_run_id)
      if (selectedRecordId) {
        const rec = await getAlertRecord(out.alert_run.alert_run_id, selectedRecordId)
        setAlertAssessment(rec.assessment || null)
      }
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc))
    } finally {
      setBusy(false)
    }
  }

  const onRule = async (ruleId: string) => {
    try {
      const body = await getAlertRule(ruleId)
      setRuleDetail(body)
    } catch (exc) {
      setRuleDetail({ error: exc instanceof Error ? exc.message : String(exc) })
    }
  }

  const onSynthesis = async (allowLlm: boolean) => {
    if (!selectedRunId || !selectedRecordId) return
    setBusy(true)
    setError(null)
    try {
      const out = await createSynthesisRun({
        l5_run_id: selectedRunId,
        record_ids: [selectedRecordId],
        allow_llm_synthesis: allowLlm,
        force_new: true,
        max_records: 1,
      })
      const page = await listSynthesisRecords(out.synthesis_run.synthesis_run_id, 10)
      const hit = page.items?.find((i) => i.record_id === selectedRecordId) || page.items?.[0]
      setSynthesis(hit?.assessment || null)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc))
    } finally {
      setBusy(false)
    }
  }

  const demoSelected = DIAGNOSTIC_REPORTS.find((r) => r.report_id === demoId) || DIAGNOSTIC_REPORTS[0]

  return (
    <div className="rp-page">
      <div className="rp-topbar">
        <div>
          <h1 className="rp-page__title">报告中心</h1>
          <p className="rp-page__sub">L5-P3 十字段监督预测 + 知识增强对照 · 演示数据严格隔离</p>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <button type="button" onClick={() => setMode("live")} disabled={mode === "live"}>
            真实 L5 报告
          </button>
          <button type="button" onClick={() => setMode("demo")} disabled={mode === "demo"}>
            演示数据
          </button>
          {mode === "live" ? (
            <>
              <button type="button" onClick={() => void loadRuns()} disabled={busy}>
                刷新
              </button>
              <button type="button" onClick={() => void onRunAlerts()} disabled={busy || !selectedRunId}>
                运行阈值评估
              </button>
            </>
          ) : null}
        </div>
      </div>

      {error ? <p className="rp-body">错误：{error}</p> : null}

      {mode === "demo" ? (
        <div className="rp-layout">
          <aside className="rp-sidebar">
            <div className="rp-sidebar__head">演示列表（非引擎）</div>
            <ul className="rp-sidebar__list">
              {DIAGNOSTIC_REPORTS.map((r) => (
                <li key={r.report_id}>
                  <button
                    type="button"
                    className={`rp-sidebar__item${demoId === r.report_id ? " is-active" : ""}`}
                    onClick={() => setDemoId(r.report_id)}
                  >
                    <div className="rp-sidebar__item-title">{r.title}</div>
                    <div className="rp-sidebar__item-meta">
                      <span>{r.report_id}</span>
                      <span className="rp-badge rp-badge--archived">演示</span>
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          </aside>
          <main className="rp-main">{demoSelected ? <DemoReportDetail report={demoSelected} /> : null}</main>
        </div>
      ) : (
        <div className="rp-layout">
          <aside className="rp-sidebar">
            <div className="rp-sidebar__head">L5Run 列表 {busy ? "· 加载中" : ""}</div>
            <ul className="rp-sidebar__list">
              {runs.length ? (
                runs.map((r) => (
                  <li key={r.l5_run_id}>
                    <button
                      type="button"
                      className={`rp-sidebar__item${selectedRunId === r.l5_run_id ? " is-active" : ""}`}
                      onClick={() => setSelectedRunId(r.l5_run_id)}
                    >
                      <div className="rp-sidebar__item-title">{r.l5_run_id}</div>
                      <div className="rp-sidebar__item-meta">
                        <span>{r.status}</span>
                        <span>{(r.created_at || "").slice(0, 10)}</span>
                      </div>
                      <div className="rp-sidebar__item-tags">
                        <span className="rp-badge rp-badge--draft">
                          {r.selected_record_count || 0} 条 · partial {r.partial_record_count || 0}
                        </span>
                      </div>
                    </button>
                  </li>
                ))
              ) : (
                <li>
                  <p className="rp-body" style={{ padding: 12 }}>
                    暂无 L5Run。请先通过 API `POST /l5/runs` 绑定显式 L2/L3/L4 run_id 生成报告。
                  </p>
                </li>
              )}
            </ul>
            {selectedRunId ? (
              <>
                <div className="rp-sidebar__head">记录</div>
                <ul className="rp-sidebar__list">
                  {records.map((rec) => (
                    <li key={rec.record_id}>
                      <button
                        type="button"
                        className={`rp-sidebar__item${selectedRecordId === rec.record_id ? " is-active" : ""}`}
                        onClick={() => {
                          setSelectedRecordId(rec.record_id)
                          setActiveField(null)
                          setProvenance(null)
                        }}
                      >
                        <div className="rp-sidebar__item-title">{rec.record_id}</div>
                        <div className="rp-sidebar__item-meta">
                          <span>{rec.outcome || "—"}</span>
                          {reviewBadge(rec.review_status)}
                        </div>
                      </button>
                    </li>
                  ))}
                </ul>
              </>
            ) : null}
          </aside>
          <main className="rp-main">
            {selectedAssessment ? (
              <LiveReportDetail
                assessment={selectedAssessment}
                provenance={provenance}
                onTrace={(f) => void onTrace(f)}
                activeField={activeField}
                alert={alertAssessment}
                onRule={(id) => void onRule(id)}
                ruleDetail={ruleDetail}
                synthesis={synthesis}
                onSynthesis={(allow) => void onSynthesis(allow)}
                busy={busy}
                predictionModelId={predictionModelId}
              />
            ) : (
              <p className="rp-body">选择 L5Run 与记录以查看真实十字段报告。</p>
            )}
          </main>
        </div>
      )}
    </div>
  )
}
