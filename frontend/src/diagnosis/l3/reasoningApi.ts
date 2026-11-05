export type ReasoningRunSummary = {
  reasoning_run_id: string
  source_analysis_run_id: string
  knowledge_snapshot_id?: string | null
  llm_model?: string | null
  status: string
  selected_record_count?: number
  processed_record_count?: number
  succeeded_record_count?: number
  insufficient_record_count?: number
  failed_record_count?: number
  validation_failed_record_count?: number
  selection?: {
    selected?: Array<{ record_id: string; selection_reason?: string }>
    deferred?: Array<{ record_id: string; note?: string }>
    skipped?: Array<{ record_id: string; selection_reason?: string }>
  }
  error_summary?: string | null
  created_at?: string
  finished_at?: string | null
}

export type ReasoningRecord = {
  record_id: string
  status: string
  selection_reason?: string | null
  mode?: string | null
  outcome_class?: string | null
  error_message?: string | null
  audit?: {
    prompt_tokens?: number | null
    completion_tokens?: number | null
    total_tokens?: number | null
    llm_call_count?: number
    tool_call_count?: number
    execution_duration?: number | null
    finish_reason?: string | null
    error_type?: string | null
  }
  assessment: {
    status?: string
    outcome_class?: string
    explanation?: string
    evidence_sufficiency?: string
    fault_type_review?: Record<string, unknown>
    candidate_hypotheses?: Array<{
      hypothesis_id?: string
      fault_type?: string
      hypothesis_origin?: string
      assessment?: string
      supporting_evidence_ids?: string[]
      contradicting_evidence_ids?: string[]
      knowledge_reference_ids?: string[]
      missing_evidence?: string[]
    }>
    missing_evidence?: string[]
    limitations?: string[]
    knowledge_references?: string[]
    evidence_references?: string[]
    validation?: { ok?: boolean; errors?: string[]; association_errors?: string[] }
    llm_model?: string
    audit?: ReasoningRecord["audit"]
  }
  tool_history?: Array<{ tool?: string; ok?: boolean; arguments?: Record<string, unknown> }>
  trace_overlay?: {
    nodes?: Array<Record<string, unknown>>
    build_status?: string
    reasoning_enrichment?: Record<string, unknown>
  } | null
}

async function requestJson(path: string, init?: RequestInit): Promise<unknown> {
  const response = await fetch(path, {
    headers: { Accept: "application/json", ...(init?.headers || {}) },
    ...init,
  })
  const text = await response.text()
  let body: unknown = null
  try {
    body = text ? JSON.parse(text) : null
  } catch {
    body = { raw: text }
  }
  if (!response.ok) {
    const detail = (body as { detail?: { message?: string } | string })?.detail
    const message =
      typeof detail === "string"
        ? detail
        : detail && typeof detail === "object"
          ? detail.message || JSON.stringify(detail)
          : `HTTP ${response.status}`
    throw new Error(message || `HTTP ${response.status}`)
  }
  return body
}

export async function getReasoningStatus() {
  return (await requestJson("/l3/reasoning/status")) as {
    module_version?: string
    policy?: Record<string, unknown>
    tools?: string[]
  }
}

export async function createReasoningRun(body: {
  analysis_run_id: string
  record_ids?: string[]
  automatic_selection?: boolean
  manual_request?: boolean
  force_new?: boolean
  dry_run?: boolean
}) {
  return (await requestJson("/l3/reasoning/runs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  })) as ReasoningRunSummary
}

export async function getReasoningByAnalysis(analysisRunId: string) {
  return (await requestJson(`/l3/reasoning/by-analysis/${encodeURIComponent(analysisRunId)}`)) as {
    analysis_run_id: string
    reasoning_run: ReasoningRunSummary | null
  }
}

export async function getReasoningRecord(reasoningRunId: string, recordId: string) {
  return (await requestJson(
    `/l3/reasoning/runs/${encodeURIComponent(reasoningRunId)}/records/${encodeURIComponent(recordId)}`,
  )) as ReasoningRecord
}
