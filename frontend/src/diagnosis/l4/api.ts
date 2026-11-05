export type L4RunSummary = {
  l4_run_id: string
  source_analysis_run_id: string
  source_reasoning_run_id?: string | null
  selected_record_count: number
  processed_record_count: number
  succeeded_record_count: number
  failed_record_count: number
  status: string
  error_summary?: string | null
  causal_knowledge_version?: string
  created_at?: string
  finished_at?: string | null
}

export type RootCauseAssessment = {
  l4_run_id?: string
  analysis_run_id?: string
  record_id?: string
  outcome?: string
  root_cause_confirmation?: string
  l3_fault_candidates?: Array<Record<string, unknown>>
  root_cause_candidates?: Array<Record<string, unknown>>
  causal_paths?: Array<Record<string, unknown>>
  impact_assessment?: Record<string, unknown>
  missing_evidence?: string[]
  limitations?: string[]
  causal_trace?: {
    nodes?: Array<Record<string, unknown>>
    edges?: Array<{ from: string; to: string; relation: string }>
    acyclic?: boolean
  }
  notes?: string[]
  input_context_summary?: Record<string, unknown>
  [key: string]: unknown
}

async function requestJson(path: string, init?: RequestInit): Promise<unknown> {
  const response = await fetch(path, {
    headers: { Accept: "application/json", "Content-Type": "application/json", ...(init?.headers || {}) },
    ...init,
  })
  const body = await response.json().catch(() => ({}))
  if (!response.ok) {
    const detail = (body as { detail?: { message?: string; error_code?: string } })?.detail
    throw new Error(detail?.message || detail?.error_code || `HTTP ${response.status}`)
  }
  return body
}

export async function getL4Status(signal?: AbortSignal) {
  return (await requestJson("/l4/status", { signal })) as Record<string, unknown>
}

export async function getL4ByAnalysis(analysisRunId: string, signal?: AbortSignal) {
  return (await requestJson(`/l4/by-analysis/${encodeURIComponent(analysisRunId)}`, { signal })) as {
    l4_run: L4RunSummary | null
    analysis_run_id: string
  }
}

export async function createL4Run(
  body: {
    analysis_run_id: string
    record_ids?: string[]
    force_new?: boolean
    max_records?: number
  },
  signal?: AbortSignal,
) {
  return (await requestJson("/l4/runs", { method: "POST", body: JSON.stringify(body), signal })) as {
    l4_run: L4RunSummary
    created: boolean
    selected_record_ids: string[]
  }
}

export async function listL4Results(l4RunId: string, signal?: AbortSignal) {
  return (await requestJson(`/l4/runs/${encodeURIComponent(l4RunId)}/results?limit=100`, { signal })) as {
    total: number
    items: Array<{
      record_id: string
      equipment_id?: string
      status: string
      outcome?: string
      assessment?: RootCauseAssessment
    }>
  }
}

export async function getL4Record(l4RunId: string, recordId: string, signal?: AbortSignal) {
  return (await requestJson(`/l4/runs/${encodeURIComponent(l4RunId)}/records/${encodeURIComponent(recordId)}`, {
    signal,
  })) as {
    record_id: string
    status: string
    outcome?: string
    assessment?: RootCauseAssessment
  }
}

export type L4ReasoningRunSummary = {
  reasoning_run_id: string
  source_l4_run_id: string
  selected_record_count: number
  processed_record_count: number
  succeeded_record_count: number
  failed_record_count: number
  validation_failed_record_count?: number
  status: string
  prompt_version?: string
  token_usage?: Record<string, number>
  error_summary?: string | null
}

export type CausalReasoningAssessment = {
  reasoning_run_id?: string
  source_l4_run_id?: string
  record_id?: string
  outcome?: string
  root_cause_confirmation?: string
  candidate_reviews?: Array<Record<string, unknown>>
  causal_path_reviews?: Array<Record<string, unknown>>
  missing_evidence?: string[]
  limitations?: string[]
  overall_assessment?: string
  impact_interpretation?: Record<string, unknown>
  excluded_review_recommendations?: Array<Record<string, unknown>>
  audit?: Record<string, unknown>
  validation?: Record<string, unknown>
  [key: string]: unknown
}

export async function createL4ReasoningRun(
  body: {
    l4_run_id: string
    record_ids?: string[]
    force_new?: boolean
    max_records?: number
    dry_run?: boolean
  },
  signal?: AbortSignal,
) {
  return (await requestJson("/l4/reasoning/runs", { method: "POST", body: JSON.stringify(body), signal })) as {
    reasoning_run: L4ReasoningRunSummary
    created: boolean
    selected_record_ids: string[]
  }
}

export async function getL4ReasoningByL4(l4RunId: string, signal?: AbortSignal) {
  return (await requestJson(`/l4/reasoning/by-l4/${encodeURIComponent(l4RunId)}`, { signal })) as {
    reasoning_run: L4ReasoningRunSummary | null
    l4_run_id: string
  }
}

export async function listL4ReasoningResults(reasoningRunId: string, signal?: AbortSignal) {
  return (await requestJson(`/l4/reasoning/runs/${encodeURIComponent(reasoningRunId)}/results?limit=100`, {
    signal,
  })) as {
    total: number
    items: Array<{
      record_id: string
      status: string
      outcome?: string
      assessment?: CausalReasoningAssessment
      tool_history?: Array<Record<string, unknown>>
    }>
  }
}
