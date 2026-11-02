import { useEffect, useState } from "react"
import {
  L1ApiError,
  applyStandardization,
  getRawCell,
  listStandardizationDiff,
  listStandardizationIssues,
  listStandardizationRows,
  previewStandardization,
  standardizationConfig,
  validateStandardization,
  type StandardizationDraft,
} from "./api"
import { DatasetReport } from "./DatasetReport"
import type { CellDetail, DatasetDetail, SheetInfo, StandardizationField, StandardizationIssue, StandardizationPlan, StandardizationRecord, StandardizationSummary, StandardizationValue } from "./types"

const MODES = [
  ["input", "输入"],
  ["output", "输出"],
  ["diff", "差异"],
  ["config", "配置"],
] as const

const STATUS_LABEL: Record<string, string> = {
  success: "转换成功",
  missing_empty: "原始空值",
  missing_blank: "空白",
  missing_marker: "缺失标识",
  parse_failed: "类型转换失败",
  numeric_ambiguous: "数字格式待确认",
  datetime_failed: "时间解析失败",
  datetime_ambiguous: "日期歧义",
  datetime_excel_unreliable: "Excel 日期不可靠",
  unit_incompatible: "单位不兼容",
  unit_unconfirmed: "单位待确认",
  mapping_unconfirmed: "映射待确认",
  not_executed: "未执行",
  not_applicable: "不适用",
}

type Options = {
  decimal_style: string
  thousands_separator: string
  missing_tokens: string
  datetime_formats: string
  source_timezone: string
  target_timezone: string
}

const EMPTY_OPTIONS: Options = {
  decimal_style: "point",
  thousands_separator: "",
  missing_tokens: "",
  datetime_formats: "",
  source_timezone: "",
  target_timezone: "",
}

