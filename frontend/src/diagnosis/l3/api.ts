export type AnalysisRunSummary = {
  analysis_run_id: string
  source_diagnosis_run_id: string
  prepared_dataset_id: string
  dataset_revision_id: string
  equipment_type: string
  model_bundle_id?: string | null
  selected_record_count: number
  processed_record_count: number
  succeeded_record_count: number
  failed_record_count: number
  inconclusive_record_count: number
  analyzer_config_hash?: string
  routing_policy_version?: string
  status: string
  error_summary?: string | null
  result_dir?: string | null
  created_at?: string
  started_at?: string | null
  finished_at?: string | null
}

export type EvidenceBundle = {
  analysis_run_id?: string
  record_id?: string
  equipment_type?: string
  equipment_id?: string | null
  l2_context?: Record<string, unknown>
  fault_type_review?: Array<Record<string, unknown>>
  severity_assessment?: Record<string, unknown>
  location_candidates?: Record<string, unknown>
  spectral_feature_assessment?: Record<string, unknown>
  evidence_sufficiency?: string
  evidence_items?: Array<Record<string, unknown>>
  support_evidence?: Array<Record<string, unknown>>
  contradict_evidence?: Array<Record<string, unknown>>
  missing_evidence?: string[]
  analysis_limitations?: string[]
  covering_analyzers?: string[]
  deferred_fields?: Record<string, unknown>
  l4_handoff_stub?: Record<string, unknown>
  history_note?: string
  history_sample_count?: number
  trigger_reason?: string
  reasoning_trace?: {
    trace_id?: string
    trace_version?: string
    build_status?: string
    error?: string
    nodes?: Array<Record<string, unknown>>
    edges?: Array<{ from: string; to: string; relation: string }>
    conclusion_node_ids?: string[]
    limitations?: string[]
  } | null
  [key: string]: unknown
}

async function requestJson(path: string, init?: RequestInit): Promise<unknown> {
  const response = await fetch(path, {
    headers: { Accept: "application/json", ...(init?.headers || {}) },
    ...init,
  })
  const body = await response.json().catch(() => ({}))
  if (!response.ok) {
    const detail = (body as { detail?: { message?: string; error_code?: string } })?.detail
    throw new Error(detail?.message || detail?.error_code || `HTTP ${response.status}`)
  }
  return body
}

export async function getAnalysisRunByDiagnosis(
  diagnosisRunId: string,
  signal?: AbortSignal,
): Promise<{ analysis_run: AnalysisRunSummary | null; diagnosis_run_id: string }> {
  return (await requestJson(`/l3/by-diagnosis/${encodeURIComponent(diagnosisRunId)}`, { signal })) as {
    analysis_run: AnalysisRunSummary | null
    diagnosis_run_id: string
  }
}

export async function getAnalysisRun(analysisRunId: string, signal?: AbortSignal): Promise<AnalysisRunSummary> {
  return (await requestJson(`/l3/analysis-runs/${encodeURIComponent(analysisRunId)}`, { signal })) as AnalysisRunSummary
}

export async function createAnalysisRun(payload: {
  diagnosis_run_id: string
  record_ids?: string[]
  manual_request?: boolean
  force_new?: boolean
}): Promise<AnalysisRunSummary & { created?: boolean; reused?: boolean }> {
  return (await requestJson("/l3/analysis-runs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })) as AnalysisRunSummary & { created?: boolean; reused?: boolean }
}

export async function listAnalysisResults(
  analysisRunId: string,
  params?: { offset?: number; limit?: number; equipment_id?: string; record_id?: string; status?: string },
  signal?: AbortSignal,
): Promise<{ total: number; offset: number; limit: number; items: Array<{
  record_id: string
  equipment_id?: string | null
  status: string
  trigger_reason?: string
  screening_result?: string
  evidence: EvidenceBundle
  error_message?: string | null
}> }> {
  const query = new URLSearchParams()
  if (params?.offset != null) query.set("offset", String(params.offset))
  if (params?.limit != null) query.set("limit", String(params.limit))
  if (params?.equipment_id) query.set("equipment_id", params.equipment_id)
  if (params?.record_id) query.set("record_id", params.record_id)
  if (params?.status) query.set("status", params.status)
  const suffix = query.toString() ? `?${query}` : ""
  return (await requestJson(`/l3/analysis-runs/${encodeURIComponent(analysisRunId)}/results${suffix}`, {
    signal,
  })) as {
    total: number
    offset: number
    limit: number
    items: Array<{
      record_id: string
      equipment_id?: string | null
      status: string
      trigger_reason?: string
      screening_result?: string
      evidence: EvidenceBundle
      error_message?: string | null
    }>
  }
}

export async function getRecordEvidence(
  analysisRunId: string,
  recordId: string,
  signal?: AbortSignal,
): Promise<{
  record_id: string
  equipment_id?: string | null
  status: string
  evidence: EvidenceBundle
}> {
  return (await requestJson(
    `/l3/analysis-runs/${encodeURIComponent(analysisRunId)}/records/${encodeURIComponent(recordId)}`,
    { signal },
  )) as {
    record_id: string
    equipment_id?: string | null
    status: string
    evidence: EvidenceBundle
  }
}
