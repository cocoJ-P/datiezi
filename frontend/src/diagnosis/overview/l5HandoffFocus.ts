/** L5 报告深链：总览 / L4 / L5 工作台 → 报告中心 */

export const L5_REPORT_FOCUS_KEY = "l5_report_focus"

export type L5ReportFocus = {
  l5_run_id?: string
  record_id?: string
  diagnosis_run_id?: string
  analysis_run_id?: string
  l4_run_id?: string
  equipment_type?: string
  reason?: string
}

export function saveL5ReportFocus(focus: L5ReportFocus): void {
  try {
    sessionStorage.setItem(L5_REPORT_FOCUS_KEY, JSON.stringify(focus))
  } catch {
    /* ignore */
  }
}

export function consumeL5ReportFocus(): L5ReportFocus | null {
  try {
    const raw = sessionStorage.getItem(L5_REPORT_FOCUS_KEY)
    if (!raw) return null
    sessionStorage.removeItem(L5_REPORT_FOCUS_KEY)
    const value = JSON.parse(raw) as L5ReportFocus
    return value && typeof value === "object" ? value : null
  } catch {
    return null
  }
}

export function peekL5ReportFocus(): L5ReportFocus | null {
  try {
    const raw = sessionStorage.getItem(L5_REPORT_FOCUS_KEY)
    if (!raw) return null
    const value = JSON.parse(raw) as L5ReportFocus
    return value && typeof value === "object" ? value : null
  } catch {
    return null
  }
}
