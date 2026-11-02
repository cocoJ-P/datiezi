export type PipelineStage = {
  stage_id: string
  name: string
  implemented: boolean
  status: string
  status_label: string
}

export type DatasetSummary = {
  dataset_id: string
  original_filename: string
  file_type: string
  size_bytes: number
  sha256: string
  uploaded_at: string
  parse_status: string
  p0_status: string
  sheet_count: number
  row_count: number
  column_count: number
  template_id: string
}

export type DatasetDetail = DatasetSummary & {
  encoding: string
  delimiter: string
  cell_count: number
  warnings: { error_type: string; message: string }[]
  error: { error_type: string; message: string } | null
  stages: PipelineStage[]
  storage_consistent: boolean
  file_url: string
  duplicate?: boolean
}

export type SheetInfo = {
  sheet_id: string
  sheet_index: number
  name: string
  visibility: string
  max_row: number
  max_column: number
  cell_count: number
  hidden_row_count: number
  merges: string[]
}

export type RawCell = {
  cell_id: string
  column_number: number
  column_id: string
  coordinate: string
  raw_value: string
  cell_type: string
  number_format: string | null
  formula: string | null
  cached_value: string | null
  merge_range: string | null
  parse_status: string
}

export type RawRow = {
  source_row_id: string
  row_number: number
  hidden: boolean
  parse_status: string
  source_line_start: number | null
  source_line_end: number | null
  cells: RawCell[]
}

export type RawPage = {
  dataset_id: string
  sheet_id: string
  sheet_name: string
  total_rows: number
  max_column: number
  offset: number
  limit: number
  row_number: number | null
  rows: RawRow[]
}

export type RegionRole = "data_table" | "note" | "blank" | "unclassified"

export type RegionColumn = {
  column_number: number
  column_id: string
  letter: string
  header: string[]
  header_parts: { row_number: number; raw_value: string; cell_id: string | null; coordinate: string }[]
}

export type RegionRecord = {
  record_index: number
  source_row_id: string
  sheet_id: string
  row_number: number
  hidden: boolean
  parse_status: string
  values: {
    cell_id: string
    column_number: number
    column_id: string
    coordinate: string
    raw_value: string
    cell_type: string
    source_row_id: string
  }[]
}

export type DataRegion = {
  region_id: string | null
  client_region_id: string
  dataset_id: string
  sheet_id: string
  sheet_name: string
  sheet_enabled: boolean
  role: RegionRole
  start_row: number
  end_row: number
  start_column: number
  end_column: number
  header_start_row: number | null
  header_row_count: number
  data_start_row: number | null
  data_end_row: number | null
  detection_method: string
  confidence: string
  confirmation_status: string
  confirmed?: boolean
  record_count: number
  columns: RegionColumn[]
  warnings: { code?: string; message: string }[]
  range_label: string
  sample_rows?: RegionRecord[]
}

export type SheetSetting = {
  sheet_id: string
  name: string
  visibility: string
  enabled: boolean
  max_row: number
  max_column: number
  merges: string[]
}

export type RegionDiff = {
  baseline_run_id: string | null
  baseline_revision: number | null
  added: { sheet_name: string; role: string; range_label: string; record_count: number; header: string }[]
  removed: { sheet_name: string; role: string; range_label: string; record_count: number; header: string }[]
  header_changes: { sheet_name: string; before: string; after: string }[]
  record_count_before: number
  record_count_after: number
  changed_data_row_count: number
  unclassified_cell_count: number
}

