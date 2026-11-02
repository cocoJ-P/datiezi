import type {
  CellDetail,
  DataRegion,
  DatasetDetail,
  DatasetSummary,
  EquipmentFieldPlan,
  GroupingPage,
  GroupingPlan,
  PipelineStage,
  PreparePlan,
  PreparedRow,
  SchemaPage,
  SchemaPlan,
  StandardizationPage,
  StandardizationPlan,
  RawPage,
  RegionPlan,
  RegionRows,
  SheetInfo,
  SheetSetting,
} from "./types"

export class L1ApiError extends Error {
  errorType: string
  status: number

  constructor(errorType: string, message: string, status: number) {
    super(message)
    this.errorType = errorType
    this.status = status
  }
}

export async function readPipeline(signal?: AbortSignal): Promise<{ template_id: string; stages: PipelineStage[] }> {
  const body = await requestJson("/api/l1/pipeline", { signal })
  const stages = Array.isArray(body.stages) ? (body.stages as PipelineStage[]) : []
  return { template_id: typeof body.template_id === "string" ? body.template_id : "", stages }
}

export async function listDatasets(signal?: AbortSignal): Promise<DatasetSummary[]> {
  const body = await requestJson("/api/l1/datasets", { signal })
  return Array.isArray(body.datasets) ? (body.datasets as DatasetSummary[]) : []
}

export async function getDataset(datasetId: string, signal?: AbortSignal): Promise<DatasetDetail> {
  return (await requestJson(`/api/l1/datasets/${encodeURIComponent(datasetId)}`, { signal })) as DatasetDetail
}

export async function listSheets(datasetId: string, signal?: AbortSignal): Promise<SheetInfo[]> {
  const body = await requestJson(`/api/l1/datasets/${encodeURIComponent(datasetId)}/sheets`, { signal })
  return Array.isArray(body.sheets) ? (body.sheets as SheetInfo[]) : []
}

export async function listRawRows(
  datasetId: string,
  query: { sheetId?: string; offset: number; limit: number; row?: number },
  signal?: AbortSignal,
): Promise<RawPage> {
  const params = new URLSearchParams({ offset: String(query.offset), limit: String(query.limit) })
  if (query.sheetId) params.set("sheet_id", query.sheetId)
  if (query.row) params.set("row", String(query.row))
  return (await requestJson(`/api/l1/datasets/${encodeURIComponent(datasetId)}/raw-rows?${params.toString()}`, { signal })) as RawPage
}

export async function getRawCell(datasetId: string, cellId: string, signal?: AbortSignal): Promise<CellDetail> {
  return (await requestJson(`/api/l1/datasets/${encodeURIComponent(datasetId)}/raw-cells/${encodeURIComponent(cellId)}`, { signal })) as CellDetail
}

export async function suggestRegions(datasetId: string, signal?: AbortSignal): Promise<RegionPlan> {
  return (await requestJson(`/api/l1/datasets/${encodeURIComponent(datasetId)}/regions/suggestions`, { signal })) as RegionPlan
}

export async function listAppliedRegions(datasetId: string, runId?: string, signal?: AbortSignal): Promise<RegionPlan> {
  const params = new URLSearchParams()
  if (runId) params.set("run_id", runId)
  const query = params.toString()
  return (await requestJson(`/api/l1/datasets/${encodeURIComponent(datasetId)}/regions${query ? `?${query}` : ""}`, { signal })) as RegionPlan
}

export async function previewRegions(datasetId: string, config: RegionConfig): Promise<RegionPlan> {
  return (await sendJson(`/api/l1/datasets/${encodeURIComponent(datasetId)}/regions/preview`, config)) as RegionPlan
}

export async function applyRegions(datasetId: string, config: RegionConfig): Promise<RegionPlan> {
  return (await sendJson(`/api/l1/datasets/${encodeURIComponent(datasetId)}/regions/apply`, config)) as RegionPlan
}

export async function listRegionRows(regionId: string, offset: number, limit: number, signal?: AbortSignal): Promise<RegionRows> {
  const params = new URLSearchParams({ offset: String(offset), limit: String(limit) })
  return (await requestJson(`/api/l1/regions/${encodeURIComponent(regionId)}/rows?${params.toString()}`, { signal })) as RegionRows
}

export async function equipmentFields(datasetId: string, signal?: AbortSignal): Promise<EquipmentFieldPlan> {
  return (await requestJson(`/api/l1/datasets/${encodeURIComponent(datasetId)}/equipment-fields`, { signal })) as EquipmentFieldPlan
}

