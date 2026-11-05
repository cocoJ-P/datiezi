export type L2ViewId = "diagnosis" | "models" | "training"

export type EquipmentTypeSummary = {
  equipment_type: string
  display_name: string
  schema_version: string
  feature_count: number
  class_count: number
  class_labels: string[]
  normal_label: string
  binary_labels: string[]
  feature_columns: string[]
  has_active_model: boolean
  active_model_bundle_id: string | null
}

export type DatasetSummary = {
  dataset_id: string
  prepared_dataset_id?: string | null
  revision_id?: string | null
  equipment_type: string
  schema_version?: string | null
  source_type: "legacy_csv" | "l1_prepared" | string
  release_level?: string | null
  release_status?: string | null
  training_ready?: boolean
  inference_ready?: boolean
  record_count?: number | null
  feature_count?: number | null
  label_count?: number | null
  warning_count?: number
  registration_status?: string
  l1_prepared_version_id?: string | null
  created_at?: string | null
}

export type PedRecordRow = {
  record_id: string
  equipment_id?: string | null
  equipment_type?: string | null
  collection_time?: string | null
  source_row_id?: string | null
  has_label?: boolean | string | number | null
}

export type PedRowsPage = {
  prepared_dataset_id?: string
  total?: number
  total_records?: number
  offset: number
  limit: number
  rows: PedRecordRow[]
}

export type ProbabilityMap = Record<string, number>

export type DiagnosisResult = {
  record_id?: string | null
  equipment_type: string
  model_bundle_id?: string
  schema_version?: string
  prepared_dataset_id?: string
  dataset_revision_id?: string
  binary_classifier: {
    predicted_label?: string
    probabilities: ProbabilityMap
    p_abnormal?: number
    calibration_status?: string | null
  }
  multiclass_classifier: {
    predicted_label?: string
    probabilities: ProbabilityMap
    ranked_classes?: { label: string; probability: number }[]
    top1_probability?: number
    top2_probability?: number
    top1_top2_margin?: number
    p_abnormal_aggregate?: number
    calibration_status?: string | null
  }
  consistency: {
    p_a_abnormal?: number
    p_b_abnormal?: number
    probability_gap?: number
    binary_direction?: string
    multiclass_aggregate_direction?: string
    multiclass_top1?: string
  }
  decision: {
    screening_result?: string
    preliminary_fault?: string
    status?: string
    route?: string
    reason_codes?: string[]
  }
  spectral_reference?: {
    fault_class?: string
    description?: string
    related_features?: string[]
    source?: string
    template_version?: string
    verification_status?: string
  } | null
  calibration_status?: Record<string, string> | string | null
  inference_time_ms?: number
}

export type ActiveStatus = {
  equipment_type: string
  active_model_bundle_id: string | null
  runtime_model_bundle_id?: string | null
  consistent?: boolean
  model_loaded?: boolean
  activated_at?: string | null
  compatibility_mode?: boolean
}

export type ModelSummary = {
  model_bundle_id: string
  equipment_type: string
  status: string
  feature_schema_version?: string | null
  dataset_id?: string | null
  dataset_revision_id?: string | null
  training_job_id?: string | null
  parent_model_bundle_id?: string | null
  created_at?: string | null
  registered_at?: string | null
  activated_at?: string | null
  metrics_summary?: Record<string, number | null> | null
  calibration_status?: Record<string, string> | null
  bundle_path?: string
}

export type ModelDetail = ModelSummary & {
  algorithms?: { binary?: string; multiclass?: string }
  integrity_ok?: boolean
  integrity_check?: { ok?: boolean; error?: string; verify_report?: unknown }
}

export type TrainingJob = {
  job_id: string
  equipment_type: string
  dataset_id: string
  dataset_revision_id?: string
  status: string
  train_mode?: "new" | "retrain" | string
  parent_model_bundle_id?: string | null
  created_at?: string
  started_at?: string | null
  finished_at?: string | null
  model_bundle_id?: string | null
  error_code?: string | null
  error_message?: string | null
  log_path?: string | null
  training_config_json?: string
  source_data_hash?: string
}

export type TrainingJobLogs = {
  job_id: string
  log_path?: string | null
  max_lines: number
  truncated: boolean
  lines: string[]
}

export type L2ApiErrorBody = {
  error_code?: string
  error_type?: string
  message?: string
  details?: Record<string, unknown>
  request_id?: string | null
}
