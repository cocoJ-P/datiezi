import type {
  ActiveStatus,
  DatasetSummary,
  DiagnosisResult,
  EquipmentTypeSummary,
  L2ApiErrorBody,
  ModelDetail,
  ModelSummary,
  PedRowsPage,
  TrainingJob,
  TrainingJobLogs,
} from "./types"

export class L2ApiError extends Error {
  errorCode: string
  status: number
  details: Record<string, unknown>
  requestId: string | null

  constructor(errorCode: string, message: string, status: number, details: Record<string, unknown> = {}, requestId: string | null = null) {
    super(message)
    this.errorCode = errorCode
    this.status = status
    this.details = details
    this.requestId = requestId
  }
}

export function mapL2Error(error: unknown): string {
  if (error instanceof L2ApiError) {
    const code = error.errorCode
    if (code === "MODEL_NOT_READY" || code === "MODEL_NOT_AVAILABLE" || error.status === 503) return "该设备当前没有可用的运行时模型。"
    if (code === "DATASET_NOT_FOUND") return error.message || "数据集不存在或不适用于当前操作。"
    if (code === "DATASET_NOT_INFERENCE_READY") return "该数据集尚未具备推理条件。"
    if (code === "SCHEMA_INCOMPATIBLE") return "数据集 Feature Schema 与 Active 模型不兼容。"
    if (code === "RECORD_NOT_FOUND") return "所选记录不存在于当前数据集。"
    if (code === "BATCH_TOO_LARGE") return "批量记录数超过上限。"
    if (code === "MODEL_ACTIVATION_FAILED" || code === "MODEL_BUNDLE_INVALID") return error.message || "模型激活失败。"
    if (code === "MODEL_ALREADY_ACTIVE") return "该模型已经是 Active。"
    if (code === "MODEL_VERSION_CONFLICT") return "模型状态在操作过程中发生变化，请刷新后重试。"
    if (code === "TRAINING_JOB_NOT_FOUND") return "训练任务不存在。"
    if (error.status === 0) return "无法连接后端服务，请确认 FastAPI 已启动。"
    return error.message || "请求失败。"
  }
  if (error instanceof Error && error.name === "AbortError") return "请求已取消。"
  return "请求失败。"
}

export async function listEquipmentTypes(signal?: AbortSignal): Promise<EquipmentTypeSummary[]> {
  const body = await requestJson("/l2/management/equipment-types", { signal })
  return Array.isArray(body.equipment_types) ? (body.equipment_types as EquipmentTypeSummary[]) : []
}

export async function getEquipmentType(equipmentType: string, signal?: AbortSignal): Promise<EquipmentTypeSummary> {
  return (await requestJson(`/l2/management/equipment-types/${encodeURIComponent(equipmentType)}`, { signal })) as EquipmentTypeSummary
}

export async function listDatasets(signal?: AbortSignal): Promise<DatasetSummary[]> {
  const body = await requestJson("/l2/management/datasets", { signal })
  return Array.isArray(body.datasets) ? (body.datasets as DatasetSummary[]) : []
}

export async function listPedRows(
  preparedDatasetId: string,
  options: { offset?: number; limit?: number } = {},
  signal?: AbortSignal,
): Promise<PedRowsPage> {
  const params = new URLSearchParams({
    offset: String(options.offset ?? 0),
    limit: String(options.limit ?? 20),
  })
  const body = await requestJson(`/api/l1/prepared-datasets/${encodeURIComponent(preparedDatasetId)}/rows?${params}`, { signal })
  const rows = Array.isArray(body.rows) ? (body.rows as PedRowsPage["rows"]) : []
  return {
    prepared_dataset_id: typeof body.prepared_dataset_id === "string" ? body.prepared_dataset_id : preparedDatasetId,
    total: typeof body.total === "number" ? body.total : undefined,
    total_records: typeof body.total_records === "number" ? body.total_records : undefined,
    offset: typeof body.offset === "number" ? body.offset : options.offset ?? 0,
    limit: typeof body.limit === "number" ? body.limit : options.limit ?? 20,
    rows,
  }
}

export async function diagnoseByFeatures(
  payload: { equipment_type: string; record_id?: string; features: Record<string, number> },
  signal?: AbortSignal,
): Promise<DiagnosisResult> {
  return (await sendJson("/l2/diagnose", payload, { signal })) as DiagnosisResult
}

