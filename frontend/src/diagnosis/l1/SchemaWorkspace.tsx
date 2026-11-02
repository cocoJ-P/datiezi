import { useEffect, useState } from "react"
import { L1ApiError, applySchema, listSchemaRows, previewSchema, schemaMappings, schemaSuggestions, type SchemaDraft } from "./api"
import { DatasetReport } from "./DatasetReport"
import type { DatasetDetail, SchemaIssue, SchemaMapping, SchemaPlan, SchemaRecord, SheetInfo } from "./types"

const MODES = [
  ["input", "输入"],
  ["output", "输出"],
  ["diff", "差异"],
  ["config", "配置"],
] as const

const ROLES = [
  ["identifier", "标识"],
  ["timestamp", "时间"],
  ["numerical_feature", "数值特征"],
  ["categorical_feature", "类别特征"],
  ["context", "上下文"],
  ["target_label", "监督标签"],
  ["diagnostic_output", "诊断输出"],
  ["dataset_metadata", "数据集元数据"],
  ["unmapped", "未映射"],
]

const ELIGIBILITY = [
  ["candidate_input", "候选输入"],
  ["excluded", "排除"],
  ["requires_review", "需要复核"],
]

const METHODS: Record<string, string> = {
  exact_match: "精确",
  alias_match: "别名",
  normalized_match: "规范化",
  similarity: "相似",
  ambiguous: "歧义",
  manual: "人工",
  unmapped: "未映射",
}

type Draft = SchemaDraft["mappings"] extends (infer Item)[] | undefined ? Item : never

