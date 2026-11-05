export type KnowledgeStatus = {
  module_version?: string
  knowledge_snapshot_id?: string | null
  item_count?: number
  sources?: { importable_count?: number; missing?: string[] }
  graph?: { available?: boolean; entity_count?: number; relationship_count?: number }
  bm25_index?: { available?: boolean; note?: string }
  vector_index?: { available?: boolean; backend?: string; note?: string }
  conflicts?: unknown[]
}

export type KnowledgeHit = {
  knowledge_id: string
  knowledge_type?: string
  canonical_name?: string
  content_summary?: string
  source_document_id?: string
  source_location?: Record<string, unknown>
  equipment_scope?: string[]
  fault_mode_scope?: string[]
  applicability_status?: string
  retrieval_score?: number
  note?: string
}

export type EnrichmentRecord = {
  record_id: string
  status: string
  context: {
    fault_candidates?: string[]
    applicable_knowledge?: KnowledgeHit[]
    related_components?: { components?: Array<{ canonical_name?: string }>; paths?: unknown[] }
    matching_cases?: Array<{ cases?: Array<{ canonical_name?: string }> }>
    diagnostic_rules?: { parameters?: Array<{ canonical_name?: string }>; paths?: unknown[] }
    missing_knowledge?: string[]
    retrieval?: Record<string, unknown>
    knowledge_snapshot_id?: string
  }
  trace_overlay?: {
    nodes?: Array<Record<string, unknown>>
    build_status?: string
    knowledge_enrichment?: Record<string, unknown>
  } | null
}

async function requestJson(path: string, init?: RequestInit): Promise<unknown> {
  const response = await fetch(path, {
    headers: { Accept: "application/json", ...(init?.headers || {}) },
    ...init,
  })
  const body = await response.json().catch(() => ({}))
  if (!response.ok) {
    const detail = (body as { detail?: { message?: string } })?.detail
    throw new Error(detail?.message || `HTTP ${response.status}`)
  }
  return body
}

export async function getKnowledgeStatus(signal?: AbortSignal): Promise<KnowledgeStatus> {
  return (await requestJson("/l3/knowledge/status", { signal })) as KnowledgeStatus
}

export async function ensureKnowledgeSnapshot(): Promise<{ knowledge_snapshot_id: string; item_count: number }> {
  return (await requestJson("/l3/knowledge/snapshots", { method: "POST" })) as {
    knowledge_snapshot_id: string
    item_count: number
  }
}

export async function searchKnowledge(params: {
  query: string
  equipment_type?: string
  fault_mode?: string
  top_k?: number
}): Promise<{ mode?: string; message?: string; results: KnowledgeHit[]; semantic?: { status?: string }; bm25?: { available?: boolean } }> {
  const q = new URLSearchParams()
  q.set("query", params.query)
  if (params.equipment_type) q.set("equipment_type", params.equipment_type)
  if (params.fault_mode) q.set("fault_mode", params.fault_mode)
  if (params.top_k) q.set("top_k", String(params.top_k))
  return (await requestJson(`/l3/knowledge/search?${q}`)) as {
    mode?: string
    message?: string
    results: KnowledgeHit[]
    semantic?: { status?: string }
    bm25?: { available?: boolean }
  }
}

export async function createEnrichment(payload: {
  analysis_run_id: string
  record_ids?: string[]
  limit?: number
  force_new?: boolean
}): Promise<{ enrichment_run_id: string; status: string; succeeded_record_count?: number }> {
  return (await requestJson("/l3/knowledge/enrichment-runs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })) as { enrichment_run_id: string; status: string; succeeded_record_count?: number }
}

export async function getEnrichmentByAnalysis(analysisRunId: string): Promise<{
  enrichment_run: { enrichment_run_id: string; status: string; knowledge_snapshot_id?: string } | null
}> {
  return (await requestJson(`/l3/knowledge/by-analysis/${encodeURIComponent(analysisRunId)}`)) as {
    enrichment_run: { enrichment_run_id: string; status: string; knowledge_snapshot_id?: string } | null
  }
}

export async function getEnrichmentRecord(enrichmentRunId: string, recordId: string): Promise<EnrichmentRecord> {
  return (await requestJson(
    `/l3/knowledge/enrichment-runs/${encodeURIComponent(enrichmentRunId)}/records/${encodeURIComponent(recordId)}`,
  )) as EnrichmentRecord
}