export async function diagnosePreparedDataset(
  preparedDatasetId: string,
  payload: { record_ids: string[]; revision_id?: string },
  signal?: AbortSignal,
): Promise<{ results: DiagnosisResult[]; prepared_dataset_id?: string; dataset_revision_id?: string; model_bundle_id?: string }> {
  return (await sendJson(`/l2/datasets/${encodeURIComponent(preparedDatasetId)}/diagnose`, payload, { signal })) as {
    results: DiagnosisResult[]
    prepared_dataset_id?: string
    dataset_revision_id?: string
    model_bundle_id?: string
  }
}

export async function getActiveStatus(equipmentType?: string, signal?: AbortSignal): Promise<ActiveStatus | { equipment: ActiveStatus[] }> {
  const query = equipmentType ? `?equipment_type=${encodeURIComponent(equipmentType)}` : ""
  return (await requestJson(`/l2/management/active${query}`, { signal })) as ActiveStatus | { equipment: ActiveStatus[] }
}

export async function getRuntimeModel(equipmentType: string, signal?: AbortSignal): Promise<Record<string, unknown>> {
  return requestJson(`/l2/model?equipment_type=${encodeURIComponent(equipmentType)}`, { signal })
}

export async function listModels(
  options: { equipment_type?: string; status?: string; offset?: number; limit?: number } = {},
  signal?: AbortSignal,
): Promise<{ total: number; models: ModelSummary[] }> {
  const params = new URLSearchParams()
  if (options.equipment_type) params.set("equipment_type", options.equipment_type)
  if (options.status) params.set("status", options.status)
  params.set("offset", String(options.offset ?? 0))
  params.set("limit", String(options.limit ?? 50))
  const body = await requestJson(`/l2/management/models?${params}`, { signal })
  return {
    total: typeof body.total === "number" ? body.total : 0,
    models: Array.isArray(body.models) ? (body.models as ModelSummary[]) : [],
  }
}

export async function getModelDetail(modelBundleId: string, signal?: AbortSignal): Promise<ModelDetail> {
  return (await requestJson(`/l2/management/models/${encodeURIComponent(modelBundleId)}`, { signal })) as ModelDetail
}

export async function activateModel(modelBundleId: string, signal?: AbortSignal): Promise<Record<string, unknown>> {
  return sendJson(`/l2/management/models/${encodeURIComponent(modelBundleId)}/activate`, {}, { signal })
}

export async function rollbackModel(
  payload: { equipment_type: string; target_model_bundle_id: string },
  signal?: AbortSignal,
): Promise<Record<string, unknown>> {
  return sendJson("/l2/management/models/rollback", payload, { signal })
}

export async function submitTrainJob(
  payload: {
    equipment_type: string
    dataset_id?: string
    prepared_dataset_id?: string
    parent_model_bundle_id?: string
  },
  signal?: AbortSignal,
): Promise<{
  job_id: string
  status: string
  equipment_type: string
  dataset_id: string
  dataset_revision_id?: string
  created_at?: string
  train_mode?: "new" | "retrain" | string
  parent_model_bundle_id?: string | null
}> {
  return (await sendJson("/l2/management/train/jobs", payload, { signal })) as {
    job_id: string
    status: string
    equipment_type: string
    dataset_id: string
    dataset_revision_id?: string
    created_at?: string
    train_mode?: "new" | "retrain" | string
    parent_model_bundle_id?: string | null
  }
}

export async function listTrainJobs(
  options: { status?: string; offset?: number; limit?: number } = {},
  signal?: AbortSignal,
): Promise<{ total: number; jobs: TrainingJob[] }> {
  const params = new URLSearchParams()
  if (options.status) params.set("status", options.status)
  params.set("offset", String(options.offset ?? 0))
  params.set("limit", String(options.limit ?? 20))
  const body = await requestJson(`/l2/management/train/jobs?${params}`, { signal })
  return {
    total: typeof body.total === "number" ? body.total : 0,
    jobs: Array.isArray(body.jobs) ? (body.jobs as TrainingJob[]) : [],
  }
}

export async function getTrainJob(jobId: string, signal?: AbortSignal): Promise<TrainingJob> {
  return (await requestJson(`/l2/management/train/jobs/${encodeURIComponent(jobId)}`, { signal })) as TrainingJob
}

export async function getTrainJobLogs(jobId: string, maxLines = 200, signal?: AbortSignal): Promise<TrainingJobLogs> {
  return (await requestJson(
    `/l2/management/train/jobs/${encodeURIComponent(jobId)}/logs?max_lines=${maxLines}`,
    { signal },
  )) as TrainingJobLogs
}

