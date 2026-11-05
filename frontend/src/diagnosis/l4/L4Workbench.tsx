import { useCallback, useEffect, useMemo, useState } from "react"
import { useRoute } from "../../routing"
import { listL5Runs } from "../../report/api"
import { getAnalysisRunByDiagnosis } from "../l3/api"
import { consumeL3HandoffFocus, type L3HandoffFocus } from "../overview/l3HandoffFocus"
import { saveL5ReportFocus } from "../overview/l5HandoffFocus"
import {
  createL4ReasoningRun,
  createL4Run,
  getL4ByAnalysis,
  getL4ReasoningByL4,
  getL4Status,
  listL4ReasoningResults,
  listL4Results,
  type CausalReasoningAssessment,
  type L4ReasoningRunSummary,
  type L4RunSummary,
  type RootCauseAssessment,
} from "./api"
import "./l4.css"

function StateBadge({ value }: { value?: string }) {
  return <span className="l4-badge is-warn">{value || "unconfirmed"}</span>
}

function reasoningOutcomeMeta(outcome?: string): { label: string; tone: "ok" | "warn" | "bad" | "muted" } {
  switch (outcome) {
    case "live_llm_success":
      return { label: "AI 推理成功（根因仍未确认）", tone: "ok" }
    case "insufficient_evidence":
      return { label: "推理完成 · 因果证据不足", tone: "warn" }
    case "llm_invalid_json_fallback":
      return { label: "模型输出不合法 · 已回退确定性结果", tone: "bad" }
    case "validation_failed":
      return { label: "模型结果未通过引用/语义校验", tone: "bad" }
    case "llm_failed_fallback":
      return { label: "模型调用失败 · 已回退", tone: "bad" }
    case "dry_run":
      return { label: "干跑（未调用 LLM）", tone: "muted" }
    case "deterministic_fallback":
      return { label: "确定性回退", tone: "warn" }
    default:
      return { label: outcome || "未运行", tone: "muted" }
  }
}

function ReasoningOutcomeBadge({ outcome }: { outcome?: string }) {
  const meta = reasoningOutcomeMeta(outcome)
  const cls =
    meta.tone === "ok"
      ? "l4-badge"
      : meta.tone === "bad"
        ? "l4-badge is-danger"
        : meta.tone === "warn"
          ? "l4-badge is-warn"
          : "l4-badge is-muted"
  return <span className={cls}>{meta.label}</span>
}

function evidenceSupportLabel(cand: Record<string, unknown>): {
  label: string
  tone: "ok" | "warn" | "bad" | "muted"
} {
  const contra = (cand.contradicting_evidence_ids as string[] | undefined) || []
  const support = (cand.supporting_evidence_ids as string[] | undefined) || []
  const manif = (cand.fault_manifestation_evidence_ids as string[] | undefined) || []
  const status = String(cand.assessment_status || "unconfirmed").toLowerCase()
  if (contra.length || status.includes("contradict")) return { label: "contradicted", tone: "bad" }
  if (status.includes("confirm") && !status.includes("unconfirm")) return { label: "confirmed", tone: "ok" }
  if (support.length || manif.length) return { label: "supported", tone: "ok" }
  if (status.includes("insufficient") || !(support.length || manif.length)) {
    return { label: "insufficient_evidence", tone: "warn" }
  }
  return { label: "unconfirmed", tone: "warn" }
}

