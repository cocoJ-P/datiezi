import type { EvidenceBundle } from "./api"

type TraceNode = {
  node_id: string
  node_type: string
  feature_name?: string
  value?: number | string
  unit?: string | null
  method?: string
  analyzer_id?: string
  analyzer_version?: string
  rule_id?: string
  rule_version?: string
  assessment?: string
  evidence_id?: string
  related_fault_mode?: string
  conclusion_type?: string
  summary?: string
  limitations?: string[]
  source_reference?: Record<string, unknown>
  condition?: Record<string, unknown>
  parameters?: Record<string, unknown>
  result?: Record<string, unknown>
}

type ReasoningTrace = {
  trace_id?: string
  trace_version?: string
  build_status?: string
  error?: string
  nodes?: TraceNode[]
  edges?: Array<{ from: string; to: string; relation: string }>
  conclusion_node_ids?: string[]
  limitations?: string[]
}

const STEP_ORDER = [
  "OBSERVATION",
  "ANALYSIS",
  "RULE",
  "EVIDENCE",
  "KNOWLEDGE_REFERENCE",
  "TOOL_RESULT",
  "HYPOTHESIS",
  "CONCLUSION",
] as const

const STEP_TITLE: Record<string, string> = {
  OBSERVATION: "关键观测",
  ANALYSIS: "数值与规则分析",
  RULE: "适用规则",
  EVIDENCE: "支持 / 反对证据",
  KNOWLEDGE_REFERENCE: "知识引用（参考）",
  TOOL_RESULT: "工具调用结果",
  HYPOTHESIS: "故障假设（待验证）",
  CONCLUSION: "L3 复核结论",
}

export function ReasoningTraceView({
  evidence,
  l2Label,
}: {
  evidence: EvidenceBundle
  l2Label: string
}) {
  const trace = (evidence.reasoning_trace || null) as ReasoningTrace | null
  if (!trace) {
    return (
      <details className="l3-trace">
        <summary>诊断推理链</summary>
        <p className="l3-note">当前结果尚未包含 reasoning_trace（可能为旧 AnalysisRun）。可重新分析生成。</p>
      </details>
    )
  }

  const nodes = trace.nodes || []
  const byType = (type: string) => nodes.filter((n) => n.node_type === type)

  return (
    <details className="l3-trace" open={false}>
      <summary>
        诊断推理链
        <span className="l3-trace-meta">
          {trace.trace_version || "—"} · {trace.build_status || "—"} · 节点 {nodes.length}
        </span>
      </summary>

      {trace.build_status === "failed" ? (
        <p className="l3-note is-error">推理链构建失败：{trace.error || (trace.limitations || []).join("；")}</p>
      ) : null}

      <ol className="l3-trace-steps">
        <li>
          <header>
            <strong>1. L2 初步判断</strong>
          </header>
          <p>{l2Label}</p>
          <p className="l3-note">L2 结果仅作上下文，不作为独立验证证据写入 Trace 观测。</p>
        </li>

        {STEP_ORDER.map((type, index) => {
          const items = byType(type)
          if (!items.length && type === "RULE") {
            return (
              <li key={type}>
                <header>
                  <strong>
                    {index + 2}. {STEP_TITLE[type]}
                  </strong>
                </header>
                <p className="l3-note">无适用规则节点（未伪造）。</p>
              </li>
            )
          }
          if (!items.length) return null
          return (
            <li key={type}>
              <header>
                <strong>
                  {index + 2}. {STEP_TITLE[type]}
                </strong>
                <span>{items.length}</span>
              </header>
              <ul className="l3-plain">
                {items.slice(0, type === "OBSERVATION" ? 8 : 12).map((node) => (
                  <li key={node.node_id}>
                    <details>
                      <summary>
                        {type === "OBSERVATION"
                          ? `${node.feature_name} = ${String(node.value)}`
                          : type === "ANALYSIS"
                            ? `${node.analyzer_id} · ${node.method || ""}`
                            : type === "RULE"
                              ? `${node.rule_id} (${node.rule_version || "—"})`
                              : type === "EVIDENCE"
                                ? `${node.assessment} · ${node.related_fault_mode || "—"} · ${node.feature_name || ""}`
                                : type === "KNOWLEDGE_REFERENCE"
                                  ? `${String((node as { knowledge_id?: string }).knowledge_id || node.node_id)} · ${String((node as { applicability_status?: string }).applicability_status || "reference")}`
                                  : `${node.assessment} · ${node.conclusion_type || ""}`}
                      </summary>
                      <p className="l3-note">node_id: {node.node_id}</p>
                      {node.analyzer_version ? <p className="l3-note">分析器版本 {node.analyzer_version}</p> : null}
                      {node.summary ? <p>{node.summary}</p> : null}
                      {node.result ? <pre className="l3-mini-json">{JSON.stringify(node.result, null, 2)}</pre> : null}
                      {node.condition ? <pre className="l3-mini-json">{JSON.stringify(node.condition, null, 2)}</pre> : null}
                      {node.parameters ? <pre className="l3-mini-json">{JSON.stringify(node.parameters, null, 2)}</pre> : null}
                      {node.source_reference ? (
                        <pre className="l3-mini-json">{JSON.stringify(node.source_reference, null, 2)}</pre>
                      ) : null}
                      {(node.limitations || []).length ? (
                        <p className="l3-note">限制：{(node.limitations || []).filter(Boolean).join("；")}</p>
                      ) : null}
                    </details>
                  </li>
                ))}
              </ul>
              {type === "OBSERVATION" && items.length > 8 ? (
                <p className="l3-note">其余观测共 {items.length - 8} 项已省略，完整节点见持久化 Trace。</p>
              ) : null}
            </li>
          )
        })}
      </ol>

      {(trace.limitations || []).length ? (
        <p className="l3-note">Trace 限制：{(trace.limitations || []).join("；")}</p>
      ) : null}
      <p className="l3-note">底层保存 DAG（{trace.edges?.length || 0} 条边）；此处为可读步骤视图，非图编辑器。</p>
    </details>
  )
}
