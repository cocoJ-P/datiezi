import { useCallback, useEffect, useMemo, useState } from "react"
import { useRoute } from "../routing"
import { consumeL5ReportFocus } from "../diagnosis/overview/l5HandoffFocus"
import {
  getDiagnosisRoutingSummary,
  type DiagnosisRoutingSummary,
} from "../diagnosis/l2/api"
import { getAnalysisRun, type AnalysisRunSummary } from "../diagnosis/l3/api"
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

function fieldOf(assessment: FinalDiagnosisAssessment, key: string): string {
  return formatSlotValue(assessment.fields?.[key])
}

function pct(part: number, total: number): string {
  if (!total) return "—"
  return `${((part / total) * 100).toFixed(1)}%`
}

function statusTone(statusText: string): "ok" | "warn" | "bad" | "neutral" {
  const s = statusText.toLowerCase()
  if (!statusText || statusText === "—") return "neutral"
  if (/(正常|normal|ok|healthy)/i.test(s)) return "ok"
  if (/(严重|critical|alarm|fault|异常|abnormal|重度)/i.test(s)) return "bad"
  if (/(注意|warning|uncertain|conflict|中度|轻度|待|unconfirmed)/i.test(s)) return "warn"
  return "neutral"
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
  const [showTech, setShowTech] = useState(false)
  const [l2Summary, setL2Summary] = useState<DiagnosisRoutingSummary | null>(null)
  const [l3Run, setL3Run] = useState<AnalysisRunSummary | null>(null)

  const equipmentStatus = fieldOf(assessment, "equipment_status")
  const faultType = fieldOf(assessment, "fault_type")
  const faultSeverity = fieldOf(assessment, "fault_severity")
  const faultLocation = fieldOf(assessment, "fault_location")
  const confidence = fieldOf(assessment, "diagnosis_confidence")
  const spectral = fieldOf(assessment, "spectral_description")
  const rootCause = fieldOf(assessment, "root_cause_analysis")
  const actions = fieldOf(assessment, "recommended_actions")
  const priority = fieldOf(assessment, "priority")
  const impact = fieldOf(assessment, "impact_degree")
  const tone = statusTone(`${equipmentStatus} ${faultSeverity} ${faultType}`)

  useEffect(() => {
    let cancelled = false
    const diagnosisId = assessment.source_run_ids?.diagnosis_run_id
    const analysisId = assessment.source_run_ids?.analysis_run_id
    setL2Summary(null)
    setL3Run(null)
    ;(async () => {
      if (diagnosisId) {
        try {
          const body = await getDiagnosisRoutingSummary(diagnosisId)
          if (!cancelled) setL2Summary(body)
        } catch {
          if (!cancelled) setL2Summary(null)
        }
      }
      if (analysisId) {
        try {
          const body = await getAnalysisRun(analysisId)
          if (!cancelled) setL3Run(body)
        } catch {
          if (!cancelled) setL3Run(null)
        }
      }
    })()
    return () => {
      cancelled = true
    }
  }, [assessment.source_run_ids?.diagnosis_run_id, assessment.source_run_ids?.analysis_run_id])

  const l2Group = useMemo(() => {
    if (!l2Summary?.groups?.length) return null
    const byEquip = l2Summary.groups.find((g) => String(g.equipment_id || "") === String(assessment.equipment_id || ""))
    return byEquip || l2Summary.groups[0]
  }, [l2Summary, assessment.equipment_id])

  const l2Total = l2Group?.total_records || l2Summary?.total_records || 0
  const l2Complete = l2Group?.l2_complete_count ?? l2Summary?.l2_complete_count ?? 0
  const l2Abnormal = l2Group?.abnormal_count ?? l2Summary?.screening_counts?.ABNORMAL ?? 0
  const l2Conflict = l2Group?.conflict_count ?? l2Summary?.screening_counts?.CONFLICT ?? 0
  const l2Uncertain = l2Group?.uncertain_count ?? l2Summary?.screening_counts?.UNCERTAIN ?? 0
  const l2Normal = l2Group?.normal_count ?? Math.max(0, l2Total - l2Abnormal - l2Conflict - l2Uncertain)
  const l3Review = l2Group?.l3_review_count ?? l2Summary?.l3_review_count ?? 0

  const l3Selected = l3Run?.selected_record_count || 0
  const l3Succeeded = l3Run?.succeeded_record_count || 0
  const l3Failed = l3Run?.failed_record_count || 0
  const l3Inconclusive = l3Run?.inconclusive_record_count || 0
  const l3Processed = l3Run?.processed_record_count || 0

  return (
    <article className="rp-doc">
      <header className="rp-doc__header">
        <div className="rp-doc__meta-row">
          <span className={`rp-status-pill is-${tone}`}>
            {tone === "ok" ? "状态正常倾向" : tone === "bad" ? "需重点关注" : tone === "warn" ? "建议复核" : "结果待解读"}
          </span>
          {reviewBadge(assessment.review_status)}
        </div>
        <h1 className="rp-doc__title">设备诊断结论</h1>
        <p className="rp-body">
          面向现场的摘要结论。技术字段、证据链与模型细节默认收在下方二级。
        </p>
      </header>

      <section className="rp-section">
        <h2>涉及设备</h2>
        <div className="rp-customer-hero">
          <div>
            <span className="rp-label">设备类型</span>
            <strong>{assessment.equipment_type || "—"}</strong>
          </div>
          <div>
            <span className="rp-label">设备编号</span>
            <strong>{assessment.equipment_id || "—"}</strong>
          </div>
          <div>
            <span className="rp-label">设备状态</span>
            <strong className={`rp-emph is-${tone}`}>{equipmentStatus}</strong>
          </div>
        </div>
      </section>

      <section className="rp-section">
        <h2>最终检出问题</h2>
        <div className="rp-customer-findings">
          <div>
            <span className="rp-label">故障类型</span>
            <p>{faultType}</p>
          </div>
          <div>
            <span className="rp-label">严重程度</span>
            <p>{faultSeverity}</p>
          </div>
          <div>
            <span className="rp-label">可能部位</span>
            <p>{faultLocation}</p>
          </div>
          <div>
            <span className="rp-label">诊断置信度</span>
            <p>{confidence}</p>
          </div>
          <div className="is-wide">
            <span className="rp-label">频谱/现象描述</span>
            <p>{spectral}</p>
          </div>
          <div className="is-wide">
            <span className="rp-label">根因分析结论</span>
            <p>{rootCause}</p>
            <p className="rp-inline-note">
              根因确认状态：
              {assessment.root_cause_confirmation === "confirmed" ? "已确认" : "未确认（需人工复核）"}
            </p>
          </div>
          <div className="is-wide">
            <span className="rp-label">建议措施</span>
            <p>{actions}</p>
          </div>
          <div>
            <span className="rp-label">优先级</span>
            <p>{priority}</p>
          </div>
          <div>
            <span className="rp-label">影响程度</span>
            <p>{impact}</p>
          </div>
        </div>
      </section>

      <section className="rp-section">
        <h2>筛查与深度分析比率</h2>
        <div className="rp-ratio-grid">
          <div className="rp-ratio-card">
            <h3>L2 设备筛查</h3>
            {l2Summary ? (
              <>
                <p className="rp-body">
                  范围：{l2Group?.display_name || l2Summary.display_name} · 共 {l2Total} 条
                </p>
                <ul className="rp-ratio-list">
                  <li>
                    <span>初判正常</span>
                    <b>
                      {l2Normal}（{pct(l2Normal, l2Total)}）
                    </b>
                  </li>
                  <li>
                    <span>初判异常</span>
                    <b>
                      {l2Abnormal}（{pct(l2Abnormal, l2Total)}）
                    </b>
                  </li>
                  <li>
                    <span>冲突</span>
                    <b>
                      {l2Conflict}（{pct(l2Conflict, l2Total)}）
                    </b>
                  </li>
                  <li>
                    <span>不确定</span>
                    <b>
                      {l2Uncertain}（{pct(l2Uncertain, l2Total)}）
                    </b>
                  </li>
                  <li>
                    <span>L2 完成留存</span>
                    <b>
                      {l2Complete}（{pct(l2Complete, l2Total)}）
                    </b>
                  </li>
                  <li>
                    <span>建议进入 L3</span>
                    <b>
                      {l3Review}（{pct(l3Review, l2Total)}）
                    </b>
                  </li>
                </ul>
              </>
            ) : (
              <p className="rp-body">未绑定 L2 DiagnosisRun，暂无筛查比率。</p>
            )}
          </div>
          <div className="rp-ratio-card">
            <h3>L3 深度分析</h3>
            {l3Run ? (
              <>
                <p className="rp-body">
                  状态：
                  {l3Run.status === "succeeded"
                    ? "分析完成"
                    : l3Run.status === "partial"
                      ? "部分完成"
                      : l3Run.status}
                  · 已处理 {l3Processed}/{l3Selected}
                </p>
                <ul className="rp-ratio-list">
                  <li>
                    <span>成功判定</span>
                    <b>
                      {l3Succeeded}（{pct(l3Succeeded, l3Selected || l3Processed)}）
                    </b>
                  </li>
                  <li>
                    <span>失败</span>
                    <b>
                      {l3Failed}（{pct(l3Failed, l3Selected || l3Processed)}）
                    </b>
                  </li>
                  <li>
                    <span>不可判定</span>
                    <b>
                      {l3Inconclusive}（{pct(l3Inconclusive, l3Selected || l3Processed)}）
                    </b>
                  </li>
                  <li>
                    <span>处理完成率</span>
                    <b>{pct(l3Processed, l3Selected)}</b>
                  </li>
                </ul>
              </>
            ) : (
              <p className="rp-body">未绑定 L3 AnalysisRun，暂无深度分析比率。</p>
            )}
          </div>
        </div>
      </section>

      <section className="rp-section">
        <div className="rp-tech-toggle">
          <button type="button" className="rp-tech-toggle__btn" onClick={() => setShowTech((v) => !v)}>
            {showTech ? "收起技术详情（二级）" : "展开技术详情（二级）"}
          </button>
          <span className="rp-inline-note">十字段原始字段、证据追溯、合成对照、规程警报</span>
        </div>
        {showTech ? (
          <div className="rp-tech-panel">
            <section className="rp-section">
              <h2>十字段原始输出</h2>
              <p className="rp-body">
                主值来自 L5 监督预测；诊断置信度为数据集回归目标，不等于已校准正确概率。根因模板 ≠ 现场已确认根因。
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
                        来源：{slot?.source_layer || "—"} · 模型{" "}
                        {String(extras.source_model_id || predMeta?.model_id || predictionModelId || "—")} · 状态{" "}
                        {String(extras.prediction_status || "—")}
                      </p>
                      {slot?.uncertainty ? <p className="rp-inline-note">不确定性：{slot.uncertainty}</p> : null}
                      <button type="button" className="rp-ghost-btn" onClick={() => onTrace(key)}>
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
              <h2>评测与追溯 ID</h2>
              <p className="rp-mono">
                fingerprint {predMeta?.model_input_fingerprint || "—"} · L2{" "}
                {assessment.source_run_ids?.diagnosis_run_id || "—"} · L3{" "}
                {assessment.source_run_ids?.analysis_run_id || "—"} · L4{" "}
                {assessment.source_run_ids?.l4_run_id || "—"} · L5 {assessment.l5_run_id || "—"}
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
              <h2>局限性</h2>
              <ul className="rp-list">
                {(assessment.limitations || []).map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </section>
          </div>
        ) : null}
      </section>

      <footer className="rp-doc__footer">记录 {assessment.record_id} · 客户向一级结论</footer>
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
  const { route } = useRoute()
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
  const [pendingRecordId, setPendingRecordId] = useState<string | null>(null)

  useEffect(() => {
    const focus = consumeL5ReportFocus()
    if (focus?.l5_run_id) {
      setMode("live")
      setSelectedRunId(focus.l5_run_id)
      if (focus.record_id) setPendingRecordId(focus.record_id)
    } else if (route.runId) {
      setMode("live")
      setSelectedRunId(route.runId)
    }
  }, [route.runId])

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
        const prefer =
          pendingRecordId && page.items?.some((r) => r.record_id === pendingRecordId)
            ? pendingRecordId
            : page.items?.[0]?.record_id || ""
        setSelectedRecordId(prefer)
        setPendingRecordId(null)
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
  }, [mode, selectedRunId, pendingRecordId])

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
          <p className="rp-page__sub">一级：设备问题结论与 L2/L3 比率 · 二级：技术证据与模型细节</p>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <button type="button" onClick={() => setMode("live")} disabled={mode === "live"}>
            正式报告
          </button>
          <button type="button" onClick={() => setMode("demo")} disabled={mode === "demo"}>
            演示样例
          </button>
          {mode === "live" ? (
            <>
              <button type="button" onClick={() => void loadRuns()} disabled={busy}>
                刷新
              </button>
              <button type="button" onClick={() => void onRunAlerts()} disabled={busy || !selectedRunId}>
                运行规程评估
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
            <div className="rp-sidebar__head">诊断批次 {busy ? "· 加载中" : ""}</div>
            <ul className="rp-sidebar__list">
              {runs.length ? (
                runs.map((r) => (
                  <li key={r.l5_run_id}>
                    <button
                      type="button"
                      className={`rp-sidebar__item${selectedRunId === r.l5_run_id ? " is-active" : ""}`}
                      onClick={() => setSelectedRunId(r.l5_run_id)}
                    >
                      <div className="rp-sidebar__item-title">
                        {r.status === "completed" ? "已完成批次" : r.status === "partial" ? "部分完成批次" : "诊断批次"}
                      </div>
                      <div className="rp-sidebar__item-meta">
                        <span>{(r.created_at || "").slice(0, 16).replace("T", " ")}</span>
                        <span>{r.selected_record_count || 0} 台/条</span>
                      </div>
                      <div className="rp-sidebar__item-tags">
                        <span className="rp-badge rp-badge--draft">{r.l5_run_id.slice(0, 12)}…</span>
                      </div>
                    </button>
                  </li>
                ))
              ) : (
                <li>
                  <p className="rp-body" style={{ padding: 12 }}>
                    暂无正式报告。请先在智能诊断工作台完成 L2→L5 流程。
                  </p>
                </li>
              )}
            </ul>
            {selectedRunId ? (
              <>
                <div className="rp-sidebar__head">设备记录</div>
                <ul className="rp-sidebar__list">
                  {records.map((rec) => {
                    const a = rec.assessment
                    const fault = formatSlotValue(a?.fields?.fault_type)
                    const status = formatSlotValue(a?.fields?.equipment_status)
                    return (
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
                          <div className="rp-sidebar__item-title">
                            {a?.equipment_id || rec.record_id}
                          </div>
                          <div className="rp-sidebar__item-meta">
                            <span>{a?.equipment_type || "设备"}</span>
                            {reviewBadge(rec.review_status)}
                          </div>
                          <div className="rp-sidebar__item-tags">
                            <span className="rp-badge rp-badge--draft">{status}</span>
                            {fault !== "—" ? <span className="rp-badge rp-badge--archived">{fault}</span> : null}
                          </div>
                        </button>
                      </li>
                    )
                  })}
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
              <p className="rp-body">请选择诊断批次与设备记录，查看客户向结论报告。</p>
            )}
          </main>
        </div>
      )}
    </div>
  )
}
