import { useEffect, useState } from "react"
import {
  L1ApiError,
  applyGrouping,
  equipmentFields,
  groupingSummary,
  listEquipmentGroups,
  listGroupRows,
  listGroupingRows,
  listUngroupedRows,
  previewGrouping,
  type GroupingConfig,
} from "./api"
import type { DatasetDetail, EquipmentFieldPlan, GroupingPlan, GroupingRow, SheetInfo } from "./types"

const STATUS_LABEL: Record<string, string> = {
  completed: "已完成",
  completed_with_warnings: "已完成（有警告）",
  needs_review: "待审核",
  failed: "失败",
  not_started: "未执行",
}
const GROUP_PAGE = 40

const VIEWS = [
  ["sheet", "按 Sheet"],
  ["equipment", "按设备"],
  ["ungrouped", "未归组"],
  ["all", "全部记录"],
] as const

const REASONS: Record<string, string> = {
  empty: "空编号",
  invalid: "非法占位",
  no_field: "未识别字段",
  unconfirmed_field: "字段未确认",
  override: "人工移出",
}

export function GroupingWorkspace({
  datasetId,
  detail,
  sheets,
  sheetId,
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
  sheetId: string
  limit: number
  debug: boolean
  onSheet: (id: string) => void
  onPage: (offset: number) => void
  onJumpChange: (value: string) => void
  onApplied: () => void
}) {
  const [fields, setFields] = useState<EquipmentFieldPlan | null>(null)
  const [choices, setChoices] = useState<Record<string, { column_id: string | null; confirmed: boolean }>>({})
  const [matchMode, setMatchMode] = useState("exact")
  const [crossSheet, setCrossSheet] = useState("separate")
  const [ledgerKey, setLedgerKey] = useState("")
  const [ledgerAsset, setLedgerAsset] = useState("")
  const [ledger, setLedger] = useState<{ match_key: string; asset_id: string }[]>([])
  const [overrides, setOverrides] = useState<{ source_row_id: string; action: string; equipment_value?: string }[]>([])
  const [assignValue, setAssignValue] = useState("")
  const [preview, setPreview] = useState<GroupingPlan | null>(null)
  const [applied, setApplied] = useState<GroupingPlan | null>(null)
  const [view, setView] = useState<(typeof VIEWS)[number][0]>("sheet")
  const [groupId, setGroupId] = useState("")
  const [rows, setRows] = useState<GroupingRow[]>([])
  const [rowTotal, setRowTotal] = useState(0)
  const [rowOffset, setRowOffset] = useState(0)
  const [selected, setSelected] = useState<GroupingRow | null>(null)
  const [error, setError] = useState("")
  const [notice, setNotice] = useState("")
  const [busy, setBusy] = useState("")
  const [editing, setEditing] = useState(false)
  const [groupOffset, setGroupOffset] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    setError("")
    setPreview(null)
    setSelected(null)
    setEditing(false)
    setGroupOffset(0)
    // 默认只拉摘要 + 分页设备组，不触发预览计算；字段建议按需加载
    Promise.all([
      groupingSummary(datasetId, controller.signal),
      listEquipmentGroups(datasetId, { limit: GROUP_PAGE, offset: 0, signal: controller.signal }),
    ])
      .then(([summary, current]) => {
        if (controller.signal.aborted) return
        const merged: GroupingPlan = current.applied
          ? { ...current, summary: summary.summary ?? current.summary, warnings: summary.warnings ?? current.warnings }
          : summary
        setApplied(merged.applied ? merged : null)
        setMatchMode(current.config?.match_mode || "exact")
        setCrossSheet(current.config?.cross_sheet || "separate")
        setOverrides(current.config?.overrides || [])
        setNotice(merged.applied ? statusNotice(merged) : "等待自动分组，或打开调整后重新执行。")
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) setError(reason instanceof L1ApiError ? reason.message : "分组摘要没有读取完成。")
      })
    return () => controller.abort()
  }, [datasetId])

  useEffect(() => {
    if (!editing || fields) return
    const controller = new AbortController()
    equipmentFields(datasetId, controller.signal)
      .then((fieldPlan) => {
        if (controller.signal.aborted) return
        setFields(fieldPlan)
        const initial: Record<string, { column_id: string | null; confirmed: boolean }> = {}
        for (const region of fieldPlan.regions) {
          const saved = applied?.config?.regions?.find((item) => item.region_id === region.region_id)
          initial[region.region_id] = saved
            ? { column_id: saved.column_id, confirmed: saved.confirmed }
            : { column_id: region.suggested_column_id, confirmed: !region.requires_review && region.confirmation_status !== "needs_confirmation" }
        }
        setChoices(initial)
        if (!applied?.config?.cross_sheet) setCrossSheet(fieldPlan.default_cross_sheet || "separate")
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) setError(reason instanceof L1ApiError ? reason.message : "设备字段没有读取完成。")
      })
    return () => controller.abort()
  }, [editing, fields, datasetId, applied])

  useEffect(() => {
    if (view === "equipment" && !groupId) {
      const first = (preview?.groups ?? applied?.groups ?? []).find((item) => item.group_id)
      if (first?.group_id) setGroupId(first.group_id)
    }
  }, [view, groupId, preview, applied])

  useEffect(() => {
    if (!applied?.applied || preview) {
      setRows(preview?.sample_rows ?? [])
      setRowTotal(preview?.sample_rows?.length ?? 0)
      return
    }
    const controller = new AbortController()
    const task =
      view === "equipment" && groupId
        ? listGroupRows(groupId, rowOffset, limit, controller.signal)
        : view === "ungrouped"
          ? listUngroupedRows(datasetId, rowOffset, limit, controller.signal)
          : listGroupingRows(datasetId, { offset: rowOffset, limit, sheetId: view === "sheet" ? sheetId : undefined }, controller.signal)
    task
      .then((body) => {
        if (controller.signal.aborted) return
        setRows(body.rows)
        setRowTotal(body.total_records)
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) setError(reason instanceof L1ApiError ? reason.message : "分组记录没有读取完成。")
      })
    return () => controller.abort()
  }, [applied, preview, view, groupId, rowOffset, limit, sheetId, datasetId])

  function config(): GroupingConfig {
    return {
      match_mode: matchMode,
      cross_sheet: crossSheet,
      regions: Object.entries(choices).map(([regionId, choice]) => ({ region_id: regionId, column_id: choice.column_id, confirmed: choice.confirmed })),
      ledger,
      overrides,
    }
  }

  async function onPreview() {
    setBusy("预览")
    setError("")
    try {
      const body = await previewGrouping(datasetId, config())
      setPreview(body)
      setNotice("这是分组预览，尚未写入正式结果。")
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
      const body = await applyGrouping(datasetId, config())
      setPreview(null)
      setApplied(body)
      setGroupId(body.groups[0]?.group_id ?? "")
      setNotice(statusNotice(body))
      onApplied()
    } catch (reason: unknown) {
      setError(reason instanceof L1ApiError ? reason.message : "执行没有完成。")
    } finally {
      setBusy("")
    }
  }

  const balance = preview?.balance ?? applied?.balance
  const summary = preview?.summary ?? applied?.summary
  const links = preview?.sheet_links ?? applied?.sheet_links ?? []
  const groups = preview?.groups ?? applied?.groups ?? []
  const matches = preview?.potential_cross_sheet_matches ?? applied?.potential_cross_sheet_matches ?? fields?.potential_cross_sheet_matches ?? []
  const activeSheet = sheets.find((item) => item.sheet_id === sheetId)
  const sheetRegions = fields?.regions.filter((item) => item.sheet_id === (activeSheet?.sheet_id ?? sheetId)) ?? []
  const status = preview?.status ?? applied?.status ?? applied?.run?.status ?? "not_started"

  return (
    <>
      <div className="l1-center">
        <div className="l1-viewer">
          {error ? <p className="l1-note is-error">{error}</p> : null}
          <section className="l1-summary-card" aria-label="P2 状态摘要">
            <div className="l1-summary-head">
              <strong>{STATUS_LABEL[status] || status}</strong>
              <span className="l1-status-line">{notice || "正在准备分组摘要。"}</span>
            </div>
            <div className="l1-summary-stats">
              <span>设备组 {summary?.equipment_count ?? balance?.equipment_count ?? 0}</span>
              <span>已归组 {summary?.grouped_count ?? balance?.grouped_count ?? 0}</span>
              <span>未归组 {summary?.ungrouped_count ?? balance?.ungrouped_count ?? 0}</span>
              <span>未归组占比 {Math.round(((summary?.ungrouped_ratio ?? 0) * 100))}%</span>
              <span>警告 {summary?.warning_count ?? applied?.warnings?.length ?? 0}</span>
              <span>修订 {applied?.run?.revision ?? "—"}</span>
            </div>
            {matches.length ? (
              <p className="l1-note">跨表同号候选 {matches.length} 个（默认隔离，未自动合并为同一资产）。</p>
            ) : null}
          </section>
          {preview?.diff ? (
            <p className="l1-diff">
              <strong>{preview.diff.baseline_revision ? `对照修订 ${preview.diff.baseline_revision}` : "还没有已执行版本"}</strong>
              <span>设备 {preview.diff.equipment_count_before} → {preview.diff.equipment_count_after}</span>
              <span>已分组 {preview.diff.grouped_count_before} → {preview.diff.grouped_count_after}</span>
              <span>未归组 {preview.diff.ungrouped_count_before} → {preview.diff.ungrouped_count_after}</span>
              <span>归属变化 {preview.diff.changed_record_count}</span>
            </p>
          ) : null}
          <div className="l1-bar">
          <div className="l1-tools">
            {VIEWS.map(([id, label]) => (
              <button key={id} type="button" aria-pressed={view === id} onClick={() => { setView(id); setRowOffset(0) }}>
                {label}
              </button>
            ))}
            <button type="button" aria-pressed={editing} disabled={busy !== ""} onClick={() => setEditing((value) => !value)}>
              {editing ? "收起调整" : "打开调整"}
            </button>
            {editing ? (
              <>
                <button type="button" disabled={busy !== ""} onClick={() => void onPreview()}>
                  预览调整
                </button>
                <button type="button" disabled={busy !== ""} onClick={() => void onApply()}>
                  重新执行 P2
                </button>
              </>
            ) : null}
          </div>
          <p className="l1-meta">
            <span>{applied?.run ? `修订 ${applied.run.revision}` : "P2 未执行"}</span>
            <span>业务记录 {balance?.business_count ?? fields?.business_count ?? 0}</span>
            {balance?.ok ? <span>数量守恒</span> : null}
            <span>{detail?.filename || detail?.dataset_id || datasetId}</span>
          </p>
          </div>
          {view === "equipment" ? (
            <div className="l1-tools">
              {groups.map((group) => (
                <button key={group.label + (group.group_id ?? "")} type="button" aria-pressed={group.group_id === groupId} onClick={() => group.group_id && setGroupId(group.group_id)}>
                  {group.label} · {group.record_count}
                </button>
              ))}
              {(applied?.group_total ?? groups.length) > groups.length ? (
                <button
                  type="button"
                  disabled={busy !== ""}
                  onClick={() => {
                    const next = groupOffset + GROUP_PAGE
                    setBusy("加载设备")
                    listEquipmentGroups(datasetId, { limit: GROUP_PAGE, offset: next })
                      .then((page) => {
                        setApplied((current) =>
                          current
                            ? { ...current, groups: [...current.groups, ...page.groups], group_total: page.group_total }
                            : page,
                        )
                        setGroupOffset(next)
                      })
                      .catch((reason: unknown) => setError(reason instanceof L1ApiError ? reason.message : "设备组分页没有完成。"))
                      .finally(() => setBusy(""))
                  }}
                >
                  更多设备组…
                </button>
              ) : null}
            </div>
          ) : null}
          <RecordGrid
            rows={rows}
            onOpen={(row) => {
              setSelected(row)
              onSheet(row.sheet_id)
              onJumpChange(String(row.row_number))
              onPage(Math.floor((row.row_number - 1) / limit) * limit)
            }}
          />
          <p className="l1-pager">
            <button type="button" disabled={rowOffset <= 0} onClick={() => setRowOffset(Math.max(0, rowOffset - limit))}>
              上一页
            </button>
            <span>
              {rowTotal === 0 ? "0" : `${rowOffset + 1}–${Math.min(rowOffset + rows.length, rowTotal)}`} / {rowTotal}
            </span>
            <button type="button" disabled={rowOffset + limit >= rowTotal} onClick={() => setRowOffset(rowOffset + limit)}>
              下一页
            </button>
          </p>
          <SourceTree links={links} onSheet={onSheet} />
        </div>
      </div>
      <aside className="l1-inspector" aria-label="设备分组">
        <header>
          <h3>设备分组</h3>
        </header>
        {!editing ? (
          <>
            <p className="l1-note">摘要优先：打开调整后才会加载字段配置并允许重新计算。</p>
            {(applied?.region_stats || []).slice(0, 8).map((region) => (
              <p key={region.region_id} className="l1-note">
                {region.sheet_name} {region.range_label} · {region.selected_header || "未选字段"}
                {region.requires_review ? " · 待审核" : ""}
              </p>
            ))}
            {(applied?.warnings || []).slice(0, 5).map((item, index) => (
              <p key={`${item.code}-${index}`} className="l1-note">
                异常 {item.code}
                {typeof item.sheet_name === "string" ? ` · ${item.sheet_name}` : ""}
                {typeof item.range_label === "string" ? ` ${item.range_label}` : ""}
              </p>
            ))}
          </>
        ) : (
          <>
        <p className="l1-note">{fields?.rule_summary}</p>
        {sheetRegions.map((region) => {
          const choice = choices[region.region_id] ?? { column_id: null, confirmed: false }
          return (
            <div key={region.region_id} className="l1-region-form">
              <strong>
                {region.sheet_name} {region.range_label}
              </strong>
              <label>
                设备编号字段
                <select
                  aria-label={`${region.sheet_name} 设备编号字段`}
                  value={choice.column_id ?? ""}
                  onChange={(event) => {
                    setPreview(null)
                    setChoices((current) => ({ ...current, [region.region_id]: { ...choice, column_id: event.target.value || null } }))
                  }}
                >
                  <option value="">不分组</option>
                  {region.columns.map((column) => (
                    <option key={column.column_id} value={column.column_id}>
                      {(column.header.filter(Boolean).join(" / ") || column.column_id)}
                    </option>
                  ))}
                </select>
              </label>
              {region.requires_review || region.confirmation_status === "needs_confirmation" ? (
                <label>
                  <input
                    type="checkbox"
                    checked={choice.confirmed}
                    onChange={(event) => {
                      setPreview(null)
                      setChoices((current) => ({ ...current, [region.region_id]: { ...choice, confirmed: event.target.checked } }))
                    }}
                  />
                  确认此字段（仅歧义区域需要）
                </label>
              ) : null}
              {region.requires_review ? <p className="l1-note">该区域设备字段存在歧义，已标记待审核。</p> : null}
              {region.recognition_source ? <p className="l1-note">识别来源：{region.recognition_source} · 置信度 {region.confidence_level || "—"}</p> : null}
            </div>
          )
        })}
        <label className="l1-region-form">
          匹配规则
          <select aria-label="匹配规则" value={matchMode} onChange={(event) => { setPreview(null); setMatchMode(event.target.value) }}>
            <option value="exact">保留原始文本</option>
            <option value="trim_casefold">忽略首尾空格和大小写</option>
          </select>
        </label>
        <label className="l1-region-form">
          跨 Sheet 策略
          <select aria-label="跨 Sheet 合并策略" value={crossSheet} onChange={(event) => { setPreview(null); setCrossSheet(event.target.value) }}>
            <option value="separate">默认：按工作表隔离</option>
            <option value="same_text">相同文本合并（显式）</option>
            <option value="ledger">仅台账映射（显式）</option>
          </select>
        </label>
        <p className="l1-note">跨表同号默认隔离并保留候选关联；group_id 不等于 asset_id。未归组记录会完整保留。</p>
        {crossSheet === "ledger" ? (
          <div className="l1-region-form">
            <label>
              匹配文本
              <input aria-label="台账匹配文本" value={ledgerKey} onChange={(event) => setLedgerKey(event.target.value)} />
            </label>
            <label>
              资产编号
              <input aria-label="台账资产编号" value={ledgerAsset} onChange={(event) => setLedgerAsset(event.target.value)} />
            </label>
            <button
              type="button"
              onClick={() => {
                if (!ledgerKey || !ledgerAsset) return
                setLedger((current) => [...current, { match_key: ledgerKey, asset_id: ledgerAsset }])
                setLedgerKey("")
                setLedgerAsset("")
                setPreview(null)
              }}
            >
              添加台账映射
            </button>
            {ledger.map((item) => (
              <p key={`${item.match_key}-${item.asset_id}`} className="l1-note">
                {item.match_key} → {item.asset_id}
              </p>
            ))}
          </div>
        ) : null}
          </>
        )}
        {selected ? (
          <div className="l1-region-form">
            <strong>当前记录</strong>
            {debug ? (
              <dl>
                <dt>原始设备编号</dt>
                <dd>{selected.original_equipment_value || "（空）"}</dd>
                <dt>逻辑分组编号</dt>
                <dd>{selected.equipment_id || "未归组"}</dd>
                <dt>字段来源</dt>
                <dd>{selected.field_header || "无"} · {selected.column_id || "未选"}</dd>
                <dt>匹配规则</dt>
                <dd>{selected.mapping_rule_id}</dd>
                <dt>原始 Sheet</dt>
                <dd>{selected.sheet_name}</dd>
                <dt>原始位置</dt>
                <dd>
                  {selected.coordinate || `第 ${selected.row_number} 行`} · {selected.source_row_id}
                </dd>
                <dt>分组版本</dt>
                <dd>{selected.run_id}</dd>
                <dt>P1 区域</dt>
                <dd>{selected.region_id}</dd>
              </dl>
            ) : (
              <p className="l1-note">
                {selected.sheet_name} 第 {selected.row_number} 行 · {selected.equipment_id || REASONS[selected.ungroup_reason] || "未归组"}
              </p>
            )}
            <label>
              改分到
              <input aria-label="修正设备编号" value={assignValue} onChange={(event) => setAssignValue(event.target.value)} />
            </label>
            <button
              type="button"
              onClick={() => {
                setOverrides((current) => [...current.filter((item) => item.source_row_id !== selected.source_row_id), { source_row_id: selected.source_row_id, action: "assign", equipment_value: assignValue }])
                setPreview(null)
              }}
            >
              加入修正
            </button>
            <button
              type="button"
              onClick={() => {
                setOverrides((current) => [...current.filter((item) => item.source_row_id !== selected.source_row_id), { source_row_id: selected.source_row_id, action: "ungroup" }])
                setPreview(null)
              }}
            >
              移出分组
            </button>
          </div>
        ) : (
          <p className="l1-note">选择一条记录后，可以修正它的设备归属。修正会在预览和执行后生效，不改原始单元格。</p>
        )}
      </aside>
    </>
  )
}