export async function previewGrouping(datasetId: string, config: GroupingConfig): Promise<GroupingPlan> {
  return (await sendJson(`/api/l1/datasets/${encodeURIComponent(datasetId)}/grouping/preview`, config)) as GroupingPlan
}

export async function applyGrouping(datasetId: string, config: GroupingConfig): Promise<GroupingPlan> {
  return (await sendJson(`/api/l1/datasets/${encodeURIComponent(datasetId)}/grouping/apply`, config)) as GroupingPlan
}

export async function groupingSummary(datasetId: string, signal?: AbortSignal): Promise<GroupingPlan> {
  return (await requestJson(`/api/l1/datasets/${encodeURIComponent(datasetId)}/grouping/summary`, { signal })) as GroupingPlan
}

export async function listEquipmentGroups(
  datasetId: string,
  options: { limit?: number; offset?: number; signal?: AbortSignal } = {},
): Promise<GroupingPlan> {
  const params = new URLSearchParams({
    limit: String(options.limit ?? 50),
    offset: String(options.offset ?? 0),
  })
  return (await requestJson(`/api/l1/datasets/${encodeURIComponent(datasetId)}/equipment-groups?${params.toString()}`, { signal: options.signal })) as GroupingPlan
}

export async function listGroupRows(groupId: string, offset: number, limit: number, signal?: AbortSignal): Promise<GroupingPage> {
  const params = new URLSearchParams({ offset: String(offset), limit: String(limit) })
  return (await requestJson(`/api/l1/equipment-groups/${encodeURIComponent(groupId)}/rows?${params.toString()}`, { signal })) as GroupingPage
}

export async function listUngroupedRows(datasetId: string, offset: number, limit: number, signal?: AbortSignal): Promise<GroupingPage> {
  const params = new URLSearchParams({ offset: String(offset), limit: String(limit) })
  return (await requestJson(`/api/l1/datasets/${encodeURIComponent(datasetId)}/ungrouped-rows?${params.toString()}`, { signal })) as GroupingPage
}

export async function listGroupingRows(datasetId: string, query: { offset: number; limit: number; sheetId?: string }, signal?: AbortSignal): Promise<GroupingPage> {
  const params = new URLSearchParams({ offset: String(query.offset), limit: String(query.limit) })
  if (query.sheetId) params.set("sheet_id", query.sheetId)
  return (await requestJson(`/api/l1/datasets/${encodeURIComponent(datasetId)}/grouping-rows?${params.toString()}`, { signal })) as GroupingPage
}

export async function listSchemaTemplates(signal?: AbortSignal): Promise<SchemaPlan["template"][]> {
  const body = await requestJson("/api/l1/schema/templates", { signal })
  return Array.isArray(body.templates) ? (body.templates as SchemaPlan["template"][]) : []
}

export async function schemaSuggestions(datasetId: string, signal?: AbortSignal): Promise<SchemaPlan> {
  return (await requestJson(`/api/l1/datasets/${encodeURIComponent(datasetId)}/schema/suggestions`, { signal })) as SchemaPlan
}

export async function schemaMappings(datasetId: string, signal?: AbortSignal): Promise<SchemaPlan> {
  return (await requestJson(`/api/l1/datasets/${encodeURIComponent(datasetId)}/schema/mappings`, { signal })) as SchemaPlan
}

export async function previewSchema(datasetId: string, config: SchemaDraft): Promise<SchemaPlan> {
  return (await sendJson(`/api/l1/datasets/${encodeURIComponent(datasetId)}/schema/preview`, config)) as SchemaPlan
}

export async function applySchema(datasetId: string, config: SchemaDraft): Promise<SchemaPlan> {
  return (await sendJson(`/api/l1/datasets/${encodeURIComponent(datasetId)}/schema/apply`, config)) as SchemaPlan
}

export async function listSchemaRows(runId: string, offset: number, limit: number, view: string, signal?: AbortSignal): Promise<SchemaPage> {
  const params = new URLSearchParams({ offset: String(offset), limit: String(limit), view })
  return (await requestJson(`/api/l1/schema-runs/${encodeURIComponent(runId)}/rows?${params.toString()}`, { signal })) as SchemaPage
}

export type SchemaDraft = {
  batch_confirm?: boolean
  mappings?: {
    source_column_id: string
    standard_field_id?: string
    role?: string
    eligibility?: string
    source_unit?: string
    target_unit?: string
    target_type?: string
    confirmation_status?: string
  }[]
}

