/** L5a 报告中心 API 客户端 */

export type FieldSlot = {
  value?: unknown
  display?: string | null
  source_layer?: string | null
  source_run_id?: string | null
  evidence_references?: string[]
  knowledge_references?: string[]
  uncertainty?: string | null
  null_reason?: string | null
  extras?: Record<string, unknown>
}

export type FinalDiagnosisAssessment = {
  schema?: string
  l5_run_id?: string
  record_id?: string
  equipment_id?: string | null
  equipment_type?: string | null
  source_run_ids?: Record<string, string | null | undefined>
  source_versions?: Record<string, unknown>
  source_availability?: Record<string, boolean>
  fields?: Record<string, FieldSlot>
  field_labels_zh?: Record<string, string>
  verification_recommendations?: Array<Record<string, unknown>>
  limitations?: string[]
  review_status?: string
  completeness?: string
  root_cause_confirmation?: string
  outcome?: string
  provenance_index?: Record<string, unknown>
  supervised_prediction?: {
    model_id?: string
    model_versions?: Record<string, unknown>
    model_input_fingerprint?: string
    equipment_category?: string
    field_semantics?: Record<string, string>
    limitations?: string[]
  }
}

export type SynthesisAssessment = {
  schema?: string
  synthesis_run_id?: string
  record_id?: string
  field_comparisons?: Array<Record<string, unknown>>
  consistency_summary?: Record<string, number>
  unresolved_conflicts?: Array<Record<string, unknown>>
  roles?: Record<string, unknown>
  handoff?: Record<string, unknown>
  llm_synthesis?: Record<string, unknown> | null
  limitations?: string[]
  l4_root_cause_confirmation_snapshot?: string
  agent_audit?: Array<Record<string, unknown>>
}

export type L5RunSummary = {
  l5_run_id: string
  diagnosis_run_id?: string | null
  analysis_run_id?: string | null
  l4_run_id?: string | null
  status?: string
  selected_record_count?: number
  succeeded_record_count?: number
  partial_record_count?: number
  failed_record_count?: number
  created_at?: string
  finished_at?: string
  review_note?: string
  source_versions?: Record<string, unknown>
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

export async function listL5Runs(limit = 50, signal?: AbortSignal) {
  return (await requestJson(`/l5/runs?limit=${limit}`, { signal })) as {
    total: number
    items: L5RunSummary[]
  }
}

export async function getL5Run(l5RunId: string, signal?: AbortSignal) {
  return (await requestJson(`/l5/runs/${encodeURIComponent(l5RunId)}`, { signal })) as {
    l5_run: L5RunSummary
  }
}

export async function listL5Records(l5RunId: string, limit = 100, signal?: AbortSignal) {
  return (await requestJson(`/l5/runs/${encodeURIComponent(l5RunId)}/records?limit=${limit}`, {
    signal,
  })) as {
    total: number
    items: Array<{
      record_id: string
      status?: string
      outcome?: string
      review_status?: string
      completeness?: string
      assessment?: FinalDiagnosisAssessment
      error_message?: string
    }>
  }
}

export async function getL5Record(l5RunId: string, recordId: string, signal?: AbortSignal) {
  return (await requestJson(
    `/l5/runs/${encodeURIComponent(l5RunId)}/records/${encodeURIComponent(recordId)}`,
    { signal },
  )) as {
    record_id: string
    assessment?: FinalDiagnosisAssessment
    provenance?: Record<string, unknown>
    outcome?: string
    review_status?: string
  }
}

export async function getL5Provenance(
  l5RunId: string,
  recordId: string,
  field?: string,
  signal?: AbortSignal,
) {
  const q = field ? `?field=${encodeURIComponent(field)}` : ""
  return (await requestJson(
    `/l5/runs/${encodeURIComponent(l5RunId)}/records/${encodeURIComponent(recordId)}/provenance${q}`,
    { signal },
  )) as Record<string, unknown>
}

export async function createL5Run(
  body: {
    diagnosis_run_id?: string
    analysis_run_id?: string
    l4_run_id?: string
    record_ids?: string[]
    force_new?: boolean
    max_records?: number
  },
  signal?: AbortSignal,
) {
  return (await requestJson("/l5/runs", { method: "POST", body: JSON.stringify(body), signal })) as {
    l5_run: L5RunSummary
    created?: boolean
  }
}

export type AlertAssessment = {
  formal_alerts?: Array<Record<string, unknown>>
  reference_comparisons?: Array<Record<string, unknown>>
  conflicts?: Array<Record<string, unknown>>
  not_assessed?: Array<Record<string, unknown>>
  limitations?: string[]
  formal_alert_count?: number
  reference_count?: number
  catalog_version?: string
}

export async function getAlertsByL5(l5RunId: string, signal?: AbortSignal) {
  return (await requestJson(`/l5/alerts/by-l5/${encodeURIComponent(l5RunId)}`, { signal })) as {
    alert_run: { alert_run_id: string; status?: string } | null
  }
}

export async function createAlertRun(
  body: {
    source_l5_run_id?: string
    analysis_run_id?: string
    l4_run_id?: string
    record_ids?: string[]
    force_new?: boolean
    max_records?: number
  },
  signal?: AbortSignal,
) {
  return (await requestJson("/l5/alerts/runs", { method: "POST", body: JSON.stringify(body), signal })) as {
    alert_run: { alert_run_id: string; status?: string; formal_alert_total?: number }
  }
}

export async function getAlertRecord(alertRunId: string, recordId: string, signal?: AbortSignal) {
  return (await requestJson(
    `/l5/alerts/runs/${encodeURIComponent(alertRunId)}/records/${encodeURIComponent(recordId)}`,
    { signal },
  )) as { record_id: string; assessment?: AlertAssessment }
}

export async function getAlertRule(ruleId: string, signal?: AbortSignal) {
  return (await requestJson(`/l5/alerts/rules/${encodeURIComponent(ruleId)}`, { signal })) as {
    rule: Record<string, unknown>
    conflicts: Array<Record<string, unknown>>
  }
}

export async function createSynthesisRun(
  body: {
    l5_run_id: string
    record_ids?: string[]
    allow_llm_synthesis?: boolean
    force_new?: boolean
    max_records?: number
  },
  signal?: AbortSignal,
) {
  return (await requestJson("/l5/synthesis/runs", {
    method: "POST",
    body: JSON.stringify(body),
    signal,
  })) as {
    synthesis_run: { synthesis_run_id: string; status?: string }
    reused?: boolean
  }
}

export async function listSynthesisRecords(synthesisRunId: string, limit = 50, signal?: AbortSignal) {
  return (await requestJson(
    `/l5/synthesis/runs/${encodeURIComponent(synthesisRunId)}/records?limit=${limit}`,
    { signal },
  )) as {
    total: number
    items: Array<{ record_id: string; status?: string; assessment?: SynthesisAssessment }>
  }
}

export async function listSynthesisRuns(limit = 50, signal?: AbortSignal) {
  return (await requestJson(`/l5/synthesis/runs?limit=${limit}`, { signal })) as {
    total: number
    items: Array<{
      synthesis_run_id: string
      status?: string
      source_l5_run_id?: string
      record_ids?: string[]
      created_at?: string
    }>
  }
}

export async function getPredictionStatus(signal?: AbortSignal) {
  return (await requestJson("/l5/prediction/status", { signal })) as {
    model_id?: string
    n_models?: number
    equipment_categories?: string[]
  }
}