function CandidateCard({
  cand,
  confirmation,
}: {
  cand: Record<string, unknown>
  confirmation?: string
}) {
  const [open, setOpen] = useState(false)
  const missing = (cand.missing_evidence as string[] | undefined) || []
  const support = (cand.supporting_evidence_ids as string[] | undefined) || []
  const manif = (cand.fault_manifestation_evidence_ids as string[] | undefined) || []
  const contextual = (cand.contextual_evidence_ids as string[] | undefined) || []
  const contra = (cand.contradicting_evidence_ids as string[] | undefined) || []
  const components =
    (cand.involved_components as string[] | undefined) || (cand.components as string[] | undefined) || []
  const ev = evidenceSupportLabel(cand)
  const conf = confirmation || "unconfirmed"
  return (
    <article className="l4-card">
      <header>
        <h3>{String(cand.cause_name || "根因候选")}</h3>
        <span className={`l4-badge ${ev.tone === "ok" ? "" : ev.tone === "bad" ? "is-danger" : "is-warn"}`}>
          {ev.label}
        </span>
      </header>
      <p>
        关联故障 <strong>{String(cand.related_fault_type || "—")}</strong>
        {components.length ? ` · 部件 ${components.slice(0, 4).join(" / ")}` : ""}
      </p>
      <p className="l4-note">
        适用性 {String(cand.applicability_status || "—")} · root_cause_confirmation <strong>{conf}</strong>
        {conf === "confirmed" ? "" : "（未确认，不得当作已确认物理根因）"}
      </p>
      <p className="l4-note">{String(cand.support_summary || "")}</p>
      <p className="l4-note">
        知识 {String(cand.causal_knowledge_id || "—")}
        {cand.causal_knowledge_version ? ` · ${String(cand.causal_knowledge_version)}` : ""}
      </p>
      <button type="button" className="l4-linkish" onClick={() => setOpen((v) => !v)}>
        {open ? "收起证据追溯" : "展开证据追溯"}
      </button>
      {open ? (
        <div className="l4-evidence-panel">
          {support.length ? (
            <>
              <p className="l4-note">根因特异支持（cause_specific）</p>
              <ul className="l4-list-block">
                {support.map((id) => (
                  <li key={id}>{id}</li>
                ))}
              </ul>
            </>
          ) : (
            <p className="l4-note">无 cause_specific 支持 ID</p>
          )}
          {manif.length ? (
            <>
              <p className="l4-note">故障表现证据（振动/温度等观测引用）</p>
              <ul className="l4-list-block">
                {manif.map((id) => (
                  <li key={id}>{id}</li>
                ))}
              </ul>
            </>
          ) : null}
          {contextual.length ? (
            <>
              <p className="l4-note">上下文证据</p>
              <ul className="l4-list-block">
                {contextual.map((id) => (
                  <li key={id}>{id}</li>
                ))}
              </ul>
            </>
          ) : null}
          {contra.length ? (
            <>
              <p className="l4-note">反对证据</p>
              <ul className="l4-list-block">
                {contra.map((id) => (
                  <li key={id}>{id}</li>
                ))}
              </ul>
            </>
          ) : (
            <p className="l4-note">无反对证据 ID</p>
          )}
          {missing.length ? (
            <>
              <p className="l4-note">证据不足 / 未验证条件</p>
              <ul className="l4-list-block">
                {missing.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </>
          ) : null}
        </div>
      ) : null}
    </article>
  )
}

function PathCard({ path }: { path: Record<string, unknown> }) {
  const states = (path.link_states as Record<string, unknown> | undefined) || {}
  const validation = String(path.validation_status || "candidate_mechanism")
  const confirmed = validation === "validated" || validation === "confirmed"
  return (
    <article className="l4-card">
      <header>
        <h3>因果链 {String(path.causal_path_id || "")}</h3>
        <span className="l4-badge is-warn">
          {confirmed ? validation : `候选关联 · ${validation}`}
        </span>
      </header>
      <p className="l4-note">
        {confirmed ? "已验证因果关系（以后端 validation_status 为准）" : "因果关系未确认 · 仅展示 API 返回的候选路径"}
      </p>
      <div className="l4-path" aria-label="异常观测到可能原因">
        <span>
          异常/原因
          <div className="l4-state">{String(path.cause || states.cause || "—")}</div>
        </span>
        <span>→</span>
        <span>
          机理/表现
          <div className="l4-state">{String(path.mechanism || states.mechanism || "—")}</div>
        </span>
        <span>→</span>
        <span>
          故障/影响
          <div className="l4-state">
            {String(path.related_fault_type || path.resulting_fault || states.fault_to_evidence || "—")}
          </div>
        </span>
      </div>
      <p className="l4-note">已观测 / 知识参考 / 假设 / 未知 均标注在环节状态；知识≠本机事实。不绘制虚构节点。</p>
    </article>
  )
}

function ImpactCard({ impact }: { impact: Record<string, unknown> }) {
  const observed = (impact.observed_impact as Record<string, unknown> | undefined) || {}
  const potential = (impact.potential_impact as Record<string, unknown> | undefined) || {}
  const mapping = (impact.equipment_mapping as Record<string, unknown> | undefined) || {}
  const upstream =
    (potential.upstream_dependencies as Array<Record<string, unknown>> | undefined) ||
    (impact.upstream_dependencies as Array<Record<string, unknown>> | undefined) ||
    []
  const downstream =
    (potential.downstream_connections as Array<Record<string, unknown>> | undefined) ||
    (impact.downstream_connections as Array<Record<string, unknown>> | undefined) ||
    []
  const other =
    (potential.other_coupled_connections as Array<Record<string, unknown>> | undefined) ||
    (impact.other_coupled_connections as Array<Record<string, unknown>> | undefined) ||
    []
  const renderNodes = (nodes: Array<Record<string, unknown>>, label: string) =>
    nodes.length ? (
      <>
        <p className="l4-note">{label}</p>
        <ul className="l4-list-block">
          {nodes.slice(0, 12).map((d) => (
            <li key={`${label}-${String(d.graph_node_id)}`}>
              {String(d.graph_label)}（图谱{String(d.relation_direction)} · depth {String(d.depth)}
              {d.reference_topology_only ? " · reference_topology_only" : ""}）
            </li>
          ))}
        </ul>
      </>
    ) : null
  return (
    <article className="l4-card">
      <header>
        <h3>潜在关联范围</h3>
        <span className="l4-badge">propagation: {String(impact.propagation_status || "unknown")}</span>
      </header>
      <p>
        映射 {String(mapping.mapping_level || "—")} · {String(mapping.graph_label || "未绑定图谱节点")}
      </p>
      <p className="l4-note">本机已观测：筛查 {String(observed.screening_result || "—")} · 初判故障 {String(observed.preliminary_fault || "—")}</p>
      {potential.reference_topology_only ? (
        <p className="l4-note">当前为类型级拓扑参考（reference_topology_only），不是具体资产已受影响结论。</p>
      ) : null}
      {renderNodes(upstream, "上游依赖（upstream_dependencies）")}
      {renderNodes(downstream, "下游关联（downstream_connections）")}
      {renderNodes(other, "其他耦合（other_coupled_connections）")}
      {!upstream.length && !downstream.length && !other.length ? (
        <p className="l4-note">{String(potential.reason || "无具体关联设备列表")}</p>
      ) : null}
      <p className="l4-note">图谱 CONNECTS_TO 方向 ≠ 故障传播方向；不输出影响程度等级；他机是否已受影响：无法判断。</p>
    </article>
  )
}

export function L4Workbench({ handoff }: { handoff?: L3HandoffFocus | null }) {
  const { navigate } = useRoute()
  const [focus] = useState(() => handoff ?? consumeL3HandoffFocus())
  const [analysisRunId, setAnalysisRunId] = useState<string | null>(null)
  const [run, setRun] = useState<L4RunSummary | null>(null)
  const [items, setItems] = useState<Array<{ record_id: string; status: string; outcome?: string; assessment?: RootCauseAssessment }>>([])
  const [selectedId, setSelectedId] = useState<string>("")
  const [busy, setBusy] = useState(false)
  const [reasoningBusy, setReasoningBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [moduleNote, setModuleNote] = useState<string>("")
  const [reasoningRun, setReasoningRun] = useState<L4ReasoningRunSummary | null>(null)
  type ReasoningRecordMap = Record<
    string,
    { assessment?: CausalReasoningAssessment; tool_history?: Array<Record<string, unknown>> }
  >
  const [reasoningByRecord, setReasoningByRecord] = useState<ReasoningRecordMap>({})

  const selected = useMemo(() => items.find((i) => i.record_id === selectedId) || items[0], [items, selectedId])
  const assessment = selected?.assessment
  const reasoningAssessment = selectedId ? reasoningByRecord[selectedId]?.assessment : undefined
  const reasoningTools = selectedId ? reasoningByRecord[selectedId]?.tool_history : undefined

  const refreshReasoning = useCallback(async (l4RunId: string) => {
    const existing = await getL4ReasoningByL4(l4RunId)
    if (existing.reasoning_run) {
      setReasoningRun(existing.reasoning_run)
      const results = await listL4ReasoningResults(existing.reasoning_run.reasoning_run_id)
      const map: Record<string, { assessment?: CausalReasoningAssessment; tool_history?: Array<Record<string, unknown>> }> = {}
      for (const item of results.items) {
        map[item.record_id] = { assessment: item.assessment, tool_history: item.tool_history }
      }
      setReasoningByRecord(map)
    } else {
      setReasoningRun(null)
      setReasoningByRecord({})
    }
  }, [])

  const refresh = useCallback(async (arId: string) => {
    const existing = await getL4ByAnalysis(arId)
    if (existing.l4_run) {
      setRun(existing.l4_run)
      const results = await listL4Results(existing.l4_run.l4_run_id)
      setItems(results.items)
      if (!selectedId && results.items[0]) setSelectedId(results.items[0].record_id)
      await refreshReasoning(existing.l4_run.l4_run_id)
    } else {
      setRun(null)
      setItems([])
      setReasoningRun(null)
      setReasoningByRecord({})
    }
  }, [selectedId, refreshReasoning])

  useEffect(() => {
    const controller = new AbortController()
    void (async () => {
      try {
        const st = await getL4Status(controller.signal)
        setModuleNote(String((st.notes as string[] | undefined)?.[0] || "L4 Core v1"))
        const diagnosisId = focus?.diagnosis_run_id
        if (!diagnosisId) return
        const ar = await getAnalysisRunByDiagnosis(diagnosisId, controller.signal)
        const id = ar.analysis_run?.analysis_run_id
        if (!id) {
          setError("尚未找到对应 L3 AnalysisRun。请先完成 L3 分析。")
          return
        }
        setAnalysisRunId(id)
        await refresh(id)
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err))
      }
    })()
    return () => controller.abort()
  }, [focus?.diagnosis_run_id, refresh])

  async function runL4(forceNew = false) {
    if (!analysisRunId) return
    setBusy(true)
    setError(null)
    try {
      const body = await createL4Run({
        analysis_run_id: analysisRunId,
        force_new: forceNew,
        max_records: 20,
        record_ids: focus?.record_ids?.length ? focus.record_ids.slice(0, 20) : undefined,
      })
      setRun(body.l4_run)
      const results = await listL4Results(body.l4_run.l4_run_id)
      setItems(results.items)
      if (results.items[0]) setSelectedId(results.items[0].record_id)
      await refreshReasoning(body.l4_run.l4_run_id)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  async function runL4Reasoning(forceNew = false) {
    if (!run?.l4_run_id) return
    setReasoningBusy(true)
    setError(null)
    try {
      const body = await createL4ReasoningRun({
        l4_run_id: run.l4_run_id,
        force_new: forceNew,
        max_records: 5,
        record_ids: selectedId ? [selectedId] : focus?.record_ids?.slice(0, 5),
        dry_run: false,
      })
      setReasoningRun(body.reasoning_run)
      const results = await listL4ReasoningResults(body.reasoning_run.reasoning_run_id)
      const map: Record<string, { assessment?: CausalReasoningAssessment; tool_history?: Array<Record<string, unknown>> }> = {}
      for (const item of results.items) {
        map[item.record_id] = { assessment: item.assessment, tool_history: item.tool_history }
      }
      setReasoningByRecord(map)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setReasoningBusy(false)
    }
  }

  if (!focus?.diagnosis_run_id) {
    return (
      <div className="l4-shell">
        <p className="l4-empty">尚未从总览选择对象。请先进入 L3 分支完成分析，再打开 L4。</p>
      </div>
    )
  }

  return (
    <div className="l4-shell" aria-label="L4 根因与影响工作台">
      <header className="l4-head">
        <div>
          <p className="l4-kicker">L4 Core v1.2 + Reasoning v1</p>
          <h2>根因候选与潜在关联范围</h2>
          <p>
            DiagnosisRun {focus.diagnosis_run_id}
            {analysisRunId ? ` · AnalysisRun ${analysisRunId}` : ""}
            {run ? ` · L4Run ${run.l4_run_id} · ${run.status}` : ""}
            {reasoningRun ? ` · Reasoning ${reasoningRun.reasoning_run_id} · ${reasoningRun.status}` : ""}
          </p>
          <p className="l4-note">{moduleNote}</p>
        </div>
        <div className="l4-actions">
          <button type="button" disabled={busy || !analysisRunId} onClick={() => void runL4(false)}>
            {busy ? "分析中…" : run ? "刷新/复用 L4" : "运行 L4 Core"}
          </button>
          <button type="button" disabled={busy || !analysisRunId} onClick={() => void runL4(true)}>
            强制新版本
          </button>
          <button type="button" disabled={reasoningBusy || !run} onClick={() => void runL4Reasoning(false)}>
            {reasoningBusy ? "AI 推理中…" : "AI 根因推理"}
          </button>
          <button
            type="button"
            disabled={!selectedId}
            onClick={() => {
              void (async () => {
                try {
                  const page = await listL5Runs(50)
                  const hit =
                    page.items.find((r) => r.l4_run_id && run && r.l4_run_id === run.l4_run_id) ||
                    page.items.find((r) => r.analysis_run_id && analysisRunId && r.analysis_run_id === analysisRunId) ||
                    page.items.find((r) => r.diagnosis_run_id === focus?.diagnosis_run_id)
                  saveL5ReportFocus({
                    l5_run_id: hit?.l5_run_id,
                    record_id: selectedId,
                    diagnosis_run_id: focus?.diagnosis_run_id,
                    analysis_run_id: analysisRunId || undefined,
                    l4_run_id: run?.l4_run_id,
                    equipment_type: focus?.equipment_type,
                    reason: "from_l4_to_l5",
                  })
                  if (hit?.l5_run_id) navigate({ page: "report", runId: hit.l5_run_id })
                  else navigate({ page: "diagnosis", tab: "l5" })
                } catch {
                  navigate({ page: "diagnosis", tab: "l5" })
                }
              })()
            }}
          >
            打开对应 L5 报告
          </button>
        </div>
      </header>

      {error ? <p className="l4-note is-error">{error}</p> : null}

      {run ? (
        <div className="l4-metrics">
          <div>
            <small>选中</small>
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
            <small>失败</small>
            <b>{run.failed_record_count}</b>
          </div>
        </div>
      ) : null}

      <div className="l4-layout">
        <aside className="l4-list">
          {items.length === 0 ? (
            <p className="l4-note" style={{ padding: 12 }}>
              尚无 L4 结果。点击「运行 L4 Core」。
            </p>
          ) : (
            <ul>
              {items.map((item) => (
                <li key={item.record_id}>
                  <button
                    type="button"
                    className={item.record_id === (selected?.record_id || "") ? "is-active" : undefined}
                    onClick={() => setSelectedId(item.record_id)}
                  >
                    <strong>{item.record_id}</strong>
                    <span>
                      {item.outcome || "—"} · {item.status}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </aside>

        <section className="l4-detail">
          {!assessment ? (
            <p className="l4-empty">选择左侧记录查看 RootCauseAssessment。</p>
          ) : (
            <>
              <article className="l4-card">
                <header>
                  <h3>结果摘要</h3>
                  <StateBadge value={String(assessment.root_cause_confirmation || "unconfirmed")} />
                </header>
                <p>
                  outcome <strong>{String(assessment.outcome || "—")}</strong>
                </p>
                <p className="l4-note">任务成功与根因未确认是分离的：本阶段从不输出已确认根因。</p>
                {(assessment.l3_fault_candidates || []).length ? (
                  <ul className="l4-list-block">
                    {(assessment.l3_fault_candidates || []).map((f, idx) => (
                      <li key={idx}>
                        L3 候选 {String(f.fault_type)}（{String(f.source)} / {String(f.verdict || "—")}）
                      </li>
                    ))}
                  </ul>
                ) : null}
              </article>

              {(assessment.root_cause_candidates || []).map((cand) => (
                <CandidateCard
                  key={String(cand.candidate_id)}
                  cand={cand}
                  confirmation={String(assessment.root_cause_confirmation || "unconfirmed")}
                />
              ))}
              {(assessment.root_cause_candidates || []).length === 0 ? (
                <article className="l4-card">
                  <header>
                    <h3>根因候选</h3>
                  </header>
                  <p className="l4-note">空列表（无适用因果知识、故障被资格过滤或仅有参考项）。{(assessment.notes || []).join("；")}</p>
                </article>
              ) : null}

              {((assessment as Record<string, unknown>).excluded_faults as Array<Record<string, unknown>> | undefined)?.length ? (
                <article className="l4-card">
                  <header>
                    <h3>已排除故障（不展开有效根因）</h3>
                  </header>
                  <ul className="l4-list-block">
                    {((assessment as Record<string, unknown>).excluded_faults as Array<Record<string, unknown>>).map((ex, idx) => (
                      <li key={idx}>
                        {String(ex.fault_type)} · {String(ex.eligibility)} · {String(ex.reason || "")}
                      </li>
                    ))}
                  </ul>
                </article>
              ) : null}

              {((assessment as Record<string, unknown>).reference_candidates as Array<Record<string, unknown>> | undefined)?.length ? (
                <article className="l4-card">
                  <header>
                    <h3>仅供参考（reference_only / unresolved）</h3>
                  </header>
                  <ul className="l4-list-block">
                    {((assessment as Record<string, unknown>).reference_candidates as Array<Record<string, unknown>>).map((rc) => (
                      <li key={String(rc.candidate_id)}>
                        {String(rc.cause_name)} · {String(rc.applicability_status)} · {String(rc.causal_knowledge_id)}
                      </li>
                    ))}
                  </ul>
                </article>
              ) : null}

              {(assessment.causal_paths || []).map((path) => (
                <PathCard key={String(path.causal_path_id)} path={path} />
              ))}

              {assessment.impact_assessment ? <ImpactCard impact={assessment.impact_assessment} /> : null}

              {reasoningAssessment ? (
                <article className="l4-card">
                  <header>
                    <h3>AI 根因推理</h3>
                    <StateBadge value={String(reasoningAssessment.root_cause_confirmation || "unconfirmed")} />
                  </header>
                  <p>
                    <ReasoningOutcomeBadge outcome={String(reasoningAssessment.outcome || "")} />
                    <span className="l4-note"> · code {String(reasoningAssessment.outcome || "—")}</span>
                    {reasoningAssessment.audit ? (
                      <>
                        {" "}
                        · tokens {String((reasoningAssessment.audit as Record<string, unknown>).total_tokens || 0)} ·
                        tools {String((reasoningAssessment.audit as Record<string, unknown>).tool_call_count || 0)}
                        {(reasoningAssessment.audit as Record<string, unknown>).cache_hits != null ? (
                          <> · cache {String((reasoningAssessment.audit as Record<string, unknown>).cache_hits)}</>
                        ) : null}
                      </>
                    ) : null}
                  </p>
                  {String(reasoningAssessment.outcome || "").includes("fallback") ||
                  reasoningAssessment.outcome === "validation_failed" ||
                  reasoningAssessment.outcome === "llm_failed_fallback" ? (
                    <p className="l4-note">
                      当前展示为回退/校验失败结果，不是完整 AI 根因推理成功。请区分「证据不足」与「模型执行失败」。
                    </p>
                  ) : null}
                  <p className="l4-note">{String(reasoningAssessment.overall_assessment || "")}</p>
                  <p className="l4-note">任务成功 ≠ 根因已确认。AI 不覆盖 L4 Core 候选。</p>
                  {(reasoningAssessment.candidate_reviews || []).map((rev, idx) => (
                    <div key={idx} className="l4-note" style={{ marginTop: 8 }}>
                      <strong>{String(rev.cause_name || rev.candidate_id)}</strong> · {String(rev.validity_status || "—")}
                      <div>{String(rev.review_summary || "")}</div>
                      {(rev.alternative_explanations as string[] | undefined)?.length ? (
                        <div>替代解释：{(rev.alternative_explanations as string[]).join("；")}</div>
                      ) : null}
                      {(rev.unresolved_links as string[] | undefined)?.length ? (
                        <div>未验证：{(rev.unresolved_links as string[]).slice(0, 4).join("；")}</div>
                      ) : null}
                    </div>
                  ))}
                  {(reasoningAssessment.excluded_review_recommendations || []).length ? (
                    <>
                      <p className="l4-note">排除项复核建议（未自动恢复）</p>
                      <ul className="l4-list-block">
                        {(reasoningAssessment.excluded_review_recommendations || []).map((ex, idx) => (
                          <li key={idx}>
                            {String(ex.fault_type)} · {String(ex.action)} · {String(ex.exclusion_reason || "")}
                          </li>
                        ))}
                      </ul>
                    </>
                  ) : null}
                  {reasoningAssessment.impact_interpretation ? (
                    <p className="l4-note">
                      影响解读：propagation{" "}
                      {String((reasoningAssessment.impact_interpretation as Record<string, unknown>).propagation_status)}{" "}
                      · impact_degree null
                    </p>
                  ) : null}
                  {reasoningTools?.length ? (
                    <>
                      <p className="l4-note">工具调用</p>
                      <ul className="l4-list-block">
                        {reasoningTools.slice(0, 12).map((t, idx) => (
                          <li key={idx}>
                            {String(t.tool)} · {t.result_ok === false ? "失败" : "成功"}
                          </li>
                        ))}
                      </ul>
                    </>
                  ) : null}
                  {(reasoningAssessment.limitations || []).length ? (
                    <ul className="l4-list-block">
                      {(reasoningAssessment.limitations || []).slice(0, 6).map((item) => (
                        <li key={item}>{item}</li>
                      ))}
                    </ul>
                  ) : null}
                </article>
              ) : run ? (
                <article className="l4-card">
                  <header>
                    <h3>AI 根因推理</h3>
                  </header>
                  <p className="l4-note">尚未运行。选择记录后点击「AI 根因推理」（按需、限量）。</p>
                </article>
              ) : null}

              <article className="l4-card">
                <header>
                  <h3>Causal Trace</h3>
                  <span className="l4-badge">{assessment.causal_trace?.acyclic === false ? "有环异常" : "无环"}</span>
                </header>
                <p className="l4-note">
                  节点 {(assessment.causal_trace?.nodes || []).length} · 边 {(assessment.causal_trace?.edges || []).length}
                  ；覆盖层不修改 L3 Trace。
                </p>
                <ul className="l4-list-block">
                  {(assessment.causal_trace?.nodes || []).slice(0, 12).map((n) => (
                    <li key={String(n.node_id)}>
                      {String(n.node_type)} · {String(n.label)} · {String(n.state)}
                      {n.fact_claim ? " · 观测引用" : " · 非事实宣称"}
                    </li>
                  ))}
                </ul>
              </article>

              {(assessment.missing_evidence || []).length ? (
                <article className="l4-card">
                  <header>
                    <h3>全局缺失条件</h3>
                  </header>
                  <ul className="l4-list-block">
                    {(assessment.missing_evidence || []).map((item) => (
                      <li key={item}>{item}</li>
                    ))}
                  </ul>
                </article>
              ) : null}
            </>
          )}
        </section>
      </div>
    </div>
  )
}