export type WorkflowResponse = {
  dataset_id: string
  l1?: { status?: string; pd_version_id?: string; prepare_run_id?: string }
  l2?: {
    status?: string
    handoff?: {
      handoff_id: string
      status: string
      dataset_id?: string
      pd_version_id?: string
      error_message?: string | null
      summary?: Record<string, unknown>
      equipment_items?: Array<{
        item_id: string
        equipment_type?: string | null
        equipment_category?: string | null
        status: string
        ped_id?: string | null
        ped_revision_id?: string | null
        diagnosis_run_id?: string | null
        model_bundle_id?: string | null
        training_job_id?: string | null
        message?: string | null
        match_status?: string | null
        details?: Record<string, unknown>
      }>
    } | null
  }
}

export type DiagnosisRunSummary = {
  diagnosis_run_id: string
  prepared_dataset_id: string
  dataset_revision_id: string
  equipment_type: string
  model_bundle_id: string
  schema_version?: string
  status: string
  total_records: number
  processed_records: number
  succeeded_records: number
  failed_records: number
  counts: Record<string, number>
  started_at?: string
  finished_at?: string
}

export async function getDiagnosisWorkflow(datasetId: string, signal?: AbortSignal): Promise<WorkflowResponse> {
  return (await requestJson(`/api/diagnosis/workflows/${encodeURIComponent(datasetId)}`, { signal })) as WorkflowResponse
}

export async function resumeL2Workflow(datasetId: string, signal?: AbortSignal): Promise<Record<string, unknown>> {
  return sendJson(`/api/diagnosis/workflows/${encodeURIComponent(datasetId)}/resume-l2`, {}, { signal })
}

export async function confirmHandoffTrain(
  handoffId: string,
  itemId?: string,
  signal?: AbortSignal,
): Promise<Record<string, unknown>> {
  return sendJson(`/l2/handoff/${encodeURIComponent(handoffId)}/train`, itemId ? { item_id: itemId } : {}, { signal })
}

export async function skipHandoffTraining(
  handoffId: string,
  itemId?: string,
  signal?: AbortSignal,
): Promise<Record<string, unknown>> {
  return sendJson(`/l2/handoff/${encodeURIComponent(handoffId)}/skip-training`, itemId ? { item_id: itemId } : {}, { signal })
}

export async function getDiagnosisRun(runId: string, signal?: AbortSignal): Promise<DiagnosisRunSummary> {
  return (await requestJson(`/l2/diagnosis-runs/${encodeURIComponent(runId)}`, { signal })) as DiagnosisRunSummary
}

export type RoutingGroupSummary = {
  group_key: string
  equipment_type: string
  equipment_group_id?: string | null
  equipment_id?: string | null
  display_name: string
  total_records: number
  l2_complete_count: number
  l3_review_count: number
  normal_count: number
  abnormal_count: number
  conflict_count: number
  uncertain_count: number
  failed_count: number
  screening_status: "needs_l3_review" | "l2_complete" | "failed" | "pending" | string
  screening_label: string
  l3_record_ids: string[]
  l3_record_ids_truncated?: boolean
}

export type DiagnosisRoutingSummary = {
  diagnosis_run_id: string
  prepared_dataset_id: string
  dataset_revision_id: string
  equipment_type: string
  display_name: string
  model_bundle_id?: string | null
  status?: string
  total_records: number
  processed_records: number
  succeeded_records: number
  failed_records: number
  screening_counts: Record<string, number>
  route_counts: Record<string, number>
  l2_complete_count: number
  l3_review_count: number
  l3_record_ids: string[]
  l3_record_ids_truncated?: boolean
  groups: RoutingGroupSummary[]
}

export async function getDiagnosisRoutingSummary(
  runId: string,
  signal?: AbortSignal,
): Promise<DiagnosisRoutingSummary> {
  return (await requestJson(
    `/l2/diagnosis-runs/${encodeURIComponent(runId)}/routing-summary`,
    { signal },
  )) as DiagnosisRoutingSummary
}