export type RegionPlan = {
  dataset_id: string
  persisted: boolean
  preview?: boolean
  applied?: boolean
  rule_id?: string
  rule_summary?: string
  p0_sha256?: string
  sheet_settings: SheetSetting[]
  regions: DataRegion[]
  needs_confirmation?: string[]
  unclassified_cell_count?: number
  status?: string
  downstream_ready?: boolean
  reused?: boolean
  exceptions?: {
    code: string
    message: string
    sheet_id?: string
    sheet_name?: string
    cell_count?: number
    start_row?: number
    end_row?: number
    start_column?: number
    end_column?: number
  }[]
  sheet_summaries?: {
    sheet_id: string
    name: string
    nonempty_cells: number
    covered_cells: number
    auxiliary_cells: number
    data_table_count: number
    empty: boolean
    enabled?: boolean
    visibility?: string
  }[]
  diff?: RegionDiff
  run?: {
    run_id: string
    revision: number
    status: string
    created_at: string
    rule_id: string
    p0_sha256: string
    region_count: number
    unclassified_cell_count: number
    record_count: number
  } | null
}

export type RegionRows = {
  region_id: string
  dataset_id: string
  sheet_id: string
  sheet_name: string
  role: string
  range_label: string
  columns: RegionColumn[]
  total_records: number
  offset: number
  limit: number
  rows: RegionRecord[]
}

export type EquipmentFieldRegion = {
  region_id: string
  sheet_id: string
  sheet_name: string
  range_label: string
  record_count: number
  candidates: { column_number: number; column_id: string; header: string; match: string; alias: string }[]
  columns: { column_number: number; column_id: string; header: string[]; letter?: string }[]
  suggested_column_id: string | null
  selected_field?: string | null
  confirmation_status: string
  confidence_level?: string
  recognition_source?: string
  warning_codes?: string[]
  requires_review?: boolean
}

export type EquipmentFieldPlan = {
  dataset_id: string
  p1_run_id: string
  rule_summary: string
  business_count: number
  regions: EquipmentFieldRegion[]
  cross_sheet_decision_required: boolean
  default_cross_sheet?: string
  potential_cross_sheet_matches?: { match_key: string; sheet_count: number }[]
  aliases: string[]
}

export type EquipmentGroup = {
  group_id: string | null
  equipment_id: string
  match_key: string
  scope: string
  sheet_id: string | null
  sheet_name?: string
  asset_id: string | null
  record_count: number
  original_values: string[]
  label: string
  mapping_rule_id: string
}

export type GroupingField = {
  column_id: string
  column_number: number
  header: string
  raw_value: string
  cell_id: string | null
  coordinate: string | null
  cell_type: string | null
  present: boolean
}

export type GroupingRow = {
  run_id: string
  group_id: string | null
  equipment_id: string
  group_label: string
  source_row_id: string
  region_id: string
  sheet_id: string
  sheet_name: string
  row_number: number
  membership_status: string
  ungroup_reason: string
  original_equipment_value: string
  grouping_value: string
  match_key: string
  column_id: string
  field_header: string
  equipment_cell_id: string | null
  coordinate: string
  override_applied: boolean
  asset_id: string | null
  mapping_rule_id: string
  fields: GroupingField[]
}

export type GroupingBalance = {
  business_count: number
  grouped_count: number
  ungrouped_count: number
  equipment_count: number
  sheet_count: number
  ok: boolean
}

export type GroupingPlan = {
  dataset_id: string
  applied?: boolean
  persisted: boolean
  preview?: boolean
  reused?: boolean
  status?: string
  apply_allowed?: boolean
  needs_confirmation?: string[]
  cross_sheet_decision_required?: boolean
  downstream_ready?: boolean
  rule_summary?: string
  balance: GroupingBalance | null
  summary?: {
    status: string
    equipment_count: number
    grouped_count: number
    ungrouped_count: number
    ungrouped_ratio: number
    warning_count: number
    warning_codes: string[]
    active_revision?: number | null
  } | null
  warnings?: { code: string; severity?: string; sheet_name?: string; range_label?: string; [key: string]: unknown }[]
  potential_cross_sheet_matches?: { match_key: string; sheet_count: number; status?: string }[]
  region_stats?: {
    region_id: string
    sheet_id: string
    sheet_name: string
    range_label: string
    selected_field?: string | null
    selected_header?: string
    requires_review?: boolean
    grouped_count?: number
    ungrouped_count?: number
  }[]
  groups: EquipmentGroup[]
  group_total?: number
  group_offset?: number
  group_limit?: number
  sheet_links: { sheet_id: string; sheet_name: string; grouped_count: number; ungrouped_count: number; equipment_count: number; equipment_ids: string[] }[]
  run?: {
    run_id: string
    revision: number
    status: string
    rule_id: string
    equipment_count: number
    grouped_count: number
    ungrouped_count: number
    business_count: number
    sheet_count: number
  } | null
  config?: {
    match_mode: string | null
    cross_sheet: string | null
    regions: { region_id: string; column_id: string | null; confirmed: boolean }[]
    overrides: { source_row_id: string; action: string; equipment_value?: string }[]
  }
  diff?: {
    baseline_revision: number | null
    equipment_count_before: number
    equipment_count_after: number
    grouped_count_before: number
    grouped_count_after: number
    ungrouped_count_before: number
    ungrouped_count_after: number
    changed_record_count: number
  }
  sample_rows?: GroupingRow[]
}