function RecordGrid({ rows, onOpen }: { rows: GroupingRow[]; onOpen: (row: GroupingRow) => void }) {
  if (rows.length === 0) return <p className="l1-empty">这个视图还没有记录。切换视图或打开调整后查看。</p>
  const sections: { key: string; title: string; headers: string[]; rows: GroupingRow[] }[] = []
  for (const row of rows) {
    const headers = row.fields.map((field) => field.header)
    const key = `${row.region_id}:${headers.join("|")}`
    const last = sections[sections.length - 1]
    if (!last || last.key !== key) sections.push({ key, title: `${row.sheet_name} · ${row.region_id}`, headers, rows: [row] })
    else last.rows.push(row)
  }
  return (
    <div className="l1-table-scroll">
      {sections.map((section) => (
        <table key={section.key} className="l1-table" aria-label="设备分组记录">
          <caption>{section.title}</caption>
          <thead>
            <tr>
              <th>原表</th>
              <th>设备编号</th>
              {section.headers.map((header) => (
                <th key={header}>{header}</th>
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
                <td>{row.membership_status === "grouped" ? row.original_equipment_value : REASONS[row.ungroup_reason] || row.ungroup_reason}</td>
                {row.fields.map((field) => (
                  <td key={field.column_id}>{field.present ? field.raw_value : ""}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      ))}
    </div>
  )
}

function SourceTree({ links, onSheet }: { links: GroupingPlan["sheet_links"]; onSheet: (id: string) => void }) {
  if (links.length === 0) return null
  return (
    <div className="l1-records">
      <p>工作表与设备</p>
      <ul className="l1-region-list">
        {links.map((link) => (
          <li key={link.sheet_id}>
            <button type="button" onClick={() => onSheet(link.sheet_id)}>
              <b>{link.sheet_name}</b>
              <small>
                设备 {link.equipment_count} · 已分组 {link.grouped_count} · 未归组 {link.ungrouped_count}
                {link.equipment_ids.length ? ` · ${link.equipment_ids.join("、")}` : ""}
              </small>
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}

function statusNotice(plan: GroupingPlan): string {
  const status = plan.status || plan.run?.status || "completed"
  const label = STATUS_LABEL[status] || status
  const run = plan.run ? `${plan.run.run_id}，修订 ${plan.run.revision}` : "当前结果"
  if (status === "needs_review") return `P2 ${label}：${run}。请只处理标记待审核的区域。`
  if (status === "completed_with_warnings") return `P2 ${label}：${run}。跨表同号已按隔离处理。`
  if (plan.reused) return `P2 复用已有结果：${run}。`
  return `P2 ${label}：${run}。原始文件没有改动。`
}