export type StandardizationDraft = {
  decimal_style?: string
  thousands_separator?: string
  missing_tokens?: string[]
  datetime_formats?: string[]
  source_timezone?: string
  target_timezone?: string
  failure_policy?: string
  sample_records?: number
  fields?: { source_column_id: string; enabled?: boolean; decimal_style?: string }[]
}

export async function standardizationConfig(datasetId: string, signal?: AbortSignal): Promise<StandardizationPlan> {
  return (await requestJson(`/api/l1/datasets/${encodeURIComponent(datasetId)}/standardization/config`, { signal })) as StandardizationPlan
}

export async function previewStandardization(datasetId: string, config: StandardizationDraft): Promise<StandardizationPlan> {
  return (await sendJson(`/api/l1/datasets/${encodeURIComponent(datasetId)}/standardization/preview`, config)) as StandardizationPlan
}

export async function validateStandardization(datasetId: string, config: StandardizationDraft): Promise<Record<string, unknown>> {
  return sendJson(`/api/l1/datasets/${encodeURIComponent(datasetId)}/standardization/validate`, config)
}

export async function applyStandardization(datasetId: string, config: StandardizationDraft): Promise<StandardizationPlan> {
  return (await sendJson(`/api/l1/datasets/${encodeURIComponent(datasetId)}/standardization/apply`, config)) as StandardizationPlan
}

export async function listStandardizationRows(runId: string, offset: number, limit: number, view: string, signal?: AbortSignal): Promise<StandardizationPage> {
  const params = new URLSearchParams({ offset: String(offset), limit: String(limit), view })
  return (await requestJson(`/api/l1/standardization-runs/${encodeURIComponent(runId)}/rows?${params.toString()}`, { signal })) as StandardizationPage
}

export async function listStandardizationDiff(runId: string, offset: number, limit: number, signal?: AbortSignal): Promise<StandardizationPage> {
  const params = new URLSearchParams({ offset: String(offset), limit: String(limit) })
  return (await requestJson(`/api/l1/standardization-runs/${encodeURIComponent(runId)}/diff?${params.toString()}`, { signal })) as StandardizationPage
}

export async function listStandardizationIssues(runId: string, offset: number, limit: number, signal?: AbortSignal): Promise<{ total: number; issues: StandardizationPlan["issues"] }> {
  const params = new URLSearchParams({ offset: String(offset), limit: String(limit) })
  return (await requestJson(`/api/l1/standardization-runs/${encodeURIComponent(runId)}/issues?${params.toString()}`, { signal })) as { total: number; issues: StandardizationPlan["issues"] }
}

export async function runPrepare(datasetId: string, config: Record<string, unknown> = {}): Promise<PreparePlan> {
  return (await sendJson(`/api/l1/datasets/${encodeURIComponent(datasetId)}/prepare`, config)) as unknown as PreparePlan
}

/** 上传后自动续跑 P3→P8（P0–P2 已在接入时完成）。 */
export async function runFullL1Pipeline(
  datasetId: string,
  onProgress?: (step: string) => void,
  onStage?: (detail: DatasetDetail) => void,
): Promise<{ prepare: PreparePlan }> {
  const refresh = async (message: string) => {
    onProgress?.(message)
    if (onStage) onStage(await getDataset(datasetId))
  }
  await refresh("正在执行 Schema 映射（P3）…")
  await applySchema(datasetId, { batch_confirm: true })
  await refresh("正在执行数据标准化（P4）…")
  await applyStandardization(datasetId, {})
  await refresh("正在执行数据准备（P5–P8）…")
  const prepare = await runPrepare(datasetId, {})
  await refresh("L1 数据处理已完成。")
  return { prepare }
}

export async function getPrepareLatest(datasetId: string, signal?: AbortSignal): Promise<PreparePlan & { applied?: boolean }> {
  return (await requestJson(`/api/l1/datasets/${encodeURIComponent(datasetId)}/prepare/latest`, { signal })) as PreparePlan & { applied?: boolean }
}

export async function listPrepareStages(runId: string, signal?: AbortSignal): Promise<{ stages: { stage_id: string; status: string; summary?: Record<string, unknown> | null }[] }> {
  return (await requestJson(`/api/l1/prepare-runs/${encodeURIComponent(runId)}/stages`, { signal })) as {
    stages: { stage_id: string; status: string; summary?: Record<string, unknown> | null }[]
  }
}