export function StandardizationWorkspace({
  datasetId,
  detail,
  sheets,
  limit,
  debug,
  onSheet,
  onPage,
  onJumpChange,
  onApplied,
}: {
  datasetId: string
  detail: DatasetDetail | null
  sheets: SheetInfo[]
  limit: number
  debug: boolean
  onSheet: (id: string) => void
  onPage: (offset: number) => void
  onJumpChange: (value: string) => void
  onApplied: () => void
}) {
  const [plan, setPlan] = useState<StandardizationPlan | null>(null)
  const [preview, setPreview] = useState<StandardizationPlan | null>(null)
  const [options, setOptions] = useState<Options>(EMPTY_OPTIONS)
  const [enabled, setEnabled] = useState<Record<string, boolean>>({})
  const [fieldDecimal, setFieldDecimal] = useState<Record<string, string>>({})
  const [mode, setMode] = useState<(typeof MODES)[number][0]>("output")
  const [query, setQuery] = useState("")
  const [sheetFilter, setSheetFilter] = useState("")
  const [rows, setRows] = useState<StandardizationRecord[]>([])
  const [rowTotal, setRowTotal] = useState(0)
  const [rowOffset, setRowOffset] = useState(0)
  const [issues, setIssues] = useState<StandardizationIssue[]>([])
  const [issueTotal, setIssueTotal] = useState(0)
  const [issueOffset, setIssueOffset] = useState(0)
  const [selectedField, setSelectedField] = useState("")
  const [selectedValue, setSelectedValue] = useState<StandardizationValue | null>(null)
  const [record, setRecord] = useState<StandardizationRecord | null>(null)
  const [lineage, setLineage] = useState<CellDetail | null>(null)
  const [validation, setValidation] = useState("")
  const [error, setError] = useState("")
  const [notice, setNotice] = useState("")
  const [busy, setBusy] = useState("")

  useEffect(() => {
    const controller = new AbortController()
    setPreview(null)
    setEnabled({})
    setRecord(null)
    setSelectedValue(null)
    setLineage(null)
    standardizationConfig(datasetId, controller.signal)
      .then((body) => {
        if (controller.signal.aborted) return
        setPlan(body)
        const config = (body as { config?: Record<string, unknown> }).config
        if (config && typeof config === "object") setOptions(readOptions(config))
        setNotice(body.run ? `已加载 P4 ${body.run.run_id}，修订 ${body.run.revision}。` : "已读取 P3 映射，还没有执行标准化。")
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) setError(reason instanceof L1ApiError ? reason.message : "标准化配置没有读取完成。")
      })
    return () => controller.abort()
  }, [datasetId])

  const runId = preview ? "" : plan?.run?.run_id ?? ""

  useEffect(() => {
    if (preview?.rows) {
      setRows(preview.rows)
      setRowTotal(preview.rows.length)
      setIssues(preview.sample_issues ?? [])
      setIssueTotal(preview.sample_issues?.length ?? 0)
      return
    }
    if (!runId || mode === "config") return
    const controller = new AbortController()
    const request = mode === "diff" ? listStandardizationDiff(runId, rowOffset, limit, controller.signal) : listStandardizationRows(runId, rowOffset, limit, mode, controller.signal)
    request
      .then((body) => {
        if (controller.signal.aborted) return
        setRows(body.rows)
        setRowTotal(body.total_records)
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) setError(reason instanceof L1ApiError ? reason.message : "标准化记录没有读取完成。")
      })
    return () => controller.abort()
  }, [runId, preview, rowOffset, limit, mode])

  useEffect(() => {
    if (!runId) return
    const controller = new AbortController()
    listStandardizationIssues(runId, issueOffset, limit, controller.signal)
      .then((body) => {
        if (controller.signal.aborted) return
        setIssues(body.issues)
        setIssueTotal(body.total)
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) setError(reason instanceof L1ApiError ? reason.message : "转换问题没有读取完成。")
      })
    return () => controller.abort()
  }, [runId, issueOffset, limit])

  const fields = (plan?.fields ?? []).map((field) => ({ ...field, enabled: enabled[field.source_column_id] ?? field.enabled }))
  const visibleFields = fields.filter((field) => {
    if (sheetFilter && field.sheet_id !== sheetFilter) return false
    if (query && !`${field.header} ${field.standard_label} ${field.standard_field_id}`.toLowerCase().includes(query.trim().toLowerCase())) return false
    return true
  })
  const summary = preview?.summary ?? plan?.applied_summary ?? plan?.summary
  const selected = fields.find((field) => field.source_column_id === selectedField) ?? null

  function draft(): StandardizationDraft {
    return {
      decimal_style: options.decimal_style,
      thousands_separator: options.thousands_separator,
      missing_tokens: splitList(options.missing_tokens),
      datetime_formats: splitList(options.datetime_formats),
      source_timezone: options.source_timezone.trim(),
      target_timezone: options.target_timezone.trim(),
      failure_policy: "keep_record",
      sample_records: 8,
      fields: draftFields(),
    }
  }

  function draftFields(): { source_column_id: string; enabled?: boolean; decimal_style?: string }[] {
    const ids = new Set([...Object.keys(enabled), ...Object.keys(fieldDecimal)])
    return [...ids].map((source_column_id) => ({
      source_column_id,
      ...(source_column_id in enabled ? { enabled: enabled[source_column_id] } : {}),
      ...(fieldDecimal[source_column_id] ? { decimal_style: fieldDecimal[source_column_id] } : {}),
    }))
  }

  function openValue(row: StandardizationRecord, field: StandardizationValue) {
    setRecord(row)
    setSelectedValue(field)
    setSelectedField(field.source_column_id)
    onSheet(row.sheet_id)
    onJumpChange(String(row.row_number))
    onPage(Math.floor((row.row_number - 1) / limit) * limit)
    if (!debug || !field.cell_id) {
      setLineage(null)
      return
    }
    getRawCell(datasetId, field.cell_id)
      .then(setLineage)
      .catch(() => setLineage(null))
  }

  async function onPreview() {
    setBusy("预览")
    setError("")
    setValidation("")
    try {
      const body = await previewStandardization(datasetId, draft())
      setPreview(body)
      setRowOffset(0)
      setMode("diff")
      setNotice(body.summary?.note || "这是抽样预览，尚未写入正式结果。")
    } catch (reason: unknown) {
      setError(reason instanceof L1ApiError ? reason.message : "预览没有完成。")
    } finally {
      setBusy("")
    }
  }

  async function onValidate() {
    setBusy("验证")
    setError("")
    try {
      const body = await validateStandardization(datasetId, draft())
      const allowed = body.apply_allowed === true
      const found = Array.isArray(body.issues) ? body.issues.length : 0
      setValidation(allowed ? `配置可以执行。仍有 ${found} 条提示，部分标准化会保留未确认字段。` : "配置有阻断项，不能执行。")
    } catch (reason: unknown) {
      setError(reason instanceof L1ApiError ? reason.message : "验证没有完成。")
    } finally {
      setBusy("")
    }
  }

  async function onApply() {
    setBusy("执行")
    setError("")
    try {
      const body = await applyStandardization(datasetId, draft())
      setPreview(null)
      setEnabled({})
      setPlan(body)
      setRowOffset(0)
      setIssueOffset(0)
      setNotice(body.run ? `P4 已执行，${body.run.run_id}，修订 ${body.run.revision}。原始单元格没有改写。` : "P4 已执行。")
      onApplied()
    } catch (reason: unknown) {
      setError(reason instanceof L1ApiError ? reason.message : "执行没有完成。")
    } finally {
      setBusy("")
    }
  }

  return (
    <>
      <div className="l1-center">
        <div className="l1-viewer">
          {notice ? <p className="l1-note">{notice}</p> : null}
          {error ? <p className="l1-note is-error">{error}</p> : null}
          {validation ? <p className="l1-note">{validation}</p> : null}
          {summary ? <SummaryLine summary={summary} sampled={Boolean(preview)} /> : null}
          <div className="l1-bar">
          <div className="l1-tools">
            {MODES.map(([id, label]) => (
              <button key={id} type="button" aria-pressed={mode === id} onClick={() => { setPreview(null); setMode(id); setRowOffset(0) }}>
                {label}
              </button>
            ))}
            <button type="button" disabled={busy !== ""} onClick={() => void onPreview()}>预览试运行</button>
            <button type="button" disabled={busy !== ""} onClick={() => { setPreview(null); setMode("diff"); setRowOffset(0) }}>检查差异</button>
            <button type="button" disabled={busy !== ""} onClick={() => void onValidate()}>验证配置</button>
            <button type="button" disabled={busy !== ""} onClick={() => void onApply()}>应用执行</button>
          </div>
          <p className="l1-meta">
            <span>P3 {plan?.p3_run_id || "未执行"}{plan?.p3_revision ? ` · 修订 ${plan.p3_revision}` : ""}</span>
            <span>{plan?.run ? `P4 修订 ${plan.run.revision}` : "P4 未执行"}</span>
            <span>{plan?.run ? statusText(plan.run.status) : "未执行"}</span>
            <DatasetReport detail={detail} />
          </p>
          </div>
          <div className="l1-tools">
            <input aria-label="搜索标准字段" value={query} placeholder="搜索字段" onChange={(event) => setQuery(event.target.value)} />
            <select aria-label="按工作表过滤字段" value={sheetFilter} onChange={(event) => setSheetFilter(event.target.value)}>
              <option value="">全部工作表</option>
              {sheets.map((sheet) => (
                <option key={sheet.sheet_id} value={sheet.sheet_id}>{sheet.name}</option>
              ))}
            </select>
          </div>
          {mode === "config" ? (
            <ConfigEditor
              options={options}
              fields={visibleFields}
              onOptions={(patch) => {
                setPreview(null)
                setOptions((current) => ({ ...current, ...patch }))
              }}
              onEnabled={(field, value) => {
                setPreview(null)
                setEnabled((current) => ({ ...current, [field.source_column_id]: value }))
                setSelectedField(field.source_column_id)
              }}
              onSelect={setSelectedField}
            />
          ) : mode === "diff" ? (
            <DiffTables rows={rows} onOpen={openValue} />
          ) : (
            <RecordTables rows={rows} output={mode === "output"} onOpen={openValue} />
          )}
          {mode !== "config" ? (
            <p className="l1-pager">
              <button type="button" disabled={rowOffset <= 0} onClick={() => setRowOffset(Math.max(0, rowOffset - limit))}>上一页</button>
              <span>
                {rowTotal === 0 ? "0" : `${rowOffset + 1}–${Math.min(rowOffset + rows.length, rowTotal)}`} / {rowTotal}
                {preview ? " · 抽样预览" : ""}
              </span>
              <button type="button" disabled={rowOffset + limit >= rowTotal} onClick={() => setRowOffset(rowOffset + limit)}>下一页</button>
            </p>
          ) : null}
          <IssueList
            issues={issues}
            total={issueTotal}
            offset={issueOffset}
            limit={limit}
            sampled={Boolean(preview)}
            onPage={setIssueOffset}
            onOpen={(issue) => {
              if (!issue.sheet_id || !issue.row_number) return
              onSheet(issue.sheet_id)
              onJumpChange(String(issue.row_number))
              onPage(Math.floor((issue.row_number - 1) / limit) * limit)
              setSelectedField(issue.source_column_id)
            }}
          />
        </div>
      </div>
      <aside className="l1-inspector" aria-label="数据治理调试">
        <header>
          <h3>{debug ? "数据治理调试" : "标准化"}</h3>
        </header>
        <p className="l1-note">目标类型、角色和单位来自 P3。要改语义请回到 P3 生成新修订，这里不会改写已确认映射。</p>
        {selected ? (
          <div className="l1-region-form">
            <strong>{selected.header}</strong>
            <p className="l1-note">{selected.sheet_name} · {selected.standard_label || "未映射"} · {selected.column_id}</p>
            <dl>
              <dt>确认状态</dt>
              <dd>{selected.confirmation_status === "confirmed" ? "已确认" : "待确认"}</dd>
              <dt>目标类型</dt>
              <dd>{selected.target_type || "未声明"}</dd>
              <dt>单位</dt>
              <dd>{selected.source_unit || "未确认"} → {selected.target_unit || "无"}</dd>
              <dt>资格</dt>
              <dd>{selected.eligibility === "excluded" ? "排除" : selected.eligibility}</dd>
              <dt>转换</dt>
              <dd>{selected.confirmation_status !== "confirmed" ? "未确认，不转换" : selected.enabled ? "启用" : "停用"}</dd>
            </dl>
            {selected.confirmation_status === "confirmed" && (selected.target_type === "float64" || selected.target_type === "integer") ? (
              <label>
                本字段小数点
                <select
                  aria-label={`${selected.header} 小数点`}
                  value={fieldDecimal[selected.source_column_id] ?? ""}
                  onChange={(event) => {
                    setPreview(null)
                    setFieldDecimal((current) => ({ ...current, [selected.source_column_id]: event.target.value }))
                  }}
                >
                  <option value="">沿用全局</option>
                  <option value="point">点</option>
                  <option value="comma">逗号</option>
                </select>
              </label>
            ) : null}
          </div>
        ) : (
          <p className="l1-note">选择一个字段或单元格后，可以查看转换规则和原始位置。</p>
        )}
        {selectedValue ? (
          <dl>
            <dt>原始值</dt>
            <dd>{selectedValue.raw_value === "" ? "空" : selectedValue.raw_value}</dd>
            <dt>标准化值</dt>
            <dd>{displayValue(selectedValue)}</dd>
            <dt>原始类型</dt>
            <dd>{selectedValue.cell_type || "空"}</dd>
            <dt>规则</dt>
            <dd>{selectedValue.rule_id || "无"}{selectedValue.unit_rule ? ` · ${selectedValue.unit_rule}` : ""}</dd>
            <dt>状态</dt>
            <dd>{STATUS_LABEL[selectedValue.status] ?? selectedValue.status}{selectedValue.timezone_status === "unconfirmed" ? "，时区待确认" : ""}</dd>
            <dt>说明</dt>
            <dd>{selectedValue.message || "无"}</dd>
          </dl>
        ) : null}
        {debug ? (
          <dl>
            <dt>P4 版本</dt>
            <dd>{plan?.run?.run_id || "未执行"}</dd>
            <dt>规则版本</dt>
            <dd>{plan?.rule_id || "standardization_v1"}</dd>
            <dt>P3 版本</dt>
            <dd>{record?.p3_run_id || plan?.p3_run_id || "未执行"}</dd>
            <dt>P2 / P1</dt>
            <dd>{lineage ? `${lineage.sheet_name} ${lineage.coordinate} · 原始行 ${lineage.source_row_id}` : "点击单元格后追溯到原始档案"}</dd>
            <dt>P0 原始值</dt>
            <dd>{lineage ? lineage.raw_value || "空单元格" : "尚未定位"}</dd>
            <dt>Excel 日期系统</dt>
            <dd>{plan?.excel_date_system || "未读取"}</dd>
          </dl>
        ) : null}
      </aside>
    </>
  )
}

