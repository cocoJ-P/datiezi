import { useEffect, useState } from "react"
import {
  createEnrichment,
  ensureKnowledgeSnapshot,
  getEnrichmentByAnalysis,
  getEnrichmentRecord,
  getKnowledgeStatus,
  type EnrichmentRecord,
  type KnowledgeStatus,
} from "./knowledgeApi"

export function KnowledgePanel({
  analysisRunId,
  recordId,
  equipmentType,
}: {
  analysisRunId?: string | null
  recordId?: string | null
  equipmentType?: string
}) {
  const [status, setStatus] = useState<KnowledgeStatus | null>(null)
  const [enrichment, setEnrichment] = useState<EnrichmentRecord | null>(null)
  const [error, setError] = useState("")
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let cancelled = false
    getKnowledgeStatus()
      .then((body) => {
        if (!cancelled) setStatus(body)
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err))
      })
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    if (!analysisRunId || !recordId) {
      setEnrichment(null)
      return
    }
    let cancelled = false
    async function load() {
      try {
        const by = await getEnrichmentByAnalysis(analysisRunId!)
        if (!by.enrichment_run) {
          if (!cancelled) setEnrichment(null)
          return
        }
        const row = await getEnrichmentRecord(by.enrichment_run.enrichment_run_id, recordId!)
        if (!cancelled) setEnrichment(row)
      } catch {
        if (!cancelled) setEnrichment(null)
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [analysisRunId, recordId])

  async function runEnrich() {
    if (!analysisRunId || !recordId) return
    setBusy(true)
    setError("")
    try {
      if (!status?.knowledge_snapshot_id) {
        await ensureKnowledgeSnapshot()
        setStatus(await getKnowledgeStatus())
      }
      const run = await createEnrichment({
        analysis_run_id: analysisRunId,
        record_ids: [recordId],
        limit: 1,
        force_new: true,
      })
      const row = await getEnrichmentRecord(run.enrichment_run_id, recordId)
      setEnrichment(row)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const ctx = enrichment?.context
  const knowNodes = (enrichment?.trace_overlay?.nodes || []).filter((n) => n.node_type === "KNOWLEDGE_REFERENCE")

  return (
    <details className="l3-trace" open>
      <summary>
        知识依据
        <span className="l3-trace-meta">
          {status?.knowledge_snapshot_id || "未建快照"} · 条目 {status?.item_count ?? "—"}
        </span>
      </summary>

      {error ? <p className="l3-note is-error">{error}</p> : null}

      <p className="l3-note">
        图谱 {status?.graph?.available ? `${status.graph.entity_count} 实体 / ${status.graph.relationship_count} 关系` : "不可用"}
        {" · "}
        BM25 {status?.bm25_index?.available ? "可用" : "检查中"}
        {" · "}
        向量 {status?.vector_index?.backend || "—"}（{status?.vector_index?.note || "可降级"}）
      </p>
      <p className="l3-note">知识检索相似度 ≠ 诊断置信度；案例与规程引用不是当前设备已确认根因。</p>

      <div className="l3-actions" style={{ marginTop: 8 }}>
        <button type="button" disabled={busy || !analysisRunId || !recordId} onClick={() => void runEnrich()}>
          {busy ? "关联中…" : "关联当前记录知识"}
        </button>
      </div>

      {!ctx ? (
        <p className="l3-note">尚未对该记录做知识增强。点击上方按钮生成 KnowledgeEnrichment（不覆盖数值 EvidenceBundle）。</p>
      ) : (
        <>
          <article className="l3-card" style={{ marginTop: 10 }}>
            <header>
              <h3>设备与故障知识</h3>
            </header>
            <p>候选故障：{(ctx.fault_candidates || []).join(" · ") || "—"}</p>
            <p className="l3-note">
              关联部件：
              {(ctx.related_components?.components || [])
                .slice(0, 8)
                .map((c) => c.canonical_name)
                .filter(Boolean)
                .join(" · ") || "无匹配"}
            </p>
            <ul className="l3-plain">
              {(ctx.applicable_knowledge || []).slice(0, 6).map((hit) => (
                <li key={hit.knowledge_id}>
                  <strong>{hit.canonical_name}</strong>（{hit.knowledge_type} · {hit.applicability_status}）
                  <div className="l3-note">{hit.content_summary}</div>
                  <div className="l3-note">
                    {hit.source_document_id} · {String((hit.source_location as { locator?: string } | undefined)?.locator || "")}
                  </div>
                </li>
              ))}
              {(ctx.applicable_knowledge || []).length === 0 ? <li className="l3-note">no_relevant_knowledge</li> : null}
            </ul>
          </article>

          <article className="l3-card">
            <header>
              <h3>相关规程与专家案例</h3>
            </header>
            <p className="l3-note">
              规程参数：
              {(ctx.diagnostic_rules?.parameters || [])
                .slice(0, 6)
                .map((p) => p.canonical_name)
                .filter(Boolean)
                .join(" · ") || "无"}
            </p>
            <p className="l3-note">
              案例：
              {(ctx.matching_cases || [])
                .flatMap((block) => block.cases || [])
                .slice(0, 6)
                .map((c) => c.canonical_name)
                .filter(Boolean)
                .join(" · ") || "无"}
            </p>
            <p className="l3-note">设备类型上下文：{equipmentType || "—"}</p>
          </article>

          <article className="l3-card">
            <header>
              <h3>推理链知识引用</h3>
              <span>{knowNodes.length}</span>
            </header>
            <ul className="l3-plain">
              {knowNodes.slice(0, 8).map((node) => (
                <li key={String(node.node_id)}>
                  KNOWLEDGE_REFERENCE · {String(node.knowledge_id)} · {String(node.applicability_status || "")}
                  <div className="l3-note">{String(node.content_summary || "")}</div>
                  <div className="l3-note">{String(node.note || "参考知识，非独立数值 SUPPORT")}</div>
                </li>
              ))}
              {knowNodes.length === 0 ? <li className="l3-note">本记录 Trace 覆盖层暂无知识节点</li> : null}
            </ul>
            {(ctx.missing_knowledge || []).length ? (
              <p className="l3-note">缺失：{(ctx.missing_knowledge || []).join(" · ")}</p>
            ) : null}
          </article>
        </>
      )}
    </details>
  )
}