export type GroupingPage = {
  total_records: number
  offset: number
  limit: number
  rows: GroupingRow[]
  group?: EquipmentGroup
  run_id?: string
}

export type SchemaField = {
  field_id: string
  label: string
  description: string
  physical_meaning: string
  role: string
  target_type: string
  target_unit: string
  required: boolean
  eligibility: string
  aliases: string[]
}

export type SchemaMapping = {
  source_column_id: string
  region_id: string
  sheet_id: string
  sheet_name: string
  column_id: string
  column_number: number
  header: string
  standard_field_id: string
  standard_label: string
  description: string
  physical_meaning: string
  role: string
  eligibility: string
  source_unit: string
  target_unit: string
  target_type: string
  match_method: string
  match_reason: string
  confirmation_status: string
  candidates: string[]
  samples: string[]
  sample_limited?: boolean
}

export type SchemaSummary = {
  column_count: number
  matched_count: number
  auto_matched_count: number
  confirmed_count: number
  pending_count: number
  unmapped_count: number
  conflict_count: number
  warning_count: number
  label_fields: string[]
  diagnostic_fields: string[]
}

export type SchemaIssue = {
  severity: string
  code: string
  message: string
  source_column_id: string
  standard_field_id: string
  region_id: string
}

export type SchemaPlan = {
  applied?: boolean
  persisted: boolean
  template_id?: string
  template_version?: string
  template?: { template_id: string; version: string; name: string; summary: string; fields: SchemaField[] }
  summary: SchemaSummary | null
  mappings: SchemaMapping[]
  issues: SchemaIssue[]
  apply_allowed?: boolean
  run?: { run_id: string; revision: number; p1_run_id?: string; p2_run_id?: string | null } | null
  p1_run_id?: string
  p2_run_id?: string | null
  rule_id?: string
  diff?: {
    baseline_revision: number | null
    changed_count: number
    note: string
    changes: { source_column_id: string; header: string; change: string }[]
  }
  sample_rows?: SchemaRecord[]
  sample_limited?: boolean
}

export type SchemaRecord = {
  source_row_id: string
  region_id: string
  sheet_id: string
  sheet_name: string
  row_number: number
  p3_run_id: string | null
  p2_run_id: string | null
  equipment_group_id: string | null
  equipment_value: string
  fields: {
    source_column_id: string
    column_id: string
    header: string
    standard_field_id: string
    standard_label: string
    role: string
    raw_value: string
    cell_id: string | null
    coordinate: string | null
  }[]
}

export type SchemaPage = {
  total_records: number
  offset: number
  limit: number
  rows: SchemaRecord[]
  view: string
  values_are_raw_text: boolean
  run_id: string
}

export type StandardizationField = {
  source_column_id: string
  region_id: string
  sheet_id: string
  sheet_name: string
  column_id: string
  column_number: number
  header: string
  standard_field_id: string
  standard_label: string
  role: string
  eligibility: string
  confirmation_status: string
  source_unit: string
  target_unit: string
  target_type: string
  dimension: string
  enabled: boolean
}

