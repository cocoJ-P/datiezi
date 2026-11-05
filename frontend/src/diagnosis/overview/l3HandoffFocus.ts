export const L3_HANDOFF_FOCUS_KEY = "l3_handoff_focus"

export type L3TriggerReason = "auto_review" | "manual_request"

export type L3HandoffFocus = {
  contract_version?: string
  equipment_type?: string
  equipment_id?: string | null
  equipment_group_id?: string | null
  display_name?: string
  record_ids?: string[]
  prepared_dataset_id?: string
  dataset_revision_id?: string
  model_bundle_id?: string | null
  diagnosis_run_id?: string
  screening_result?: string
  trigger_reason?: L3TriggerReason
  conflict_count?: number
  uncertain_count?: number
  abnormal_count?: number
  l3_review_count?: number
  l2_results?: Array<Record<string, unknown>>
  reason?: string
}

export function saveL3HandoffFocus(focus: L3HandoffFocus): void {
  try {
    sessionStorage.setItem(L3_HANDOFF_FOCUS_KEY, JSON.stringify(focus))
  } catch {
    /* ignore */
  }
}

export function consumeL3HandoffFocus(): L3HandoffFocus | null {
  try {
    const raw = sessionStorage.getItem(L3_HANDOFF_FOCUS_KEY)
    if (!raw) return null
    sessionStorage.removeItem(L3_HANDOFF_FOCUS_KEY)
    const value = JSON.parse(raw) as L3HandoffFocus
    return value && typeof value === "object" ? value : null
  } catch {
    return null
  }
}

export function peekL3HandoffFocus(): L3HandoffFocus | null {
  try {
    const raw = sessionStorage.getItem(L3_HANDOFF_FOCUS_KEY)
    if (!raw) return null
    const value = JSON.parse(raw) as L3HandoffFocus
    return value && typeof value === "object" ? value : null
  } catch {
    return null
  }
}