function SummaryLine({ summary, sampled }: { summary: StandardizationSummary; sampled: boolean }) {
  const scanned =
    summary.scanned_cells ??
    summary.success_cells + summary.missing_cells + summary.type_failure_cells + summary.time_failure_cells + summary.unit_failure_cells
  return (
    <p className="l1-legend">
      <span>{sampled ? "抽样记录" : "业务记录"} {summary.record_count}</span>
      <span>输出 {summary.output_record_count}</span>
      <span>扫描字段 {summary.converted_field_count}</span>
      <span>已扫描格 {scanned}</span>
      <span>成功 {summary.success_cells}</span>
      <span>缺失 {summary.missing_cells}</span>
      <span>类型失败 {summary.type_failure_cells}</span>
      <span>时间失败 {summary.time_failure_cells}</span>
      <span>单位失败 {summary.unit_failure_cells}</span>
      <span>未确认字段 {summary.unconfirmed_field_count}（潜在格 {summary.unconfirmed_cells ?? "—"}，不计入扫描守恒）</span>
      <span>{summary.publish_ready ? "可发布" : summary.partial ? "部分完成" : "未就绪"}</span>
    </p>
  )
}

function RecordTables({ rows, output, onOpen }: { rows: StandardizationRecord[]; output: boolean; onOpen: (row: StandardizationRecord, field: StandardizationValue) => void }) {
  if (rows.length === 0) return <p className="l1-empty">这个视图还没有记录。</p>
  const sections = groupRows(rows)
  return (
    <div className="l1-table-scroll">
      {sections.map((section) => (
        <table key={section.key} className="l1-table" aria-label={output ? "标准化输出" : "标准化输入"}>
          <caption>{section.title}</caption>
          <thead>
            <tr>
              <th>原表</th>
              {section.rows[0]?.fields.map((field) => (
                <th key={field.source_column_id}>{output ? field.standard_label || field.header : field.header}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {section.rows.map((row) => (
              <tr key={row.source_row_id}>
                <td>{row.row_number}</td>
                {row.fields.map((field) => (
                  <td key={field.source_column_id}>
                    <button type="button" onClick={() => onOpen(row, field)}>
                      {output ? displayValue(field) : field.raw_value === "" ? "空" : field.raw_value}
                    </button>
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      ))}
    </div>
  )
}

function DiffTables({ rows, onOpen }: { rows: StandardizationRecord[]; onOpen: (row: StandardizationRecord, field: StandardizationValue) => void }) {
  if (rows.length === 0) return <p className="l1-empty">这个范围内还没有差异记录。</p>
  return (
    <div className="l1-table-scroll">
      {rows.map((row) => (
        <table key={row.source_row_id} className="l1-table" aria-label={`${row.sheet_name} 第 ${row.row_number} 行差异`}>
          <caption>{row.sheet_name} · 第 {row.row_number} 行</caption>
          <thead>
            <tr>
              <th>字段</th>
              <th>原始值</th>
              <th>标准化值</th>
              <th>原始类型</th>
              <th>目标类型</th>
              <th>单位</th>
              <th>规则</th>
              <th>状态</th>
            </tr>
          </thead>
          <tbody>
            {row.fields.map((field) => (
              <tr key={field.source_column_id}>
                <td>
                  <button type="button" onClick={() => onOpen(row, field)}>{field.standard_label || field.header}</button>
                </td>
                <td>{field.raw_value === "" ? "空" : field.raw_value}</td>
                <td>{displayValue(field)}</td>
                <td>{field.cell_type || "空"}</td>
                <td>{field.target_type || "—"}</td>
                <td>{field.source_unit || "未确认"} → {field.target_unit || "无"}</td>
                <td>{field.rule_id || "—"}</td>
                <td>{STATUS_LABEL[field.status] ?? field.status}{field.message ? ` · ${field.message}` : ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ))}
    </div>
  )
}

function ConfigEditor({
  options,
  fields,
  onOptions,
  onEnabled,
  onSelect,
}: {
  options: Options
  fields: StandardizationField[]
  onOptions: (patch: Partial<Options>) => void
  onEnabled: (field: StandardizationField, value: boolean) => void
  onSelect: (id: string) => void
}) {
  return (
    <>
      <div className="l1-tools">
        <label>
          小数点
          <select aria-label="小数点样式" value={options.decimal_style} onChange={(event) => onOptions({ decimal_style: event.target.value })}>
            <option value="point">点</option>
            <option value="comma">逗号</option>
          </select>
        </label>
        <label>
          千位分隔
          <select aria-label="千位分隔符" value={options.thousands_separator} onChange={(event) => onOptions({ thousands_separator: event.target.value })}>
            <option value="">不使用</option>
            <option value=",">逗号</option>
            <option value=".">点</option>
            <option value=" ">空格</option>
          </select>
        </label>
        <label>
          来源时区
          <input aria-label="来源时区" value={options.source_timezone} placeholder="留空表示未确认" onChange={(event) => onOptions({ source_timezone: event.target.value })} />
        </label>
        <label>
          目标时区
          <input aria-label="目标时区" value={options.target_timezone} placeholder="Z 或 +08:00" onChange={(event) => onOptions({ target_timezone: event.target.value })} />
        </label>
      </div>
      <div className="l1-tools">
        <label>
          缺失标识
          <input aria-label="缺失标识" value={options.missing_tokens} placeholder="用逗号分隔，默认不把 NA 当缺失" onChange={(event) => onOptions({ missing_tokens: event.target.value })} />
        </label>
        <label>
          日期格式
          <input aria-label="日期格式" value={options.datetime_formats} placeholder="%d/%m/%Y，留空只用无歧义格式" onChange={(event) => onOptions({ datetime_formats: event.target.value })} />
        </label>
      </div>
      <div className="l1-table-scroll">
        <table className="l1-table" aria-label="转换规则">
          <thead>
            <tr>
              <th>字段</th>
              <th>来源</th>
              <th>目标类型</th>
              <th>单位</th>
              <th>状态</th>
              <th>启用</th>
            </tr>
          </thead>
          <tbody>
            {fields.map((field) => (
              <tr key={field.source_column_id}>
                <td>
                  <button type="button" onClick={() => onSelect(field.source_column_id)}>{field.header}</button>
                </td>
                <td>{field.sheet_name}</td>
                <td>{field.target_type || "—"}</td>
                <td>{field.source_unit || "未确认"} → {field.target_unit || "无"}</td>
                <td>{field.confirmation_status === "confirmed" ? "已确认" : "待确认"}</td>
                <td>
                  <input
                    aria-label={`${field.header} 启用转换`}
                    type="checkbox"
                    checked={field.enabled}
                    disabled={field.confirmation_status !== "confirmed"}
                    onChange={(event) => onEnabled(field, event.target.checked)}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}

function IssueList({
  issues,
  total,
  offset,
  limit,
  sampled,
  onPage,
  onOpen,
}: {
  issues: StandardizationIssue[]
  total: number
  offset: number
  limit: number
  sampled: boolean
  onPage: (offset: number) => void
  onOpen: (issue: StandardizationIssue) => void
}) {
  if (issues.length === 0) return <p className="l1-note">当前范围没有转换错误。映射待确认和缺失不会并进这个错误列表。</p>
  return (
    <>
      <div className="l1-table-scroll">
        <table className="l1-table" aria-label="转换错误">
          <caption>{sampled ? "抽样中的转换错误" : `转换错误 ${total}`}</caption>
          <thead>
            <tr>
              <th>位置</th>
              <th>原始值</th>
              <th>类型</th>
              <th>原因</th>
            </tr>
          </thead>
          <tbody>
            {issues.map((issue) => (
              <tr key={`${issue.issue_id ?? issue.source_column_id}-${issue.row_number ?? 0}-${issue.code}`}>
                <td>
                  <button type="button" onClick={() => onOpen(issue)}>
                    {issue.sheet_name || "字段"} {issue.coordinate || issue.column_id || ""} {issue.row_number ? `第 ${issue.row_number} 行` : ""}
                  </button>
                </td>
                <td>{issue.raw_value || "—"}</td>
                <td>{STATUS_LABEL[issue.code] ?? issue.code}</td>
                <td>{issue.message}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {sampled ? null : (
        <p className="l1-pager">
          <button type="button" disabled={offset <= 0} onClick={() => onPage(Math.max(0, offset - limit))}>上一批错误</button>
          <span>{total === 0 ? "0" : `${offset + 1}–${Math.min(offset + issues.length, total)}`} / {total}</span>
          <button type="button" disabled={offset + limit >= total} onClick={() => onPage(offset + limit)}>下一批错误</button>
        </p>
      )}
    </>
  )
}

function groupRows(rows: StandardizationRecord[]) {
  const sections: { key: string; title: string; rows: StandardizationRecord[] }[] = []
  for (const row of rows) {
    const last = sections[sections.length - 1]
    if (!last || last.key !== row.region_id) sections.push({ key: row.region_id, title: row.sheet_name, rows: [row] })
    else last.rows.push(row)
  }
  return sections
}

function displayValue(field: StandardizationValue): string {
  if (field.status === "success") {
    const zone = field.timezone_status === "unconfirmed" ? " · 时区待确认" : ""
    return `${field.normalized_text}${zone}`
  }
  if (field.status === "mapping_unconfirmed" || field.status === "unit_unconfirmed") return "待定"
  return STATUS_LABEL[field.status] ?? field.status
}

function statusText(status: string): string {
  if (status === "partial") return "部分完成"
  if (status === "completed") return "已完成"
  if (status === "failed") return "失败"
  return "未执行"
}

function readOptions(config: Record<string, unknown>): Options {
  return {
    decimal_style: typeof config.decimal_style === "string" ? config.decimal_style : "point",
    thousands_separator: typeof config.thousands_separator === "string" ? config.thousands_separator : "",
    missing_tokens: Array.isArray(config.missing_tokens) ? config.missing_tokens.filter((item) => typeof item === "string").join(", ") : "",
    datetime_formats: Array.isArray(config.datetime_formats) ? config.datetime_formats.filter((item) => typeof item === "string").join(", ") : "",
    source_timezone: typeof config.source_timezone === "string" ? config.source_timezone : "",
    target_timezone: typeof config.target_timezone === "string" ? config.target_timezone : "",
  }
}

function splitList(value: string): string[] {
  return value.split(/[,，]/).map((item) => item.trim()).filter(Boolean)
}