export type StandardizationValue = {
  source_column_id: string
  column_id: string
  header: string
  standard_field_id: string
  standard_label: string
  confirmation_status: string
  raw_value: string
  cell_type: string
  target_type: string
  source_unit: string
  target_unit: string
  role: string
  eligibility: string
  normalized_value: string | number | boolean | null
  normalized_text: string
  status: string
  rule_id: string
  unit_rule: string
  timezone_status: string
  precision: string
  message: string
  cell_id: string | null
  coordinate: string | null
  value_changed: boolean
  type_changed: boolean
  changed: boolean
}

export type StandardizationRecord = {
  source_row_id: string
  region_id: string
  sheet_id: string
  sheet_name: string
  row_number: number
  p3_run_id: string
  fields: StandardizationValue[]
}

export type StandardizationIssue = {
  issue_id?: number
  severity?: string
  code: string
  message: string
  source_column_id: string
  source_row_id?: string
  region_id?: string
  sheet_id?: string
  sheet_name?: string
  row_number?: number
  column_number?: number
  column_id?: string
  cell_id?: string
  coordinate?: string
  raw_value?: string
  cell_type?: string
  target_type?: string
  rule_id?: string
  sample_limited?: boolean
}

export type StandardizationSummary = {
  record_count: number
  output_record_count: number
  input_record_count?: number
  field_count: number
  confirmed_field_count: number
  unconfirmed_field_count: number
  converted_field_count: number
  scanned_cells?: number
  scanned_balance_ok?: boolean
  success_cells: number
  missing_cells: number
  type_failure_cells: number
  time_failure_cells: number
  unit_failure_cells: number
  unconfirmed_cells: number | null
  unit_hold_cells?: number | null
  not_scanned_note?: string
  unit_unconfirmed_fields: number
  value_change_cells: number
  type_change_cells: number
  partial: boolean
  publish_ready: boolean
  preview_scope: string
  counts_cover?: string
  sample_record_count?: number
  sample_limit?: number
  note?: string
  p3_run_id: string
  excel_date_system: string
  label_fields?: string[]
  diagnostic_fields?: string[]
  label_eligibility?: string
}

export type StandardizationPlan = {
  dataset_id: string
  persisted: boolean
  applied?: boolean
  preview?: boolean
  sample_limited?: boolean
  preview_scope?: string
  p3_run_id: string
  p3_revision?: number
  template_id: string
  template_version: string
  rule_id: string
  excel_date_system: string
  apply_allowed: boolean
  partial: boolean
  issues: StandardizationIssue[]
  fields: StandardizationField[]
  summary: StandardizationSummary | null
  applied_summary?: StandardizationSummary
  rows?: StandardizationRecord[]
  sample_issues?: StandardizationIssue[]
  run?: { run_id: string; revision: number; status: string; p3_run_id: string; publish_ready: boolean; created_at: string } | null
}

export type StandardizationPage = {
  run_id: string
  view: string
  offset: number
  limit: number
  total_records: number
  rows: StandardizationRecord[]
  preview_scope: string
  value_change_cells?: number
  type_change_cells?: number
  note?: string
}

export type CellDetail = RawCell & {
  dataset_id: string
  original_filename: string
  sheet_name: string
  sheet_visibility: string
  source_row_id: string
  row_number: number
  hidden: boolean
  source_line_start: number | null
  source_line_end: number | null
  row_parse_status: string
}

export type PreparePlan = {
  applied?: boolean
  run_id: string
  dataset_id: string
  revision?: number
  status: string
  version_id?: string | null
  p4_run_id?: string
  summary?: Record<string, unknown>
  readinessByAnalysisType?: Record<string, string>
  config?: Record<string, unknown>
}

export type PreparedRow = {
  source_row_id: string
  equipment_id: string
  timestamp_text: string
  features: { input_features?: Record<string, unknown>; [key: string]: unknown }
  validity?: Record<string, unknown>
  quality?: Record<string, unknown>
}
