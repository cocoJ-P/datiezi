import { useEffect, useState } from "react"
import {
  L1ApiError,
  getPrepareLatest,
  getPrepareReport,
  listPrepareIssues,
  listPrepareStages,
  listPreparedRows,
  runPrepare,
} from "./api"
import type { DatasetDetail, PreparePlan, PreparedRow, SheetInfo } from "./types"

const TABS = [
  ["summary", "摘要"],
  ["issues", "问题"],
  ["rows", "交付数据"],
  ["report", "报告"],
] as const

export function PrepareWorkspace({
  datasetId,
  detail,
  onApplied,
}: {
  datasetId: string
  detail: DatasetDetail | null
  sheets: SheetInfo[]
  onApplied: () => void
}) {
  const [plan, setPlan] = useState<PreparePlan | null>(null)
  const [tab, setTab] = useState<(typeof TABS)[number][0]>("summary")
  const [issues, setIssues] = useState<{ total: number; items: { code: string; severity: string; message: string; equipment_id?: string; source_row_id?: string }[] }>({ total: 0, items: [] })
  const [rows, setRows] = useState<PreparedRow[]>([])
  const [rowTotal, setRowTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [equipmentFilter, setEquipmentFilter] = useState("")
  const [report, setReport] = useState<Record<string, unknown> | null>(null)
  const [stages, setStages] = useState<{ stage_id: string; status: string; summary?: Record<string, unknown> | null }[]>([])
  const [error, setError] = useState("")
  const [notice, setNotice] = useState("")
  const [busy, setBusy] = useState("")

  useEffect(() => {
    const controller = new AbortController()
    setError("")
    getPrepareLatest(datasetId, controller.signal)
      .then((body) => {
        if (controller.signal.aborted) return
        setPlan(body.applied ? body : null)
        setNotice(body.applied ? `已加载准备结果 ${body.run_id}。` : "尚未运行数据准备。点击「运行数据准备」执行 P5–P8。")
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) setError(reason instanceof L1ApiError ? reason.message : "准备结果没有读取完成。")
      })
    return () => controller.abort()
  }, [datasetId])

  useEffect(() => {
    if (!plan?.run_id) {
      setStages([])
      setIssues({ total: 0, items: [] })
      setReport(null)
      return
    }
    const controller = new AbortController()
    listPrepareStages(plan.run_id, controller.signal)
      .then((body) => {
        if (!controller.signal.aborted) setStages(body.stages)
      })
      .catch(() => undefined)
    listPrepareIssues(plan.run_id, { offset: 0, limit: 50 }, controller.signal)
      .then((body) => {
        if (!controller.signal.aborted) setIssues({ total: body.total, items: body.issues })
      })
      .catch(() => undefined)
    getPrepareReport(plan.run_id, controller.signal)
      .then((body) => {
        if (!controller.signal.aborted) setReport(body.report)
      })
      .catch(() => undefined)
    return () => controller.abort()
  }, [plan?.run_id])

  useEffect(() => {
    if (tab !== "rows" || !plan?.version_id) {
      setRows([])
      setRowTotal(0)
      return
    }
    const controller = new AbortController()
    listPreparedRows(
      plan.version_id,
      { offset, limit: 50, equipmentId: equipmentFilter || undefined },
      controller.signal,
    )
      .then((body) => {
        if (controller.signal.aborted) return
        setRows(body.rows)
        setRowTotal(body.total_records)
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) setError(reason instanceof L1ApiError ? reason.message : "交付数据没有读取完成。")
      })
    return () => controller.abort()
  }, [tab, plan?.version_id, offset, equipmentFilter])

  async function onRun() {
    setBusy("准备")
    setError("")
    try {
      const body = await runPrepare(datasetId, {})
      setPlan(body)
      setNotice(`数据准备完成：${body.run_id}，状态 ${body.status}，版本 ${body.version_id}。`)
      setTab("summary")
      onApplied()
    } catch (reason: unknown) {
      setError(reason instanceof L1ApiError ? reason.message : "数据准备没有完成。")
    } finally {
      setBusy("")
    }
  }

  const readiness = plan?.readinessByAnalysisType || plan?.summary?.readinessByAnalysisType || {}
  const p5 = plan?.summary?.p5_summary as Record<string, unknown> | undefined
  const p6 = plan?.summary?.p6_summary as Record<string, unknown> | undefined
  const p7 = plan?.summary?.p7_summary as Record<string, unknown> | undefined

  return (
    <>
      <div className="l1-center">
        <div className="l1-viewer">
          {error ? <p className="l1-note is-error">{error}</p> : null}
          <section className="l1-summary-card" aria-label="数据准备摘要">
            <div className="l1-summary-head">
              <strong>{plan?.status || "未执行"}</strong>
              <span className="l1-status-line">{notice}</span>
            </div>
            <div className="l1-summary-stats">
              <span>P5 问题 {Number(p5?.issue_count ?? issues.total)}</span>
              <span>P6 保留 {Number(p6?.retained_row_count ?? "—")}</span>
              <span>P6 隔离 {Number(p6?.quarantined_row_count ?? "—")}</span>
              <span>P7 输入特征 {Number(p7?.model_input_field_count ?? "—")}</span>
              <span>
                交付行{" "}
                {Number(
                  p7?.prepared_row_count ??
                    (plan?.summary?.p8_summary as { row_count?: number } | undefined)?.row_count,
                ) || "—"}
              </span>
              <span>版本 {plan?.version_id || "—"}</span>
            </div>
            {Object.keys(readiness).length ? (
              <p className="l1-note">
                分析适用性：
                {Object.entries(readiness)
                  .map(([key, value]) => `${key}=${String(value)}`)
                  .join(" · ")}
              </p>
            ) : null}
          </section>
          <div className="l1-bar">
            <div className="l1-tools">
              {TABS.map(([id, label]) => (
                <button key={id} type="button" aria-pressed={tab === id} onClick={() => setTab(id)}>
                  {label}
                </button>
              ))}
              <button type="button" disabled={busy !== ""} onClick={() => void onRun()}>
                运行数据准备
              </button>
            </div>
            <p className="l1-meta">
              <span>{detail?.original_filename || datasetId}</span>
              <span>{plan?.run_id || "PREP 未执行"}</span>
            </p>
          </div>

          {tab === "summary" ? (
            <div className="l1-records">
              <p>阶段状态</p>
              <ul className="l1-region-list">
                {(stages.length ? stages : ["p5", "p6", "p7", "p8"].map((id) => ({ stage_id: id, status: "not_started" }))).map((stage) => (
                  <li key={stage.stage_id}>
                    <b>{stage.stage_id.toUpperCase()}</b>
                    <small> {stage.status}</small>
                  </li>
                ))}
              </ul>
              <p className="l1-note">P5–P8 共用同一套摘要与交付查询。打开「问题」可定位质量异常；打开「交付数据」按设备分页读取标准化特征。</p>
            </div>
          ) : null}

          {tab === "issues" ? (
            <div className="l1-table-scroll">
              <p className="l1-note">共 {issues.total} 条问题（分页展示前 {issues.items.length} 条）。</p>
              <table className="l1-table" aria-label="质量问题">
                <thead>
                  <tr>
                    <th>级别</th>
                    <th>代码</th>
                    <th>设备</th>
                    <th>说明</th>
                  </tr>
                </thead>
                <tbody>
                  {issues.items.map((item, index) => (
                    <tr key={`${item.code}-${index}`}>
                      <td>{item.severity}</td>
                      <td>{item.code}</td>
                      <td>{item.equipment_id || "—"}</td>
                      <td>{item.message}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}

          {tab === "rows" ? (
            <>
              <label className="l1-region-form">
                设备过滤
                <input aria-label="设备过滤" value={equipmentFilter} onChange={(event) => { setOffset(0); setEquipmentFilter(event.target.value) }} placeholder="equipment_id" />
              </label>
              <div className="l1-table-scroll">
                <table className="l1-table" aria-label="交付特征行">
                  <thead>
                    <tr>
                      <th>设备</th>
                      <th>时间</th>
                      <th>源记录</th>
                      <th>输入特征数</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row) => (
                      <tr key={row.source_row_id}>
                        <td>{row.equipment_id || "—"}</td>
                        <td>{row.timestamp_text || "—"}</td>
                        <td>{row.source_row_id}</td>
                        <td>{Object.keys(row.features?.input_features || {}).length}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="l1-pager">
                <button type="button" disabled={offset <= 0} onClick={() => setOffset(Math.max(0, offset - 50))}>
                  上一页
                </button>
                <span>
                  {rowTotal === 0 ? "0" : `${offset + 1}–${Math.min(offset + rows.length, rowTotal)}`} / {rowTotal}
                </span>
                <button type="button" disabled={offset + 50 >= rowTotal} onClick={() => setOffset(offset + 50)}>
                  下一页
                </button>
              </p>
            </>
          ) : null}

          {tab === "report" ? (
            <pre className="l1-note" style={{ whiteSpace: "pre-wrap", maxHeight: "60vh", overflow: "auto" }}>
              {report ? JSON.stringify(report, null, 2) : "还没有报告。"}
            </pre>
          ) : null}
        </div>
      </div>
      <aside className="l1-inspector" aria-label="数据准备">
        <header>
          <h3>数据准备 P5–P8</h3>
        </header>
        <p className="l1-note">默认一次运行完成质量检测、保守整理、特征隔离与发布。不修改原始单元格。</p>
        <p className="l1-note">切换阶段 Tab 不会重新执行。上游 P4 更新后准备结果会标记过期。</p>
      </aside>
    </>
  )
}