export async function listPrepareIssues(
  runId: string,
  options: { offset?: number; limit?: number; stage?: string; code?: string } = {},
  signal?: AbortSignal,
): Promise<{ total: number; issues: { code: string; severity: string; message: string; equipment_id?: string; source_row_id?: string }[] }> {
  const params = new URLSearchParams({
    offset: String(options.offset ?? 0),
    limit: String(options.limit ?? 50),
  })
  if (options.stage) params.set("stage", options.stage)
  if (options.code) params.set("code", options.code)
  return (await requestJson(`/api/l1/prepare-runs/${encodeURIComponent(runId)}/issues?${params.toString()}`, { signal })) as {
    total: number
    issues: { code: string; severity: string; message: string; equipment_id?: string; source_row_id?: string }[]
  }
}

export async function getPrepareReport(runId: string, signal?: AbortSignal): Promise<{ report: Record<string, unknown> }> {
  return (await requestJson(`/api/l1/prepare-runs/${encodeURIComponent(runId)}/report`, { signal })) as { report: Record<string, unknown> }
}

export async function listPreparedRows(
  versionId: string,
  options: { offset?: number; limit?: number; equipmentId?: string; start?: string; end?: string } = {},
  signal?: AbortSignal,
): Promise<{ total_records: number; rows: PreparedRow[] }> {
  const params = new URLSearchParams({
    offset: String(options.offset ?? 0),
    limit: String(options.limit ?? 50),
  })
  if (options.equipmentId) params.set("equipment_id", options.equipmentId)
  if (options.start) params.set("start", options.start)
  if (options.end) params.set("end", options.end)
  return (await requestJson(`/api/l1/prepared-datasets/${encodeURIComponent(versionId)}/rows?${params.toString()}`, { signal })) as {
    total_records: number
    rows: PreparedRow[]
  }
}

export type GroupingConfig = {
  use_suggestions?: boolean
  match_mode: string
  cross_sheet: string
  regions: { region_id: string; column_id: string | null; confirmed: boolean }[]
  ledger: { match_key: string; asset_id: string; sheet_id?: string | null }[]
  overrides: { source_row_id: string; action: string; equipment_value?: string }[]
}

export type RegionConfig = {
  sheets?: Pick<SheetSetting, "sheet_id" | "enabled">[]
  regions?: DataRegion[]
  source?: string
  accept_review?: boolean
}

export function uploadDataset(file: File, options: { encoding: string; delimiter: string }, onProgress: (ratio: number) => void): Promise<DatasetDetail> {
  return new Promise((resolve, reject) => {
    const params = new URLSearchParams({ filename: file.name })
    if (options.encoding && options.encoding !== "auto") params.set("encoding", options.encoding)
    if (options.delimiter && options.delimiter !== "auto") params.set("delimiter", options.delimiter)
    const xhr = new XMLHttpRequest()
    xhr.open("POST", `/api/l1/datasets?${params.toString()}`)
    xhr.setRequestHeader("Content-Type", "application/octet-stream")
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && event.total > 0) onProgress(event.loaded / event.total)
    }
    xhr.onload = () => {
      const body = readBody(xhr.responseText)
      if (xhr.status >= 200 && xhr.status < 300) resolve(body as DatasetDetail)
      else reject(asError(xhr.status, body))
    }
    xhr.onerror = () => reject(new L1ApiError("network_error", "上传没有完成，网络中断或服务器没有响应。", 0))
    xhr.onabort = () => reject(new L1ApiError("upload_aborted", "上传已中断。", 0))
    xhr.send(file)
  })
}

async function sendJson(url: string, body: unknown): Promise<Record<string, unknown>> {
  return requestJson(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  })
}

async function requestJson(url: string, init?: RequestInit): Promise<Record<string, unknown>> {
  const response = await fetch(url, init)
  const body = readBody(await response.text())
  if (!response.ok) throw asError(response.status, body)
  return body
}

function readBody(text: string): Record<string, unknown> {
  if (!text) return {}
  try {
    const value: unknown = JSON.parse(text)
    return value && typeof value === "object" ? (value as Record<string, unknown>) : {}
  } catch {
    return { message: "服务器返回了无法读取的内容。" }
  }
}

function asError(status: number, body: Record<string, unknown>): L1ApiError {
  const errorType = typeof body.error_type === "string" ? body.error_type : "request_failed"
  const message = typeof body.message === "string" && body.message ? body.message : "请求没有完成。"
  return new L1ApiError(errorType, message, status)
}