export function SchemaWorkspace({
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
  const [plan, setPlan] = useState<SchemaPlan | null>(null)
  const [preview, setPreview] = useState<SchemaPlan | null>(null)
  const [drafts, setDrafts] = useState<Record<string, Draft>>({})
  const [mode, setMode] = useState<(typeof MODES)[number][0]>("output")
  const [query, setQuery] = useState("")
  const [sheetFilter, setSheetFilter] = useState("")
  const [roleFilter, setRoleFilter] = useState("")
  const [unmappedOnly, setUnmappedOnly] = useState(false)
  const [conflictOnly, setConflictOnly] = useState(false)
  const [selectedId, setSelectedId] = useState("")
  const [record, setRecord] = useState<SchemaRecord | null>(null)
  const [rows, setRows] = useState<SchemaRecord[]>([])
  const [rowTotal, setRowTotal] = useState(0)
  const [rowOffset, setRowOffset] = useState(0)
  const [error, setError] = useState("")
  const [notice, setNotice] = useState("")
  const [busy, setBusy] = useState("")

  useEffect(() => {
    const controller = new AbortController()
    setPreview(null)
    setDrafts({})
    setRecord(null)
    Promise.all([schemaSuggestions(datasetId, controller.signal), schemaMappings(datasetId, controller.signal)])
      .then(([suggestions, current]) => {
        if (controller.signal.aborted) return
        setPlan(current.applied ? current : suggestions)
        setNotice(current.applied ? `已加载 P3 ${current.run?.run_id}，修订 ${current.run?.revision}。` : "当前是映射建议，还没有执行。")
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) setError(reason instanceof L1ApiError ? reason.message : "字段映射没有读取完成。")
      })
    return () => controller.abort()
  }, [datasetId])

  const active = preview ?? plan
  const runId = preview ? "" : plan?.run?.run_id ?? ""

  useEffect(() => {
    if (preview?.sample_rows) {
      setRows(preview.sample_rows)
      setRowTotal(preview.sample_rows.length)
      return
    }
    if (!runId || mode === "diff" || mode === "config") return
    const controller = new AbortController()
    listSchemaRows(runId, rowOffset, limit, mode === "input" ? "input" : "output", controller.signal)
      .then((body) => {
        if (controller.signal.aborted) return
        setRows(body.rows)
        setRowTotal(body.total_records)
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) setError(reason instanceof L1ApiError ? reason.message : "映射记录没有读取完成。")
      })
    return () => controller.abort()
  }, [runId, preview, rowOffset, limit, mode])

  function merged(mapping: SchemaMapping): SchemaMapping {
    const draft = drafts[mapping.source_column_id]
    if (!draft) return mapping
    const fieldChanged = draft.standard_field_id !== undefined && draft.standard_field_id !== mapping.standard_field_id
    const fieldId = draft.standard_field_id !== undefined ? draft.standard_field_id : mapping.standard_field_id
    const field = active?.template?.fields.find((item) => item.field_id === fieldId)
    return {
      ...mapping,
      standard_field_id: fieldId,
      standard_label: field?.label ?? (fieldId ? mapping.standard_label : ""),
      role: draft.role || (fieldChanged ? field?.role || "unmapped" : mapping.role),
      eligibility: draft.eligibility || (fieldId === "" ? "excluded" : fieldChanged ? field?.eligibility || mapping.eligibility : mapping.eligibility),
      source_unit: draft.source_unit ?? mapping.source_unit,
      target_unit: draft.target_unit || (fieldChanged ? field?.target_unit || "" : mapping.target_unit),
      target_type: draft.target_type || (fieldChanged ? field?.target_type || "" : mapping.target_type),
      match_method: fieldChanged ? "manual" : mapping.match_method,
      confirmation_status: draft.confirmation_status || mapping.confirmation_status,
    }
  }

  const mappings = (active?.mappings ?? []).map(merged)
  const conflicts = new Set((active?.issues ?? []).filter((item) => item.severity === "blocking").map((item) => item.source_column_id))
  const visible = mappings.filter((item) => {
    if (sheetFilter && item.sheet_id !== sheetFilter) return false
    if (roleFilter && item.role !== roleFilter) return false
    if (unmappedOnly && item.standard_field_id) return false
    if (conflictOnly && !conflicts.has(item.source_column_id)) return false
    if (query && !`${item.header} ${item.standard_field_id} ${item.standard_label}`.toLowerCase().includes(query.trim().toLowerCase())) return false
    return true
  })
  const selected = mappings.find((item) => item.source_column_id === selectedId) ?? null

  function updateDraft(mapping: SchemaMapping, patch: Partial<Draft>) {
    setPreview(null)
    setDrafts((current) => ({ ...current, [mapping.source_column_id]: { ...current[mapping.source_column_id], source_column_id: mapping.source_column_id, ...patch } }))
  }

  function draftConfig(): SchemaDraft {
    return { batch_confirm: true, mappings: Object.values(drafts) }
  }

  async function onPreview() {
    setBusy("预览")
    setError("")
    try {
      const body = await previewSchema(datasetId, draftConfig())
      setPreview(body)
      setNotice("这是映射预览，尚未写入正式结果。单元格仍是原始文本。")
    } catch (reason: unknown) {
      setError(reason instanceof L1ApiError ? reason.message : "预览没有完成。")
    } finally {
      setBusy("")
    }
  }

  async function onApply() {
    setBusy("执行")
    setError("")
    try {
      const body = await applySchema(datasetId, draftConfig())
      setPreview(null)
      setDrafts({})
      setPlan(body)
      setNotice(body.run ? `P3 已执行，${body.run.run_id}，修订 ${body.run.revision}。原始值没有转换。` : "P3 已执行。")
      onApplied()
    } catch (reason: unknown) {
      setError(reason instanceof L1ApiError ? reason.message : "执行没有完成。")
    } finally {
      setBusy("")
    }
  }

  const summary = active?.summary

  return (
    <>
      <div className="l1-center">
        <div className="l1-viewer">
          {notice ? <p className="l1-note">{notice}</p> : null}
          {error ? <p className="l1-note is-error">{error}</p> : null}
          {summary ? (
            <p className="l1-legend">
              <span>字段 {summary.column_count}</span>
              <span>已确认 {summary.confirmed_count}</span>
              <span>待确认 {summary.pending_count}</span>
              <span>未映射 {summary.unmapped_count}</span>
              <span>冲突 {summary.conflict_count}</span>
            </p>
          ) : null}
          <div className="l1-bar">
          <div className="l1-tools">
            {MODES.map(([id, label]) => (
              <button key={id} type="button" aria-pressed={mode === id} onClick={() => { setMode(id); setRowOffset(0) }}>
                {label}
              </button>
            ))}
            <button type="button" disabled={busy !== ""} onClick={() => void onPreview()}>
              预览映射
            </button>
            <button type="button" disabled={busy !== ""} onClick={() => void onApply()}>
              应用并执行 P3
            </button>
          </div>
          <p className="l1-meta">
            <span>{active?.template?.name ?? "钢铁设备诊断字段"} {active?.template_version ?? ""}</span>
            <span>{plan?.run ? `修订 ${plan.run.revision}` : "P3 未执行"}</span>
            <DatasetReport detail={detail} />
          </p>
          </div>
          <div className="l1-tools">
            <input aria-label="搜索原始字段" value={query} placeholder="搜索字段" onChange={(event) => setQuery(event.target.value)} />
            <select aria-label="按工作表过滤" value={sheetFilter} onChange={(event) => setSheetFilter(event.target.value)}>
              <option value="">全部工作表</option>
              {sheets.map((sheet) => (
                <option key={sheet.sheet_id} value={sheet.sheet_id}>
                  {sheet.name}
                </option>
              ))}
            </select>
            <select aria-label="按角色过滤" value={roleFilter} onChange={(event) => setRoleFilter(event.target.value)}>
              <option value="">全部角色</option>
              {ROLES.map(([id, label]) => (
                <option key={id} value={id}>
                  {label}
                </option>
              ))}
            </select>
            <button type="button" aria-pressed={unmappedOnly} onClick={() => setUnmappedOnly((value) => !value)}>
              只看未映射
            </button>
            <button type="button" aria-pressed={conflictOnly} onClick={() => setConflictOnly((value) => !value)}>
              只看冲突
            </button>
          </div>
          <MappingTable mappings={visible} selectedId={selectedId} onSelect={setSelectedId} />
          {mode === "diff" ? <DiffList plan={active} /> : null}
          {mode === "config" ? <ConfigList plan={active} /> : null}
          {mode === "input" || mode === "output" ? (
            <RecordTables
              rows={rows}
              output={mode === "output"}
              onOpen={(item) => {
                setRecord(item)
                onSheet(item.sheet_id)
                onJumpChange(String(item.row_number))
                onPage(Math.floor((item.row_number - 1) / limit) * limit)
              }}
            />
          ) : null}
          {mode === "input" || mode === "output" ? (
            <p className="l1-pager">
              <button type="button" disabled={rowOffset <= 0} onClick={() => setRowOffset(Math.max(0, rowOffset - limit))}>
                上一页
              </button>
              <span>
                {rowTotal === 0 ? "0" : `${rowOffset + 1}–${Math.min(rowOffset + rows.length, rowTotal)}`} / {rowTotal}
                {preview ? " · 抽样预览" : ""}
              </span>
              <button type="button" disabled={rowOffset + limit >= rowTotal} onClick={() => setRowOffset(rowOffset + limit)}>
                下一页
              </button>
            </p>
          ) : null}
          <IssueList issues={active?.issues ?? []} />
        </div>
      </div>
      <aside className="l1-inspector" aria-label="字段语义">
        <header>
          <h3>字段语义</h3>
        </header>
        <p className="l1-note">{active?.template?.summary}</p>
        {selected ? (
          <div className="l1-region-form">
            <strong>{selected.header}</strong>
            <p className="l1-note">
              {selected.sheet_name} · {selected.column_id} · {selected.source_column_id}
            </p>
            <label>
              标准字段
              <select aria-label={`${selected.header} 标准字段`} value={selected.standard_field_id} onChange={(event) => updateDraft(selected, { standard_field_id: event.target.value, confirmation_status: "confirmed" })}>
                <option value="">未映射</option>
                {(active?.template?.fields ?? []).map((field) => (
                  <option key={field.field_id} value={field.field_id}>
                    {field.label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              字段角色
              <select aria-label="字段角色" value={selected.role} onChange={(event) => updateDraft(selected, { role: event.target.value, confirmation_status: "confirmed" })}>
                {ROLES.map(([id, label]) => (
                  <option key={id} value={id}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              模型输入资格
              <select aria-label="模型输入资格" value={selected.eligibility} onChange={(event) => updateDraft(selected, { eligibility: event.target.value, confirmation_status: "confirmed" })}>
                {ELIGIBILITY.map(([id, label]) => (
                  <option key={id} value={id}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              原始单位
              <input aria-label="原始单位" value={selected.source_unit} onChange={(event) => updateDraft(selected, { source_unit: event.target.value, confirmation_status: "confirmed" })} />
            </label>
            <dl>
              <dt>中文解释</dt>
              <dd>{selected.description || "未映射"}</dd>
              <dt>物理含义</dt>
              <dd>{selected.physical_meaning || "未确认"}</dd>
              <dt>目标单位</dt>
              <dd>{selected.target_unit || "无"}</dd>
              <dt>目标类型</dt>
              <dd>{selected.target_type || "未声明"}</dd>
              <dt>匹配方式</dt>
              <dd>{METHODS[selected.match_method] ?? selected.match_method}</dd>
              <dt>识别依据</dt>
              <dd>{selected.match_reason}</dd>
              <dt>确认状态</dt>
              <dd>{selected.confirmation_status === "confirmed" ? "已确认" : selected.confirmation_status === "needs_confirmation" ? "待确认" : "建议"}</dd>
              <dt>抽样</dt>
              <dd>{selected.samples.length ? selected.samples.join("、") : "没有非空样例"} · 抽样预览</dd>
            </dl>
            {debug ? (
              <dl>
                <dt>P1 区域</dt>
                <dd>{selected.region_id}</dd>
                <dt>P2 版本</dt>
                <dd>{active?.p2_run_id || plan?.run?.p2_run_id || "未执行"}</dd>
                <dt>P3 版本</dt>
                <dd>{plan?.run?.run_id || "未执行"}</dd>
                {record ? (
                  <>
                    <dt>原始位置</dt>
                    <dd>
                      {record.sheet_name} 第 {record.row_number} 行 · {record.source_row_id}
                    </dd>
                    <dt>设备分组</dt>
                    <dd>{record.equipment_group_id ? `${record.equipment_value} · ${record.equipment_group_id}` : "没有 P2 归属"}</dd>
                  </>
                ) : null}
              </dl>
            ) : null}
          </div>
        ) : (
          <p className="l1-note">选择一个原始字段后，可以查看样例、角色和单位。样例来自原始单元格，只是抽样。</p>
        )}
      </aside>
    </>
  )
}

function MappingTable({ mappings, selectedId, onSelect }: { mappings: SchemaMapping[]; selectedId: string; onSelect: (id: string) => void }) {
  if (mappings.length === 0) return <p className="l1-empty">这个筛选下没有字段。</p>
  return (
    <div className="l1-table-scroll">
      <table className="l1-table" aria-label="字段映射">
        <thead>
          <tr>
            <th>原始字段</th>
            <th>来源</th>
            <th>标准字段</th>
            <th>角色</th>
            <th>单位</th>
            <th>目标类型</th>
            <th>匹配</th>
            <th>状态</th>
          </tr>
        </thead>
        <tbody>
          {mappings.map((item) => (
            <tr key={item.source_column_id}>
              <td>
                <button type="button" aria-pressed={item.source_column_id === selectedId} onClick={() => onSelect(item.source_column_id)}>
                  {item.header}
                </button>
              </td>
              <td>{item.sheet_name}</td>
              <td>{item.standard_label || "未映射"}</td>
              <td>{ROLES.find(([id]) => id === item.role)?.[1] ?? item.role}</td>
              <td>{item.source_unit || "—"} → {item.target_unit || "—"}</td>
              <td>{item.target_type || "—"}</td>
              <td>{METHODS[item.match_method] ?? item.match_method}</td>
              <td>{item.confirmation_status === "confirmed" ? "已确认" : item.confirmation_status === "needs_confirmation" ? "待确认" : "建议"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function RecordTables({ rows, output, onOpen }: { rows: SchemaRecord[]; output: boolean; onOpen: (row: SchemaRecord) => void }) {
  if (rows.length === 0) return <p className="l1-empty">这个视图还没有记录。</p>
  const sections: { key: string; title: string; rows: SchemaRecord[] }[] = []
  for (const row of rows) {
    const last = sections[sections.length - 1]
    if (!last || last.key !== row.region_id) sections.push({ key: row.region_id, title: `${row.sheet_name} · ${row.region_id}`, rows: [row] })
    else last.rows.push(row)
  }
  return (
    <div className="l1-table-scroll">
      {sections.map((section) => {
        const fields = section.rows[0]?.fields ?? []
        return (
          <table key={section.key} className="l1-table" aria-label={output ? "标准字段视图" : "原始字段视图"}>
            <caption>{section.title}{output ? " · 原始文本，未做类型转换" : ""}</caption>
            <thead>
              <tr>
                <th>原表</th>
                {fields.map((field) => (
                  <th key={field.source_column_id}>{output ? field.standard_label || `未映射 ${field.header}` : field.header}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {section.rows.map((row) => (
                <tr key={row.source_row_id}>
                  <td>
                    <button type="button" onClick={() => onOpen(row)}>
                      {row.row_number}
                    </button>
                  </td>
                  {row.fields.map((field) => (
                    <td key={field.source_column_id}>{field.raw_value}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        )
      })}
    </div>
  )
}

function DiffList({ plan }: { plan: SchemaPlan | null }) {
  const changes = plan?.diff?.changes ?? []
  if (!plan?.diff) return <p className="l1-note">预览后会显示相对上一版正式映射的字段差异。</p>
  return (
    <div className="l1-diff">
      <strong>{plan.diff.baseline_revision ? `对照修订 ${plan.diff.baseline_revision}` : "还没有已执行版本"}</strong>
      <span>变化 {plan.diff.changed_count}</span>
      <p>{plan.diff.note}</p>
      {changes.length === 0 ? <p>没有字段声明变化。</p> : changes.map((item) => <p key={item.source_column_id}>{item.header} · {item.change}</p>)}
    </div>
  )
}

function ConfigList({ plan }: { plan: SchemaPlan | null }) {
  const fields = plan?.template?.fields ?? []
  if (fields.length === 0) return null
  return (
    <div className="l1-table-scroll">
      <table className="l1-table" aria-label="Schema 模板">
        <thead>
          <tr>
            <th>标准字段</th>
            <th>角色</th>
            <th>类型</th>
            <th>单位</th>
            <th>别名</th>
          </tr>
        </thead>
        <tbody>
          {fields.map((field) => (
            <tr key={field.field_id}>
              <td>{field.label}</td>
              <td>{ROLES.find(([id]) => id === field.role)?.[1] ?? field.role}</td>
              <td>{field.target_type}</td>
              <td>{field.target_unit || "无"}</td>
              <td>{field.aliases.join("、")}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function IssueList({ issues }: { issues: SchemaIssue[] }) {
  if (issues.length === 0) return null
  return (
    <div className="l1-records">
      {issues.slice(0, 8).map((item) => (
        <p key={`${item.code}-${item.source_column_id}-${item.message}`} className={item.severity === "blocking" ? "l1-note is-error" : "l1-note"}>
          {item.severity === "blocking" ? "阻止" : item.severity === "warning" ? "警告" : "提示"} · {item.message}
        </p>
      ))}
    </div>
  )
}
