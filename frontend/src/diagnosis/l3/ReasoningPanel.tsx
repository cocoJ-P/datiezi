import { useEffect, useState } from "react"
import {
  createReasoningRun,
  getReasoningByAnalysis,
  getReasoningRecord,
  getReasoningStatus,
  type ReasoningRecord,
  type ReasoningRunSummary,
} from "./reasoningApi"

function statusLabel(modeOrStatus?: string | null) {
  switch (modeOrStatus) {
    case "live_llm_success":
    case "succeeded":
      return "在线推理成功"
    case "insufficient_evidence":
      return "推理成功但证据不足"
    case "llm_invalid_json_fallback":
      return "模型输出不合法（已回退 Core）"
    case "llm_failed_fallback":
    case "failed":
      return "模型调用失败（已回退 Core）"
    case "dry_run":
      return "离线测试（dry-run）"
    case "validation_failed":
      return "校验未通过"
    case "insufficient":
      // 兼容旧结果：旧版曾把 JSON 失败误标为 insufficient
      return "旧状态：证据不足/回退（请查看 mode）"
    case "running":
    case "queued":
      return "AI 推理进行中"
    default:
      return "AI 推理未执行"
  }
}

export function ReasoningPanel({
  analysisRunId,
  recordId,
  screeningResult,
  preliminaryFault,
}: {
  analysisRunId?: string | null
  recordId?: string | null
  screeningResult?: string
  preliminaryFault?: string
}) {
  const [run, setRun] = useState<ReasoningRunSummary | null>(null)
  const [record, setRecord] = useState<ReasoningRecord | null>(null)
  const [error, setError] = useState("")
  const [busy, setBusy] = useState(false)
  const [tools, setTools] = useState<string[]>([])

  useEffect(() => {
    getReasoningStatus()
      .then((body) => setTools(body.tools || []))
      .catch(() => undefined)
  }, [])

  useEffect(() => {
    if (!analysisRunId) {
      setRun(null)
      setRecord(null)
      return
    }
    let cancelled = false
    async function load() {
      try {
        const by = await getReasoningByAnalysis(analysisRunId!)
        if (cancelled) return
        setRun(by.reasoning_run)
        if (by.reasoning_run && recordId) {
          try {
            const row = await getReasoningRecord(by.reasoning_run.reasoning_run_id, recordId)
            if (!cancelled) setRecord(row)
          } catch {
            if (!cancelled) setRecord(null)
          }
        } else if (!cancelled) {
          setRecord(null)
        }
      } catch {
        if (!cancelled) {
          setRun(null)
          setRecord(null)
        }
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [analysisRunId, recordId])

  async function runDeep(dryRun: boolean) {
    if (!analysisRunId || !recordId) return
    setBusy(true)
    setError("")
    try {
      const created = await createReasoningRun({
        analysis_run_id: analysisRunId,
        record_ids: [recordId],
        manual_request: true,
        force_new: true,
        dry_run: dryRun,
      })
      setRun(created)
      const row = await getReasoningRecord(created.reasoning_run_id, recordId)
      setRecord(row)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const assessment = record?.assessment
  const hypotheses = assessment?.candidate_hypotheses || []
  const hypNodes = (record?.trace_overlay?.nodes || []).filter((n) => n.node_type === "HYPOTHESIS")
  const toolNodes = (record?.trace_overlay?.nodes || []).filter((n) => n.node_type === "TOOL_RESULT")

  return (
    <details className="l3-trace" open>
      <summary>
        AI 深度推理
        <span className="l3-trace-meta">
          {statusLabel(record?.mode || record?.status || run?.status)} · {run?.reasoning_run_id || "无 ReasoningRun"}
        </span>
      </summary>

      <p className="l3-note">
        L2 初判：{screeningResult || "—"} / {preliminaryFault || "—"} · Core 与 Knowledge 独立保存；本区不覆盖数值证据。
      </p>
      <p className="l3-note">白名单工具：{tools.join(" · ") || "加载中…"}</p>
      <p className="l3-note">假设 SUPPORTED ≠ 确诊；禁止根因/维修/优先级/影响程度最终结论。</p>

      {error ? <p className="l3-note is-error">{error}</p> : null}

      <div className="l3-actions" style={{ marginTop: 8 }}>
        <button type="button" disabled={busy || !analysisRunId || !recordId} onClick={() => void runDeep(false)}>
          {busy ? "推理中…" : "AI 深入分析"}
        </button>
        <button type="button" disabled={busy || !analysisRunId || !recordId} onClick={() => void runDeep(true)}>
          离线对齐（dry-run）
        </button>
      </div>

      {!assessment ? (
        <p className="l3-note">
          当前记录尚未执行 AI 推理。CONFLICT/UNCERTAIN 等可自动入队；其余 ABNORMAL 可点上方按钮手动深入分析。
        </p>
      ) : (
        <>
          <article className="l3-card" style={{ marginTop: 10 }}>
            <header>
              <h3>推理结论</h3>
              <span>{assessment.status}</span>
            </header>
            <p>{assessment.explanation || "—"}</p>
            <p className="l3-note">
              证据充分性：{assessment.evidence_sufficiency || "—"} · outcome：
              {record?.mode || assessment.status || "—"} · 模型：{assessment.llm_model || run?.llm_model || "—"}
            </p>
            {assessment.audit ? (
              <p className="l3-note">
                审计：tokens={String((assessment.audit as { total_tokens?: number | null }).total_tokens ?? "null")} · llm_calls=
                {String((assessment.audit as { llm_call_count?: number }).llm_call_count ?? 0)} · tools=
                {String((assessment.audit as { tool_call_count?: number }).tool_call_count ?? 0)} · duration=
                {String((assessment.audit as { execution_duration?: number | null }).execution_duration ?? "—")}s · finish=
                {String((assessment.audit as { finish_reason?: string | null }).finish_reason ?? "—")}
              </p>
            ) : null}
            {(assessment.validation?.ok === false && (assessment.validation.errors || []).length > 0) ? (
              <p className="l3-note is-error">校验：{(assessment.validation.errors || []).join("；")}</p>
            ) : null}
          </article>

          <article className="l3-card">
            <header>
              <h3>竞争性故障假设</h3>
              <span>{hypotheses.length}</span>
            </header>
            <ul className="l3-plain">
              {hypotheses.map((h) => (
                <li key={String(h.hypothesis_id || h.fault_type)}>
                  <strong>{h.fault_type}</strong> · {h.assessment} · 来源 {h.hypothesis_origin}
                  <div className="l3-note">
                    支持 {(h.supporting_evidence_ids || []).join(", ") || "无"} · 反对{" "}
                    {(h.contradicting_evidence_ids || []).join(", ") || "无"}
                  </div>
                  <div className="l3-note">
                    知识 {(h.knowledge_reference_ids || []).join(", ") || "无"} · 缺失{" "}
                    {(h.missing_evidence || []).join(" · ") || "无"}
                  </div>
                </li>
              ))}
              {hypotheses.length === 0 ? <li className="l3-note">无结构化假设</li> : null}
            </ul>
          </article>

          <article className="l3-card">
            <header>
              <h3>工具调用与溯源</h3>
              <span>{(record?.tool_history || []).length}</span>
            </header>
            <ul className="l3-plain">
              {(record?.tool_history || []).map((t, idx) => (
                <li key={`${t.tool}-${idx}`}>
                  {t.ok === false ? "失败" : "成功"} · {t.tool}
                </li>
              ))}
              {(record?.tool_history || []).length === 0 ? <li className="l3-note">无工具记录</li> : null}
            </ul>
            <p className="l3-note">
              Trace 覆盖：HYPOTHESIS {hypNodes.length} · TOOL_RESULT {toolNodes.length} ·{" "}
              {record?.trace_overlay?.build_status || "—"}
            </p>
          </article>

          {(assessment.missing_evidence || []).length || (assessment.limitations || []).length ? (
            <article className="l3-card">
              <header>
                <h3>缺失与限制</h3>
              </header>
              <p className="l3-note">缺失：{(assessment.missing_evidence || []).join(" · ") || "—"}</p>
              <p className="l3-note">限制：{(assessment.limitations || []).join(" · ") || "—"}</p>
            </article>
          ) : null}
        </>
      )}
    </details>
  )
}
