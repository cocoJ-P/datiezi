import { useEffect, useMemo, useState, type FormEvent } from "react"
import { L1ApiError, getDataset, listRawRows, listSheets, readPipeline } from "./api"
import { DatasetReport } from "./DatasetReport"
import { useL1Dataset } from "./datasetContext"
import { GroupingWorkspace } from "./GroupingWorkspace"
import { PrepareWorkspace } from "./PrepareWorkspace"
import { SchemaWorkspace } from "./SchemaWorkspace"
import { StandardizationWorkspace } from "./StandardizationWorkspace"
import { RegionWorkspace } from "./RegionWorkspace"
import type { DatasetDetail, PipelineStage, RawCell, RawPage, SheetInfo } from "./types"
import "./l1.css"

const STAGE_KEY = "l1.stage"
const PAGE_SIZES = [50, 100, 200]

export function PipelinePanel() {
  const { selectedId, registerStageReset } = useL1Dataset()
  const [stages, setStages] = useState<PipelineStage[]>([])
  const [stageId, setStageId] = useState(() => readStorage(STAGE_KEY) ?? "p0")
  const [detail, setDetail] = useState<DatasetDetail | null>(null)
  const [sheets, setSheets] = useState<SheetInfo[]>([])
  const [sheetId, setSheetId] = useState("")
  const [page, setPage] = useState<RawPage | null>(null)
  const [offset, setOffset] = useState(0)
  const [limit, setLimit] = useState(100)
  const [jump, setJump] = useState("")
  const [peek, setPeek] = useState<RawCell | null>(null)
  const [copied, setCopied] = useState(false)
  const [listError, setListError] = useState("")
  const [detailError, setDetailError] = useState("")
  const [rowsError, setRowsError] = useState("")
  const [rowsLoading, setRowsLoading] = useState(false)

  useEffect(() => {
    const controller = new AbortController()
    readPipeline(controller.signal)
      .then((body) => {
        if (controller.signal.aborted) return
        setStages(body.stages)
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) setListError(messageOf(error))
      })
    return () => controller.abort()
  }, [])

  useEffect(() => {
    if (!selectedId) {
      setDetail(null)
      setSheets([])
      return
    }
    const controller = new AbortController()
    setDetailError("")
    getDataset(selectedId, controller.signal)
      .then((body) => {
        if (controller.signal.aborted) return
        setDetail(body)
        setStages(body.stages)
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) setDetailError(messageOf(error))
      })
    listSheets(selectedId, controller.signal)
      .then((items) => {
        if (controller.signal.aborted) return
        setSheets(items)
        setSheetId((current) => (current && items.some((item) => item.sheet_id === current) ? current : items[0]?.sheet_id ?? ""))
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) setRowsError(messageOf(error))
      })
    return () => controller.abort()
  }, [selectedId])

  useEffect(() => {
    if (stageId !== "p0" || !selectedId || !detail || detail.dataset_id !== selectedId || detail.parse_status === "failed" || !sheetId) {
      if (stageId !== "p0") setPage(null)
      setRowsLoading(false)
      return
    }
    const controller = new AbortController()
    setRowsLoading(true)
    setRowsError("")
    listRawRows(selectedId, { sheetId, offset, limit }, controller.signal)
      .then((body) => {
        if (!controller.signal.aborted) setPage(body)
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          setPage(null)
          setRowsError(messageOf(error))
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setRowsLoading(false)
      })
    return () => controller.abort()
  }, [stageId, selectedId, detail, sheetId, offset, limit])

  const activeStage = stages.find((item) => item.stage_id === stageId) ?? stages[0]
  const activeSheet = sheets.find((item) => item.sheet_id === sheetId) ?? null
  const columnCount = page?.max_column ?? activeSheet?.max_column ?? 0
  const columns = useMemo(() => Array.from({ length: columnCount }, (_, index) => index + 1), [columnCount])
  const total = page?.total_rows ?? 0
  const from = total === 0 ? 0 : offset + 1
  const to = Math.min(offset + (page?.rows.length ?? 0), total)
  const pageCount = Math.max(1, Math.ceil(total / limit))
  useEffect(() => registerStageReset(() => {
    writeStorage(STAGE_KEY, "p0")
    setStageId("p0")
    setOffset(0)
    setSheetId("")
    setPeek(null)
  }), [registerStageReset])

  function chooseSheet(id: string) {
    setOffset(0)
    setPeek(null)
    setSheetId(id)
  }

  function chooseStage(id: string) {
    writeStorage(STAGE_KEY, id)
    setStageId(id)
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

  function openCell(cell: RawCell | null) {
    setCopied(false)
    setPeek(cell)
  }

  async function copyText(value: string) {
    let copiedOk = false
    try {
      if (navigator.clipboard?.writeText) {
        await Promise.race([
          navigator.clipboard.writeText(value),
          new Promise((_, reject) => window.setTimeout(() => reject(new Error("timeout")), 400)),
        ])
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

  return (
    <section className="l1-shell">
      {listError || detailError ? <p className="l1-note is-error">{listError || detailError}</p> : null}

      <div className="l1-body">
        <nav className="l1-stages" aria-label="L1 Pipeline">
          {stages.map((item, index) => (
            <button
              key={item.stage_id}
              type="button"
              aria-current={item.stage_id === (activeStage?.stage_id ?? "p0") ? "step" : undefined}
              className={item.implemented ? undefined : "is-pending"}
              onClick={() => chooseStage(item.stage_id)}
            >
              <b>P{index}</b>
              <span>{item.name}</span>
              <small>{item.implemented ? item.status_label : "待开发"}</small>
            </button>
          ))}
        </nav>

        {activeStage?.stage_id === "p1" && selectedId && detail?.dataset_id === selectedId ? (
          <RegionWorkspace
            datasetId={selectedId}
            detail={detail}
            sheets={sheets}
            onApplied={() => {
              void getDataset(selectedId)
                .then((body) => {
                  setDetail(body)
                  setStages(body.stages)
                })
                .catch((error: unknown) => setDetailError(messageOf(error)))
            }}
          />
        ) : activeStage?.stage_id === "p2" && selectedId && detail?.dataset_id === selectedId ? (
          <GroupingWorkspace
            datasetId={selectedId}
            detail={detail}
            sheets={sheets}
            sheetId={sheetId}
            limit={limit}
            debug={false}
            onSheet={chooseSheet}
            onJumpChange={setJump}
            onPage={(next) => setOffset(next)}
            onApplied={() => {
              void getDataset(selectedId)
                .then((body) => {
                  setDetail(body)
                  setStages(body.stages)
                })
                .catch((error: unknown) => setDetailError(messageOf(error)))
            }}
          />
        ) : activeStage?.stage_id === "p3" && selectedId && detail?.dataset_id === selectedId ? (
          <SchemaWorkspace
            datasetId={selectedId}
            detail={detail}
            sheets={sheets}
            limit={limit}
            debug={false}
            onSheet={chooseSheet}
            onJumpChange={setJump}
            onPage={(next) => setOffset(next)}
            onApplied={() => {
              void getDataset(selectedId)
                .then((body) => {
                  setDetail(body)
                  setStages(body.stages)
                })
                .catch((error: unknown) => setDetailError(messageOf(error)))
            }}
          />
        ) : activeStage?.stage_id === "p4" && selectedId && detail?.dataset_id === selectedId ? (
          <StandardizationWorkspace
            datasetId={selectedId}
            detail={detail}
            sheets={sheets}
            limit={limit}
            debug={false}
            onSheet={chooseSheet}
            onJumpChange={setJump}
            onPage={(next) => setOffset(next)}
            onApplied={() => {
              void getDataset(selectedId)
                .then((body) => {
                  setDetail(body)
                  setStages(body.stages)
                })
                .catch((error: unknown) => setDetailError(messageOf(error)))
            }}
          />
        ) : activeStage && ["p5", "p6", "p7", "p8"].includes(activeStage.stage_id) && selectedId && detail?.dataset_id === selectedId ? (
          <PrepareWorkspace
            datasetId={selectedId}
            detail={detail}
            sheets={sheets}
            onApplied={() => {
              void getDataset(selectedId)
                .then((body) => {
                  setDetail(body)
                  setStages(body.stages)
                })
                .catch((error: unknown) => setDetailError(messageOf(error)))
            }}
          />
        ) : activeStage?.stage_id === "p1" || activeStage?.stage_id === "p2" || activeStage?.stage_id === "p3" || activeStage?.stage_id === "p4" ? (
          <div className="l1-center">
            <p className="l1-meta">
              <DatasetReport detail={detail} />
            </p>
            <p className="l1-hold">还没有已完成 P0 索引的数据集。</p>
          </div>
        ) : (
        <div className="l1-center">
          {activeStage && activeStage.stage_id !== "p0" ? (
            <>
              <p className="l1-meta">
                <DatasetReport detail={detail} />
              </p>
              <p className="l1-hold">
                {activeStage.stage_id.toUpperCase()} {activeStage.name}：请先完成 P0 索引并选择数据集。
              </p>
            </>
          ) : (
            <Viewer
              detail={detail}
              sheets={sheets}
              sheetId={sheetId}
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
              onSheet={chooseSheet}
              onLimit={(value) => {
                setOffset(0)
                setLimit(value)
              }}
              onJump={onJump}
              onJumpChange={setJump}
              onPage={(next) => setOffset(next)}
              onCell={(cell) => void openCell(cell)}
            />
          )}
        </div>
        )}

      </div>

      {peek ? (
        <div className="l1-peek" role="dialog" aria-label="完整原始值">
          <header>
            <strong>{peek.coordinate}</strong>
            <button type="button" onClick={() => setPeek(null)}>
              关闭
            </button>
          </header>
          <pre>{peek.raw_value === "" ? "（空字段，原始数据中有这个位置。）" : peek.raw_value}</pre>
          <button type="button" onClick={() => void copyText(peek.raw_value)}>
            {copied ? "已复制" : "复制原始值"}
          </button>
        </div>
      ) : null}
    </section>
  )
}

function Viewer({
  detail,
  sheets,
  sheetId,
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
  onSheet,
  onLimit,
  onJump,
  onJumpChange,
  onPage,
  onCell,
}: {
  detail: DatasetDetail | null
  sheets: SheetInfo[]
  sheetId: string
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
  onSheet: (id: string) => void
  onLimit: (value: number) => void
  onJump: (event: FormEvent) => void
  onJumpChange: (value: string) => void
  onPage: (offset: number) => void
  onCell: (cell: RawCell | null) => void
}) {
  const activeSheet = sheets.find((item) => item.sheet_id === sheetId)
  return (
    <div className="l1-viewer">
      <div className="l1-bar">
      <div className="l1-tools">
        <span>
          当前 {from}–{to} / 共 {total} 行
          {activeSheet ? ` · ${activeSheet.max_column} 列` : ""}
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
      <p className="l1-meta">
        <DatasetReport detail={detail} />
      </p>
      </div>
      <div className="l1-table-scroll">
        {!detail ? (
          <p className="l1-empty">还没有可检视的数据集。</p>
        ) : detail.parse_status === "failed" ? (
          <p className="l1-empty">解析没有完成，表格不会显示不完整的记录。原始文件仍可从报告中下载。</p>
        ) : rowsError ? (
          <p className="l1-empty">{rowsError}</p>
        ) : (
          <table className="l1-table" aria-label="原始数据">
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
                      <th className="l1-index" title={row.hidden ? "隐藏行，内容已保留" : row.parse_status}>
                        {row.row_number}
                        {row.hidden ? " 隐" : ""}
                      </th>
                      {columns.map((column) => {
                        const cell = byColumn.get(column) ?? null
                        const text = cell ? displayText(cell) : ""
                        return (
                          <td
                            key={column}
                            className={["l1-cell", cell ? "" : "is-absent", selectedCell && cell?.cell_id === selectedCell ? "is-selected" : ""].filter(Boolean).join(" ")}
                            title={cell ? cell.raw_value : "此坐标没有单元格"}
                            onClick={() => onCell(cell)}
                          >
                            <span>{text}</span>
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
      <div className="l1-sheets" role="tablist" aria-label="工作表">
        {sheets.map((item) => (
          <button key={item.sheet_id} type="button" role="tab" aria-selected={item.sheet_id === sheetId} className={item.sheet_id === sheetId ? "is-active" : undefined} onClick={() => onSheet(item.sheet_id)}>
            {item.name}
            {item.visibility !== "visible" ? "（隐藏）" : ""}
          </button>
        ))}
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
    </div>
  )
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

function readStorage(key: string): string | null {
  try {
    return window.localStorage.getItem(key)
  } catch {
    return null
  }
}

function writeStorage(key: string, value: string) {
  try {
    window.localStorage.setItem(key, value)
  } catch {
    /* 浏览器禁用本地存储时只保持本次页面状态。 */
  }
}

function messageOf(error: unknown): string {
  if (error instanceof L1ApiError) return error.message
  if (error instanceof Error && error.message) return error.message
  return "请求没有完成。"
}