export async function listDiagnosisRunRecords(
  runId: string,
  options: { offset?: number; limit?: number; screening_result?: string } = {},
  signal?: AbortSignal,
): Promise<{ total: number; items: Array<{ record_id: string; status: string; screening_result?: string; result: DiagnosisResult }> }> {
  const params = new URLSearchParams({
    offset: String(options.offset ?? 0),
    limit: String(options.limit ?? 50),
  })
  if (options.screening_result) params.set("screening_result", options.screening_result)
  const body = await requestJson(`/l2/diagnosis-runs/${encodeURIComponent(runId)}/records?${params}`, { signal })
  return {
    total: typeof body.total === "number" ? body.total : 0,
    items: Array.isArray(body.items)
      ? (body.items as Array<{ record_id: string; status: string; screening_result?: string; result: DiagnosisResult }>)
      : [],
  }
}

export {
  L2_HANDOFF_FOCUS_KEY,
  consumeL2HandoffFocus,
  saveL2HandoffFocus,
  type L2HandoffFocus,
} from "./handoffFocus"

export type OnboardingDraft = {
  onboarding_id: string
  configuration_status: string
  dataset_id: string
  pd_revision_id: string
  equipment_type_candidate?: string
  display_name_candidate?: string
  schema_version_candidate?: string
  feature_candidates?: Array<{
    feature_id: string
    name: string
    dtype?: string
    unit?: string | null
    selected?: boolean
    leakage?: boolean
    numeric_count?: number
    missing_count?: number
    source_field_id?: string
  }>
  feature_count?: number
  target_candidate?: string
  normal_label_candidate?: string | null
  normal_label_confirmed?: boolean
  class_labels?: string[]
  class_counts?: Record<string, number>
  class_count?: number
  record_count?: number
  labeled_record_count?: number
  warnings?: string[]
  training_readiness?: {
    ready?: boolean
    has_features?: boolean
    has_normal_label?: boolean
    has_abnormal_label?: boolean
    has_labels?: boolean
    min_samples_ok?: boolean
  }
  already_registered?: boolean
  needs_confirmation?: boolean
}

export async function prepareEquipmentOnboarding(
  payload: {
    dataset_id: string
    pd_revision_id: string
    equipment_group_id?: string
    equipment_category?: string
  },
  signal?: AbortSignal,
): Promise<OnboardingDraft> {
  return (await sendJson("/l2/management/equipment-onboarding/prepare", payload, { signal })) as OnboardingDraft
}

export async function getEquipmentOnboarding(onboardingId: string, signal?: AbortSignal): Promise<OnboardingDraft> {
  return (await requestJson(`/l2/management/equipment-onboarding/${encodeURIComponent(onboardingId)}`, {
    signal,
  })) as OnboardingDraft
}

export async function confirmEquipmentOnboarding(
  onboardingId: string,
  payload: {
    equipment_type?: string
    display_name?: string
    normal_label?: string
    selected_feature_ids?: string[]
    handoff_id?: string
    handoff_item_id?: string
    continue_handoff?: boolean
  },
  signal?: AbortSignal,
): Promise<Record<string, unknown>> {
  return sendJson(`/l2/management/equipment-onboarding/${encodeURIComponent(onboardingId)}/confirm`, payload, {
    signal,
  })
}

async function sendJson(
  url: string,
  body: unknown,
  options: { signal?: AbortSignal } = {},
): Promise<Record<string, unknown>> {
  return requestJson(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal: options.signal,
  })
}

async function requestJson(url: string, init?: RequestInit): Promise<Record<string, unknown>> {
  let response: Response
  try {
    response = await fetch(url, init)
  } catch {
    throw new L2ApiError("network_error", "无法连接后端服务，请确认 FastAPI 已启动。", 0)
  }
  const text = await response.text()
  const body = readBody(text)
  if (!response.ok) throw asError(response.status, body)
  return body
}

function readBody(text: string): L2ApiErrorBody & Record<string, unknown> {
  if (!text) return {}
  try {
    const value: unknown = JSON.parse(text)
    return value && typeof value === "object" ? (value as Record<string, unknown>) : {}
  } catch {
    return { message: "服务器返回了无法读取的内容。" }
  }
}

function asError(status: number, body: L2ApiErrorBody & Record<string, unknown>): L2ApiError {
  const errorCode =
    typeof body.error_code === "string"
      ? body.error_code
      : typeof body.error_type === "string"
        ? body.error_type
        : "request_failed"
  const message = typeof body.message === "string" && body.message ? body.message : "请求没有完成。"
  const details = body.details && typeof body.details === "object" ? body.details : {}
  const requestId = typeof body.request_id === "string" ? body.request_id : null
  return new L2ApiError(errorCode, message, status, details, requestId)
}
