import { useEffect, useMemo, useState, type FormEvent } from "react"
import {
  L1ApiError,
  applyRegions,
  listAppliedRegions,
  listRawRows,
  listRegionRows,
  previewRegions,
  suggestRegions,
  type RegionConfig,
} from "./api"
import type { DataRegion, DatasetDetail, RawCell, RawPage, RegionPlan, RegionRecord, SheetInfo } from "./types"

const ROLES = [
  ["data_table", "数据表"],
  ["note", "说明"],
  ["blank", "空行"],
  ["unclassified", "未分类"],
] as const

const CONFIDENCE_LABEL: Record<string, string> = { high: "高", medium: "中", low: "低" }
const ROLE_LABEL: Record<string, string> = { data_table: "数据表", note: "说明", blank: "空行", unclassified: "未分类" }
const PAGE_SIZES = [50, 100, 200]

type Mode = "summary" | "inspect" | "edit"
type RegionException = NonNullable<RegionPlan["exceptions"]>[number]

export function RegionWorkspace({
  datasetId,
  detail,
  sheets,
  onApplied,
}: {
  datasetId: string
  detail: DatasetDetail | null
  sheets: SheetInfo[]
  onApplied: () => void
}) {
  const [plan, setPlan] = useState<RegionPlan | null>(null)
  const [draft, setDraft] = useState<DataRegion[]>([])
  const [enabled, setEnabled] = useState<Record<string, boolean>>({})
  const [mode, setMode] = useState<Mode>("summary")
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  const [selectedId, setSelectedId] = useState("")
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState("")
  const [error, setError] = useState("")
  const [notice, setNotice] = useState("")
  const [dirty, setDirty] = useState(false)
  const [preview, setPreview] = useState<RegionPlan | null>(null)
  const [inspectSheetId, setInspectSheetId] = useState("")
  const [inspectRegionId, setInspectRegionId] = useState("")
  const [page, setPage] = useState<RawPage | null>(null)
  const [offset, setOffset] = useState(0)
  const [limit, setLimit] = useState(50)
  const [jump, setJump] = useState("")
  const [rowsLoading, setRowsLoading] = useState(false)
  const [rowsError, setRowsError] = useState("")
  const [peek, setPeek] = useState<RawCell | null>(null)
  const [copied, setCopied] = useState(false)
  const [records, setRecords] = useState<RegionRecord[] | null>(null)

  useEffect(() => {
    if (!datasetId || detail?.parse_status === "failed") {
      setPlan(null)
      setDraft([])
      return
    }
    const controller = new AbortController()
    setLoading(true)
    setError("")
    listAppliedRegions(datasetId, undefined, controller.signal)
      .then((applied) => {
        if (controller.signal.aborted) return null
        if (needsRuleUpgrade(applied)) {
          setNotice("检测到旧版 P1 结果不含数据表，正在按当前规则重新识别。")
          return applyRegions(datasetId, { source: "auto" })
        }
        if (applied.applied) return applied
        return suggestRegions(datasetId, controller.signal)
      })
      .then((body) => {
        if (!body || controller.signal.aborted) return
        applyPlan(body)
        setMode("summary")
        setPreview(null)
        setDirty(false)
        setNotice(body.applied ? statusNotice(body) : "还没有正式修订，下面是识别摘要。")
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) setError(reason instanceof L1ApiError ? reason.message : "区域识别没有完成。")
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [datasetId, detail?.parse_status, detail?.dataset_id])

  useEffect(() => {
    if (mode !== "inspect" || !datasetId || !inspectSheetId) {
      setPage(null)
      setRowsLoading(false)
      return
    }
    const controller = new AbortController()
    setRowsLoading(true)
    setRowsError("")
    listRawRows(datasetId, { sheetId: inspectSheetId, offset, limit }, controller.signal)
      .then((body) => {
        if (!controller.signal.aborted) setPage(body)
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          setPage(null)
          setRowsError(reason instanceof L1ApiError ? reason.message : "原始数据没有读完。")
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setRowsLoading(false)
      })
    return () => controller.abort()
  }, [mode, datasetId, inspectSheetId, offset, limit])

  useEffect(() => {
    if (mode !== "inspect" || !inspectRegionId || dirty) {
      setRecords(null)
      return
    }
    const controller = new AbortController()
    listRegionRows(inspectRegionId, 0, 8, controller.signal)
      .then((body) => {
        if (!controller.signal.aborted) setRecords(body.rows)
      })
      .catch(() => {
        if (!controller.signal.aborted) setRecords(null)
      })
    return () => controller.abort()
  }, [mode, inspectRegionId, dirty])

  const source = preview ?? plan
  const regions = mode === "edit" ? draft : source?.regions ?? []
  const tables = useMemo(() => regions.filter((item) => item.role === "data_table"), [regions])
  const exceptions = source?.exceptions ?? []
  const attention = useMemo(() => attentionItems(exceptions, tables), [exceptions, tables])
  const selected = draft.find((item) => item.client_region_id === selectedId) ?? regions.find((item) => item.client_region_id === selectedId) ?? null
  const inspectRegion = regions.find((item) => item.client_region_id === inspectRegionId || item.region_id === inspectRegionId) ?? null
  const sheetGroups = useMemo(() => buildSheetGroups(sheets, source, tables, enabled, attention), [sheets, source, tables, enabled, attention])
  const columns = useMemo(() => {
    const count = page?.max_column ?? sheets.find((item) => item.sheet_id === inspectSheetId)?.max_column ?? 0
    return Array.from({ length: count }, (_, index) => index + 1)
  }, [page?.max_column, sheets, inspectSheetId])
  const total = page?.total_rows ?? 0
  const from = total === 0 ? 0 : offset + 1
  const to = Math.min(offset + (page?.rows.length ?? 0), total)
  const pageCount = Math.max(1, Math.ceil(total / limit) || 1)

  function applyPlan(body: RegionPlan) {
    setPlan(body)
    setDraft(body.regions)
    setEnabled(Object.fromEntries(body.sheet_settings.map((item) => [item.sheet_id, item.enabled !== false])))
    setSelectedId(body.regions.find((item) => item.role === "data_table")?.client_region_id ?? body.regions[0]?.client_region_id ?? "")
  }

  function config(accept = false): RegionConfig {
    return {
      sheets: Object.entries(enabled).map(([id, value]) => ({ sheet_id: id, enabled: value })),
      regions: draft,
      accept_review: accept,
    }
  }

  function localIssues(items: DataRegion[]): string[] {
    const issues: string[] = []
    for (const item of items) {
      if (item.end_row < item.start_row || item.end_column < item.start_column) {
        issues.push(`${item.sheet_name} ${item.client_region_id} 的结束位置早于起始位置。`)
      }
      if (item.role === "data_table" && item.header_row_count > 0) {
        const start = item.header_start_row ?? 0
        if (start < item.start_row || start + item.header_row_count - 1 > item.end_row) {
          issues.push(`${item.sheet_name} ${item.range_label} 的表头范围超出数据区域。`)
        }
      }
    }
    for (let index = 0; index < items.length; index += 1) {
      for (let next = index + 1; next < items.length; next += 1) {
        const left = items[index]
        const right = items[next]
        if (left.sheet_id !== right.sheet_id || left.role !== "data_table" || right.role !== "data_table") continue
        const separate = left.end_row < right.start_row || right.end_row < left.start_row || left.end_column < right.start_column || right.end_column < left.start_column
        if (!separate) issues.push(`${left.sheet_name} 存在重叠区域 ${left.range_label} 与 ${right.range_label}。`)
      }
    }
    return issues.slice(0, 6)
  }

  function update(patch: Partial<DataRegion>) {
    setDirty(true)
    setPreview(null)
    setNotice("已修改本地范围，确认应用后才会生成新修订。")
    setDraft((current) => current.map((item) => (item.client_region_id === selectedId ? { ...item, ...patch, detection_method: "user" } : item)))
  }

  function openInspect(sheetId: string, region?: DataRegion | null, focusRow?: number) {
    const target = region ?? tables.find((item) => item.sheet_id === sheetId) ?? null
    setMode("inspect")
    setInspectSheetId(sheetId)
    setInspectRegionId(target?.client_region_id ?? "")
    setSelectedId(target?.client_region_id ?? "")
    setPeek(null)
    const row = focusRow ?? target?.start_row ?? 1
    setJump(String(row))
    setOffset(Math.max(0, Math.floor((row - 1) / limit) * limit))
    setExpanded((current) => ({ ...current, [sheetId]: true }))
  }

  function openEdit(region?: DataRegion | null, sheetId?: string) {
    setMode("edit")
    setPreview(null)
    if (region) setSelectedId(region.client_region_id)
    else if (sheetId) {
      const first = draft.find((item) => item.sheet_id === sheetId && item.role === "data_table") ?? draft.find((item) => item.sheet_id === sheetId)
      if (first) setSelectedId(first.client_region_id)
    }
    setNotice("手动调整模式：改完后直接应用。可选用详细预览。")
  }

  function discardEdits() {
    if (!plan) return
    setDraft(plan.regions)
    setEnabled(Object.fromEntries(plan.sheet_settings.map((item) => [item.sheet_id, item.enabled !== false])))
    setPreview(null)
    setDirty(false)
    setMode("summary")
    setNotice("已放弃未提交的调整，恢复当前生效配置。")
  }

  async function onPreview() {
    const issues = localIssues(draft)
    if (issues.length > 0) {
      setError(issues[0])
      return
    }
    setBusy("预览")
    setError("")
    try {
      const body = await previewRegions(datasetId, config())
      setPreview(body)
      setNotice("这是详细预览，尚未写入正式结果。")
    } catch (reason: unknown) {
      setError(reason instanceof L1ApiError ? reason.message : "预览没有完成。")
    } finally {
      setBusy("")
    }
  }

  async function onApply(accept = false) {
    const issues = localIssues(draft)
    if (issues.length > 0) {
      setError(issues[0])
      return
    }
    setBusy("执行")
    setError("")
    try {
      const body = await applyRegions(datasetId, config(accept))
      applyPlan(body)
      setPreview(null)
      setDirty(false)
      setMode("summary")
      setNotice(body.reused ? "范围没有变化，沿用当前修订。" : statusNotice(body))
      onApplied()
    } catch (reason: unknown) {
      setError(reason instanceof L1ApiError ? reason.message : "执行没有完成。")
    } finally {
      setBusy("")
    }
  }

  function addRegion(sheetId: string) {
    const sheet = sheets.find((item) => item.sheet_id === sheetId)
    if (!sheet) return
    const id = `UR-${Date.now().toString(36)}`
    const region: DataRegion = {
      region_id: null,
      client_region_id: id,
      dataset_id: datasetId,
      sheet_id: sheet.sheet_id,
      sheet_name: sheet.name,
      sheet_enabled: enabled[sheet.sheet_id] !== false,
      role: "data_table",
      start_row: 1,
      end_row: Math.max(1, sheet.max_row || 1),
      start_column: 1,
      end_column: Math.max(1, sheet.max_column || 1),
      header_start_row: 1,
      header_row_count: 1,
      data_start_row: 2,
      data_end_row: Math.max(2, sheet.max_row || 2),
      detection_method: "user",
      confidence: "medium",
      confirmation_status: "suggested",
      confirmed: true,
      record_count: 0,
      columns: [],
      warnings: [{ code: "user_range", message: "新增区域，请确认范围后应用。" }],
      range_label: `A1:${columnLetter(Math.max(1, sheet.max_column || 1))}${Math.max(1, sheet.max_row || 1)}`,
    }
    setDirty(true)
    setDraft((current) => [...current, region])
    setSelectedId(id)
    setExpanded((current) => ({ ...current, [sheetId]: true }))
    setNotice("已新增数据区域，确认范围后应用。")
  }

  function removeRegion(clientId: string) {
    setDirty(true)
    setPreview(null)
    setDraft((current) => {
      const next = current.filter((item) => item.client_region_id !== clientId)
      if (selectedId === clientId) setSelectedId(next[0]?.client_region_id ?? "")
      return next
    })
    setNotice("已删除本地区域，应用后才会写入新修订。")
  }

  function onJump(event: FormEvent) {
    event.preventDefault()
    const value = Number(jump)
    if (!Number.isInteger(value) || value < 1 || (total > 0 && value > total)) {
      setRowsError("请输入当前工作表中的原始行号。")
      return
    }
    setOffset(Math.floor((value - 1) / limit) * limit)
  }

  async function copyText(value: string) {
    let copiedOk = false
    try {
      if (navigator.clipboard?.writeText) {
        await Promise.race([navigator.clipboard.writeText(value), new Promise((_, reject) => window.setTimeout(() => reject(new Error("timeout")), 400))])
        copiedOk = true
      }
    } catch {
      copiedOk = false
    }
    if (!copiedOk) {
      const area = document.createElement("textarea")
      area.value = value
      document.body.appendChild(area)
      area.select()
      copiedOk = document.execCommand("copy")
      area.remove()
    }
    if (copiedOk) setCopied(true)
  }

  const enabledCount = sheetGroups.filter((item) => item.enabled).length
  const recordCount = source?.run?.record_count ?? tables.reduce((sum, item) => sum + item.record_count, 0)
  const status = source?.status ?? source?.run?.status ?? detail?.stages.find((item) => item.stage_id === "p1")?.status ?? "not_started"

  return (
    <>
      <div className="l1-center">
        <div className="l1-viewer">
          <div className="l1-bar">
            <div className="l1-tools">
              <button type="button" aria-pressed={mode === "summary"} onClick={() => setMode("summary")}>
                识别摘要
              </button>
              <button type="button" disabled={!inspectSheetId && tables.length === 0} aria-pressed={mode === "inspect"} onClick={() => openInspect(inspectSheetId || tables[0]?.sheet_id || sheets[0]?.sheet_id || "")}>
                查看原始数据
              </button>
              <button type="button" aria-pressed={mode === "edit"} onClick={() => openEdit(selected)}>
                手动调整
              </button>
              {mode === "edit" ? (
                <>
                  <button type="button" disabled={busy !== ""} onClick={() => void onPreview()}>
                    详细预览
                  </button>
                  <button type="button" disabled={busy !== ""} onClick={() => void onApply(status === "needs_review")}>
                    应用调整
                  </button>
                  <button type="button" disabled={!dirty && !preview} onClick={discardEdits}>
                    放弃调整
                  </button>
                </>
              ) : null}
              {status === "needs_review" && mode !== "edit" ? (
                <button type="button" disabled={busy !== ""} onClick={() => void onApply(true)}>
                  接受当前范围并继续
                </button>
              ) : null}
            </div>
            <p className="l1-meta">
              <span className="l1-status-line">{notice || "正在准备识别摘要。"}</span>
            </p>
          </div>

          {error ? <p className="l1-note is-error">{error}</p> : null}
          {loading ? <p className="l1-empty">正在读取识别摘要。</p> : null}
          {!loading && detail?.parse_status === "failed" ? <p className="l1-empty">P0 解析没有完成，不能识别数据区域。原始文件仍可下载。</p> : null}

          {!loading && source ? (
            <>
              <section className="l1-summary-card" aria-label="P1 状态摘要">
                <div className="l1-summary-head">
                  <strong>{statusHeadline(status, source.downstream_ready)}</strong>
                  <span>{source.run ? `修订 ${source.run.revision}` : "尚未落库"}</span>
                </div>
                <div className="l1-summary-stats">
                  <span>
                    <b>{sheetGroups.length}</b>
                    <small>工作表</small>
                  </span>
                  <span>
                    <b>{enabledCount}</b>
                    <small>已启用</small>
                  </span>
                  <span>
                    <b>{tables.length}</b>
                    <small>数据区域</small>
                  </span>
                  <span>
                    <b>{recordCount}</b>
                    <small>数据记录</small>
                  </span>
                  <span>
                    <b>{attention.length}</b>
                    <small>待关注</small>
                  </span>
                </div>
              </section>

              {attention.length > 0 ? (
                <section className="l1-exception-panel" aria-label="异常区域">
                  <header>
                    <strong>待关注异常</strong>
                    <small>{attention.length} 项</small>
                  </header>
                  <ul>
                    {attention.map((item) => (
                      <li key={item.key}>
                        <div>
                          <b>{item.title}</b>
                          <span>{item.message}</span>
                        </div>
                        <div className="l1-tools">
                          <button type="button" onClick={() => openInspect(item.sheetId, item.region, item.focusRow)}>
                            查看原始数据
                          </button>
                          <button type="button" onClick={() => openEdit(item.region, item.sheetId)}>
                            查看并调整
                          </button>
                        </div>
                      </li>
                    ))}
                  </ul>
                </section>
              ) : null}

              {mode === "summary" || mode === "edit" ? (
                <section className="l1-sheet-panel" aria-label="工作表及区域列表">
                  {sheetGroups.map((group) => {
                    const open = expanded[group.sheet_id] ?? (group.issueCount > 0 || mode === "edit")
                    return (
                      <article key={group.sheet_id} className={group.issueCount > 0 ? "has-issue" : undefined}>
                        <header>
                          <button type="button" className="l1-sheet-toggle" aria-expanded={open} onClick={() => setExpanded((current) => ({ ...current, [group.sheet_id]: !open }))}>
                            <b>{group.name}</b>
                            <span>
                              {group.enabled ? "启用" : "停用"} · 区域 {group.regions.length} · 记录 {group.recordCount}
                              {group.auxiliaryCells > 0 ? ` · 表外单元格 ${group.auxiliaryCells}` : ""}
                              {group.issueCount > 0 ? ` · 异常 ${group.issueCount}` : ""}
                              {group.visibility !== "visible" ? " · 隐藏表" : ""}
                            </span>
                          </button>
                          <div className="l1-tools">
                            {mode === "edit" ? (
                              <button type="button" onClick={() => addRegion(group.sheet_id)}>
                                新增区域
                              </button>
                            ) : null}
                            <button type="button" onClick={() => openInspect(group.sheet_id, group.regions[0] ?? null)}>
                              查看原始数据
                            </button>
                          </div>
                        </header>
                        {open ? (
                          <ul className="l1-region-list">
                            {group.regions.length === 0 ? <li className="l1-note">这张工作表没有识别出数据表。</li> : null}
                            {group.regions.map((region) => (
                              <li key={region.client_region_id}>
                                <button type="button" aria-pressed={region.client_region_id === selectedId} onClick={() => setSelectedId(region.client_region_id)}>
                                  <b>
                                    {ROLE_LABEL[region.role] ?? region.role} {region.range_label}
                                  </b>
                                  <span>
                                    表头 {headerText(region)} · 记录 {region.record_count} · 确定性 {CONFIDENCE_LABEL[region.confidence] ?? region.confidence}
                                  </span>
                                  {region.warnings[0] ? <small>{region.warnings[0].message}</small> : null}
                                </button>
                                <div className="l1-tools">
                                  <button type="button" onClick={() => openInspect(region.sheet_id, region)}>
                                    查看原始数据
                                  </button>
                                  {mode === "edit" ? (
                                    <button type="button" onClick={() => removeRegion(region.client_region_id)}>
                                      删除
                                    </button>
                                  ) : (
                                    <button type="button" onClick={() => openEdit(region)}>
                                      调整
                                    </button>
                                  )}
                                </div>
                              </li>
                            ))}
                          </ul>
                        ) : null}
                      </article>
                    )
                  })}
                </section>
              ) : null}

              {preview?.diff ? <DiffPlan diff={preview.diff} /> : null}

              {mode === "inspect" ? (
                <InspectPanel
                  sheetName={sheets.find((item) => item.sheet_id === inspectSheetId)?.name ?? inspectSheetId}
                  region={inspectRegion}
                  records={records}
                  page={page}
                  columns={columns}
                  rowsError={rowsError}
                  rowsLoading={rowsLoading}
                  from={from}
                  to={to}
                  total={total}
                  pageCount={pageCount}
                  offset={offset}
                  limit={limit}
                  jump={jump}
                  selectedCell={peek?.cell_id ?? ""}
                  onLimit={(value) => {
                    setOffset(0)
                    setLimit(value)
                  }}
                  onJump={onJump}
                  onJumpChange={setJump}
                  onPage={setOffset}
                  onCell={(cell) => {
                    setCopied(false)
                    setPeek(cell)
                  }}
                  onBack={() => setMode("summary")}
                  onEdit={() => openEdit(inspectRegion)}
                />
              ) : null}
            </>
          ) : null}
        </div>
      </div>

      {mode === "edit" ? (
        <aside className="l1-inspector" aria-label="区域调整">
          <header>
            <h3>手动调整</h3>
          </header>
          {!selected ? (
            <p className="l1-note">选择一个区域后可以修改范围和表头。</p>
          ) : (
            <div className="l1-region-form">
              <label>
                启用此工作表
                <input
                  type="checkbox"
                  checked={enabled[selected.sheet_id] !== false}
                  onChange={(event) => {
                    setDirty(true)
                    setPreview(null)
                    setEnabled((current) => ({ ...current, [selected.sheet_id]: event.target.checked }))
                  }}
                />
              </label>
              <label>
                区域类型
                <select aria-label="区域类型" value={selected.role} onChange={(event) => update({ role: event.target.value as DataRegion["role"], header_row_count: event.target.value === "data_table" ? selected.header_row_count : 0 })}>
                  {ROLES.map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
              <div className="l1-region-grid">
                <label>
                  起始行
                  <input aria-label="起始行" type="number" min={1} value={selected.start_row || ""} onChange={(event) => update({ start_row: Number(event.target.value) })} />
                </label>
                <label>
                  结束行
                  <input aria-label="结束行" type="number" min={1} value={selected.end_row || ""} onChange={(event) => update({ end_row: Number(event.target.value) })} />
                </label>
                <label>
                  起始列
                  <input aria-label="起始列" type="number" min={1} value={selected.start_column || ""} onChange={(event) => update({ start_column: Number(event.target.value) })} />
                </label>
                <label>
                  结束列
                  <input aria-label="结束列" type="number" min={1} value={selected.end_column || ""} onChange={(event) => update({ end_column: Number(event.target.value) })} />
                </label>
                <label>
                  表头起始行
                  <input aria-label="表头起始行" type="number" min={1} value={selected.header_start_row ?? ""} onChange={(event) => update({ header_start_row: Number(event.target.value) })} />
                </label>
                <label>
                  表头行数
                  <input aria-label="表头行数" type="number" min={0} value={selected.header_row_count} onChange={(event) => update({ header_row_count: Number(event.target.value) })} />
                </label>
              </div>
              {selected.warnings.map((item) => (
                <p key={item.message} className="l1-note">
                  {item.message}
                </p>
              ))}
            </div>
          )}
        </aside>
      ) : null}

      {peek ? (
        <div className="l1-peek" role="dialog" aria-label="完整原始值">
          <header>
            <strong>{peek.coordinate}</strong>
            <button type="button" onClick={() => setPeek(null)}>
              关闭
            </button>
          </header>
          <pre>{peek.raw_value === "" ? "（空字段，原始数据中有这个位置。）" : peek.raw_value}</pre>
          <dl className="l1-peek-meta">
            <div>
              <dt>cell_id</dt>
              <dd>{peek.cell_id}</dd>
            </div>
            <div>
              <dt>类型</dt>
              <dd>{peek.cell_type}</dd>
            </div>
          </dl>
          <button type="button" onClick={() => void copyText(peek.raw_value)}>
            {copied ? "已复制" : "复制原始值"}
          </button>
        </div>
      ) : null}
    </>
  )
}

function InspectPanel({
  sheetName,
  region,
  records,
  page,
  columns,
  rowsError,
  rowsLoading,
  from,
  to,
  total,
  pageCount,
  offset,
  limit,
  jump,
  selectedCell,
  onLimit,
  onJump,
  onJumpChange,
  onPage,
  onCell,
  onBack,
  onEdit,
}: {
  sheetName: string
  region: DataRegion | null
  records: RegionRecord[] | null
  page: RawPage | null
  columns: number[]
  rowsError: string
  rowsLoading: boolean
  from: number
  to: number
  total: number
  pageCount: number
  offset: number
  limit: number
  jump: string
  selectedCell: string
  onLimit: (value: number) => void
  onJump: (event: FormEvent) => void
  onJumpChange: (value: string) => void
  onPage: (offset: number) => void
  onCell: (cell: RawCell | null) => void
  onBack: () => void
  onEdit: () => void
}) {
  return (
    <section className="l1-inspect-panel" aria-label="原始数据局部查看">
      <div className="l1-bar">
        <div className="l1-tools">
          <button type="button" onClick={onBack}>
            返回摘要
          </button>
          <button type="button" onClick={onEdit}>
            调整此区域
          </button>
          <span>
            {sheetName}
            {region ? ` · ${region.range_label}` : ""} · 当前 {from}–{to} / 共 {total} 行
          </span>
          <select aria-label="分页数量" value={limit} onChange={(event) => onLimit(Number(event.target.value))}>
            {PAGE_SIZES.map((size) => (
              <option key={size} value={size}>
                每页 {size} 行
              </option>
            ))}
          </select>
          <form onSubmit={onJump}>
            <input aria-label="跳转到原始行" inputMode="numeric" value={jump} placeholder="原始行号" onChange={(event) => onJumpChange(event.target.value)} />
            <button type="submit">跳转</button>
          </form>
        </div>
      </div>
      <p className="l1-legend">
        <i className="is-header" /> 表头 <i className="is-data" /> 当前区域
      </p>
      {region && records && records.length > 0 ? (
        <div className="l1-records">
          <p>
            区域样例 · {region.record_count} 行记录
          </p>
          <table className="l1-table" aria-label="区域样例">
            <thead>
              <tr>
                <th>原表</th>
                {(region.columns.length > 0 ? region.columns : [{ letter: "值", header: [] as string[], column_number: 0 }]).map((column) => (
                  <th key={column.column_number || column.letter}>{column.header.length > 0 ? column.header.join(" / ") : column.letter}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {records.map((row) => (
                <tr key={row.source_row_id}>
                  <td>
                    {row.row_number}
                    {row.hidden ? " 隐" : ""}
                  </td>
                  {region.columns.length > 0
                    ? region.columns.map((column) => <td key={column.column_number}>{row.values.find((value) => value.column_number === column.column_number)?.raw_value ?? ""}</td>)
                    : row.values.map((value) => <td key={value.cell_id}>{value.raw_value}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      <div className="l1-table-scroll">
        {rowsError ? (
          <p className="l1-empty">{rowsError}</p>
        ) : (
          <table className="l1-table" aria-label="原始数据局部">
            <thead>
              <tr>
                <th className="l1-index">#</th>
                {columns.map((column) => (
                  <th key={column}>{columnLetter(column)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {page && page.rows.length > 0 ? (
                page.rows.map((row) => {
                  const byColumn = new Map(row.cells.map((cell) => [cell.column_number, cell]))
                  return (
                    <tr key={row.source_row_id} className={row.hidden ? "is-hidden" : undefined}>
                      <th className="l1-index" title={row.source_row_id}>
                        {row.row_number}
                        {row.hidden ? " 隐" : ""}
                      </th>
                      {columns.map((column) => {
                        const cell = byColumn.get(column) ?? null
                        const marked = region ? regionClass(region, row.row_number, column) : ""
                        return (
                          <td
                            key={column}
                            className={["l1-cell", cell ? "" : "is-absent", marked, selectedCell && cell?.cell_id === selectedCell ? "is-selected" : ""].filter(Boolean).join(" ")}
                            title={cell ? `${cell.raw_value}\n${cell.cell_id}` : "此坐标没有单元格"}
                            onClick={() => onCell(cell)}
                          >
                            <span>{cell ? displayText(cell) : ""}</span>
                          </td>
                        )
                      })}
                    </tr>
                  )
                })
              ) : (
                <tr>
                  <td className="l1-empty" colSpan={Math.max(1, columns.length + 1)}>
                    {rowsLoading ? "正在读取这一页。" : "这个工作表没有单元格。"}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        )}
      </div>
      <p className="l1-pager">
        <button type="button" disabled={offset <= 0 || rowsLoading} onClick={() => onPage(Math.max(0, offset - limit))}>
          上一页
        </button>
        <span>
          第 {Math.floor(offset / limit) + 1} / {pageCount} 页
        </span>
        <button type="button" disabled={rowsLoading || offset + limit >= total} onClick={() => onPage(offset + limit)}>
          下一页
        </button>
      </p>
    </section>
  )
}

function DiffPlan({ diff }: { diff: NonNullable<RegionPlan["diff"]> }) {
  return (
    <div className="l1-diff">
      <strong>{diff.baseline_revision ? `对照已执行修订 ${diff.baseline_revision}` : "还没有已执行版本"}</strong>
      <span>新增区域 {diff.added.length}</span>
      <span>移除区域 {diff.removed.length}</span>
      <span>表头变化 {diff.header_changes.length}</span>
      <span>数据行归属变化 {diff.changed_data_row_count}</span>
      <span>数据记录 {diff.record_count_before} → {diff.record_count_after}</span>
    </div>
  )
}

function buildSheetGroups(
  sheets: SheetInfo[],
  source: RegionPlan | null,
  tables: DataRegion[],
  enabled: Record<string, boolean>,
  attention: AttentionItem[],
) {
  const settings = source?.sheet_settings ?? []
  const order = settings.length > 0 ? settings : sheets.map((item) => ({ sheet_id: item.sheet_id, name: item.name, visibility: item.visibility, enabled: true }))
  return order.map((sheet) => {
    const regions = tables.filter((item) => item.sheet_id === sheet.sheet_id)
    const summary = source?.sheet_summaries?.find((item) => item.sheet_id === sheet.sheet_id)
    return {
      sheet_id: sheet.sheet_id,
      name: sheet.name,
      visibility: sheet.visibility,
      enabled: enabled[sheet.sheet_id] !== false && sheet.enabled !== false,
      regions,
      recordCount: regions.reduce((sum, item) => sum + item.record_count, 0),
      issueCount: attention.filter((item) => item.sheetId === sheet.sheet_id).length,
      auxiliaryCells: summary?.auxiliary_cells ?? 0,
    }
  })
}

type AttentionItem = {
  key: string
  title: string
  message: string
  sheetId: string
  region: DataRegion | null
  focusRow?: number
}

function attentionItems(exceptions: RegionException[], tables: DataRegion[]): AttentionItem[] {
  const items: AttentionItem[] = []
  for (const item of exceptions) {
    if (item.code === "empty_sheet" || item.code === "auxiliary_content") continue
    const region = tables.find((entry) => entry.sheet_id === item.sheet_id) ?? null
    items.push({
      key: `${item.code}-${item.sheet_id ?? ""}-${item.message}`,
      title: exceptionTitle(item.code),
      message: item.message,
      sheetId: item.sheet_id ?? region?.sheet_id ?? "",
      region,
      focusRow: item.start_row,
    })
  }
  for (const region of tables) {
    for (const warning of region.warnings) {
      if (!warning.code || !["header_ambiguous", "recovered_table", "omitted_table"].includes(warning.code)) continue
      items.push({
        key: `${region.client_region_id}-${warning.code}-${warning.message}`,
        title: exceptionTitle(warning.code),
        message: `${region.sheet_name} ${region.range_label}：${warning.message}`,
        sheetId: region.sheet_id,
        region,
        focusRow: region.start_row,
      })
    }
  }
  return items
}

function exceptionTitle(code: string): string {
  if (code === "omitted_table") return "可能遗漏的数据区域"
  if (code === "header_ambiguous") return "表头识别不明确"
  if (code === "recovered_table") return "疑似相邻数据表"
  if (code === "region_overlap") return "区域范围冲突"
  return "需要关注"
}

function needsRuleUpgrade(plan: RegionPlan): boolean {
  if (!plan.applied) return false
  const tables = plan.regions.filter((item) => item.role === "data_table")
  if (tables.length > 0) return false
  const rule = plan.run?.rule_id ?? plan.rule_id
  if (rule === "sheet_region_heuristic_v1") return true
  return (plan.run?.record_count ?? 0) === 0 && plan.regions.some((item) => item.role === "unclassified")
}

function statusHeadline(status: string, ready?: boolean): string {
  if (status === "needs_review") return "识别完成，待审核"
  if (status === "failed") return "识别失败"
  if (status === "completed_with_warnings") return ready === false ? "识别完成，有警告" : "识别已完成"
  if (status === "completed") return "识别已完成"
  if (status === "not_started") return "尚未执行"
  return statusText(status)
}

function statusNotice(body: RegionPlan): string {
  if (body.status === "needs_review" || body.run?.status === "needs_review") return "识别结果待审核。请查看异常后调整，或接受当前范围。"
  if (body.downstream_ready) return "识别已完成。正常结果无需确认，可继续设备分组。"
  return `当前状态：${statusText(body.status ?? body.run?.status)}`
}

function statusText(status: string | undefined): string {
  if (status === "completed") return "已完成"
  if (status === "completed_with_warnings") return "已完成，有警告"
  if (status === "needs_review") return "待审核"
  if (status === "failed") return "失败"
  return "未执行"
}

function headerText(region: DataRegion): string {
  if (!region.header_row_count || !region.header_start_row) return "未确定"
  if (region.header_row_count === 1) return `第 ${region.header_start_row} 行`
  return `第 ${region.header_start_row}–${region.header_start_row + region.header_row_count - 1} 行`
}

function regionClass(region: DataRegion, rowNumber: number, column: number): string {
  if (rowNumber < region.start_row || rowNumber > region.end_row || column < region.start_column || column > region.end_column) return ""
  if (region.role === "data_table" && region.header_row_count > 0 && region.header_start_row && rowNumber >= region.header_start_row && rowNumber < region.header_start_row + region.header_row_count) return "is-header"
  if (region.role === "data_table") return "is-data"
  return ""
}

function displayText(cell: RawCell): string {
  if (cell.cell_type === "formula" && cell.cached_value) return cell.cached_value
  if (cell.raw_value === "") return "∅"
  return cell.raw_value
}

function columnLetter(column: number): string {
  let number = column
  let letters = ""
  while (number > 0) {
    const remainder = (number - 1) % 26
    letters = String.fromCharCode(65 + remainder) + letters
    number = Math.floor((number - 1) / 26)
  }
  return letters
}
