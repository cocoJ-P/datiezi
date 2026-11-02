import type { ParsedTable } from "./parseTable"

export interface FieldSpec {
  id: string
  label: string
  patterns: RegExp[]
  numeric?: boolean
}

export const INPUT_FIELDS: FieldSpec[] = [
  { id: "equipment", label: "设备编号", patterns: [/设备编号/, /设备代码/, /设备名称/, /^equipment$/i, /^device/i] },
  { id: "time", label: "采集时间", patterns: [/采集时间/, /时间戳/, /^time$/i, /timestamp/i, /日期时间/] },
  { id: "speed", label: "转速", patterns: [/转速/, /rpm/i], numeric: true },
  { id: "current", label: "电流", patterns: [/电流/, /\bA\b/], numeric: true },
  { id: "rms", label: "振动 RMS", patterns: [/振动均方根/, /振动 rms/i, /rms/i, /振动/], numeric: true },
  { id: "bearingTemp", label: "轴承温度", patterns: [/轴承温度/, /温度/], numeric: true },
  { id: "bpfo", label: "BPFO 幅值", patterns: [/bpfo/i], numeric: true },
  { id: "bpfi", label: "BPFI 幅值", patterns: [/bpfi/i], numeric: true },
  { id: "bsf", label: "BSF 幅值", patterns: [/bsf/i], numeric: true },
]

export const WITHHELD_FIELDS: FieldSpec[] = [
  { id: "faultType", label: "故障类型", patterns: [/故障类型/] },
  { id: "root", label: "根因分析", patterns: [/根因/] },
  { id: "severity", label: "故障严重度", patterns: [/严重度/, /严重程度/] },
  { id: "action", label: "建议措施", patterns: [/建议措施/, /处置建议/] },
  { id: "state", label: "设备状态", patterns: [/设备状态/] },
  { id: "confidence", label: "诊断置信度", patterns: [/置信度/] },
]

export type FieldMapping = Record<string, string | null>

export function suggestMapping(columns: string[]): { mapping: FieldMapping; withheld: string[] } {
  const used = new Set<string>()
  const withheld = columns.filter((column) => WITHHELD_FIELDS.some((field) => field.patterns.some((pattern) => pattern.test(column))))
  withheld.forEach((column) => used.add(column))
  const mapping: FieldMapping = {}
  for (const field of INPUT_FIELDS) {
    const match = columns.find((column) => !used.has(column) && field.patterns.some((pattern) => pattern.test(column)))
    mapping[field.id] = match ?? null
    if (match) used.add(match)
  }
  return { mapping, withheld }
}

export function inspectTable(table: ParsedTable, mapping: FieldMapping, withheld: string[]): string[] {
  const issues: string[] = []
  if (!mapping.equipment) issues.push("没有映射设备编号。多设备文件将无法拆成子任务。")
  if (!mapping.time) issues.push("没有映射采集时间，不能确定分析时间范围。")
  const columnIndex = new Map(table.columns.map((column, index) => [column, index]))
  for (const field of INPUT_FIELDS) {
    const column = mapping[field.id]
    if (!column || !field.numeric) continue
    const index = columnIndex.get(column)
    if (index === undefined) continue
    const values = table.rows.map((row) => row[index]).filter((value) => value)
    const invalid = values.filter((value) => !Number.isFinite(Number(value))).length
    if (values.length > 0 && invalid / values.length > 0.2) issues.push(`${field.label} 有较多非数值，单位或格式可能不一致。`)
    if (values.length < table.rows.length) issues.push(`${field.label} 存在空值。`)
  }
  if (mapping.time) {
    const index = columnIndex.get(mapping.time)
    if (index !== undefined) {
      const bad = table.rows.filter((row) => row[index] && Number.isNaN(Date.parse(row[index].replace(/\//g, "-")))).length
      if (bad > 0) issues.push(`采集时间有 ${bad} 行无法识别。`)
    }
  }
  const overlap = Object.values(mapping).filter((column): column is string => !!column && withheld.includes(column))
  if (overlap.length > 0) issues.push(`答案字段被映射成输入：${overlap.join("、")}。待预测任务不能这样创建。`)
  return issues
}

export function timeWindow(table: ParsedTable, column: string | null): string {
  if (!column) return "未识别时间范围"
  const index = table.columns.indexOf(column)
  if (index < 0) return "未识别时间范围"
  const stamps = table.rows.map((row) => Date.parse(row[index]?.replace(/\//g, "-") || "")).filter((value) => !Number.isNaN(value)).sort((a, b) => a - b)
  if (stamps.length === 0) return "未识别时间范围"
  const format = (value: number) => {
    const date = new Date(value)
    const pad = (part: number) => String(part).padStart(2, "0")
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
  }
  return stamps.length === 1 ? format(stamps[0]) : `${format(stamps[0])} – ${format(stamps[stamps.length - 1])}`
}
