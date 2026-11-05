import type { L2ViewId } from "./types"

export const L2_HANDOFF_FOCUS_KEY = "l2_handoff_focus"

export type L2HandoffFocus = {
  view?: L2ViewId
  dataset_id?: string
  equipment_type?: string
  ped_id?: string
  prepared_dataset_id?: string
  dataset_revision_id?: string
  diagnosis_run_id?: string
  record_id?: string
  reason?: string
  onboarding_id?: string
  pd_revision_id?: string
  handoff_id?: string
  handoff_item_id?: string
  equipment_category?: string
}

export function saveL2HandoffFocus(focus: L2HandoffFocus): void {
  try {
    sessionStorage.setItem(L2_HANDOFF_FOCUS_KEY, JSON.stringify(focus))
  } catch {
    /* ignore */
  }
}

export function consumeL2HandoffFocus(): L2HandoffFocus | null {
  try {
    const raw = sessionStorage.getItem(L2_HANDOFF_FOCUS_KEY)
    if (!raw) return null
    sessionStorage.removeItem(L2_HANDOFF_FOCUS_KEY)
    const value = JSON.parse(raw) as L2HandoffFocus
    return value && typeof value === "object" ? value : null
  } catch {
    return null
  }
}
