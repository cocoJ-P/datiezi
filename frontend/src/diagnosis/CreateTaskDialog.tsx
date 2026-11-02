import { useMemo, useState } from "react"
import { DEMO_LINE } from "../overview/demoData"
import { INPUT_FIELDS, inspectTable, suggestMapping, timeWindow, type FieldMapping } from "./fields"
import { parseUpload, type ParsedTable } from "./parseTable"
import { STRATEGIES } from "./labels"
import { createImportedRuns, createManualRun } from "./store"
import type { DatasetRole } from "./types"

type Mode = "existing" | "upload"

export function CreateTaskDialog({ onClose, onCreated }: { onClose: () => void; onCreated: (id: string) => void }) {
  const [step, setStep] = useState(1)
  const [mode, setMode] = useState<Mode>("upload")
  const [equipmentId, setEquipmentId] = useState(DEMO_LINE.equipment[0]?.id ?? "")
  const [table, setTable] = useState<ParsedTable | null>(null)
  const [fileName, setFileName] = useState("")
  const [parseError, setParseError] = useState("")
  const [mapping, setMapping] = useState<FieldMapping>({})
  const [withheld, setWithheld] = useState<string[]>([])
  const [role, setRole] = useState<DatasetRole>("inference")
  const [taskName, setTaskName] = useState("")
  const [strategyId, setStrategyId] = useState<string>(STRATEGIES[0].id)
  const issues = table ? inspectTable(table, mapping, withheld) : []
  const blocked = role === "inference" && issues.some((item) => item.includes("答案字段"))
  const windowLabel = table ? timeWindow(table, mapping.time) : "模拟快照时间"

  const previewRows = table?.rows.slice(0, 6) ?? []
  const equipment = DEMO_LINE.equipment.find((item) => item.id === equipmentId)

  async function takeFile(file: File | undefined) {
    if (!file) return
    setParseError("")
    try {
      const parsed = await parseUpload(file)
      const suggested = suggestMapping(parsed.columns)
      setTable(parsed)
      setFileName(file.name)
      setMapping(suggested.mapping)
      setWithheld(suggested.withheld)
      setTaskName(file.name.replace(/\.[^.]+$/, "") || "导入诊断")
      setStep(2)
    } catch (error) {
      setTable(null)
      setParseError(error instanceof Error ? error.message : "文件没有解析成功。")
    }
  }

  function create() {
    if (!taskName.trim()) return
    if (mode === "existing") {
      const id = createManualRun({
        name: taskName.trim(),
        equipmentId,
        strategyId,
        windowLabel: DEMO_LINE.updatedAt,
      })
      onCreated(id)
      return
    }
    if (!table || blocked) return
    const ids = createImportedRuns({
      fileName,
      columns: table.columns,
      rows: table.rows,
      equipmentColumn: mapping.equipment,
      withheldColumns: withheld,
      role,
      taskName: taskName.trim(),
      strategyId,
      windowLabel,
    })
    if (ids[0]) onCreated(ids[0])
  }

  const title = useMemo(() => ["选择数据", "数据预览", "字段映射与检查", "创建诊断任务"][step - 1], [step])

  return (
    <div className="dx-modal-back" role="presentation" onMouseDown={onClose}>
      <div className="dx-modal" role="dialog" aria-modal="true" aria-labelledby="dx-create-title" onMouseDown={(event) => event.stopPropagation()}>
        <header className="dx-modal-head">
          <div>
            <p>新建诊断 · {step}/4</p>
            <h2 id="dx-create-title">{title}</h2>
          </div>
          <button type="button" className="ev-back" onClick={onClose}>
            关闭
          </button>
        </header>
        {step === 1 ? (
          <div className="dx-form">
            <div className="dx-choice">
              <button type="button" aria-pressed={mode === "existing"} onClick={() => setMode("existing")}>
                从模拟设备快照创建
              </button>
              <button type="button" aria-pressed={mode === "upload"} onClick={() => setMode("upload")}>
                上传 CSV / xlsx
              </button>
            </div>
            {mode === "existing" ? (
              <label>
                设备
                <select value={equipmentId} onChange={(event) => setEquipmentId(event.target.value)}>
                  {DEMO_LINE.equipment.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.name} {item.code}
                    </option>
                  ))}
                </select>
              </label>
            ) : (
              <label
                className="dx-drop"
                onDragOver={(event) => event.preventDefault()}
                onDrop={(event) => {
                  event.preventDefault()
                  void takeFile(event.dataTransfer.files[0])
                }}
              >
                拖入文件，或点击选择。支持 CSV 与 xlsx。
                <input
                  type="file"
                  accept=".csv,.txt,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                  onChange={(event) => void takeFile(event.target.files?.[0])}
                />
              </label>
            )}
            {parseError ? <p className="is-alert">{parseError}</p> : null}
          </div>
        ) : null}
        {step === 2 && mode === "existing" && equipment ? (
          <div className="dx-form">
            <p>
              {equipment.name} {equipment.code} · 快照 {DEMO_LINE.updatedAt} · 模拟数据
            </p>
            <ul className="dx-preview-list">
              {equipment.metrics.map((item) => (
                <li key={item.id}>
                  {item.label} {item.value} {item.unit}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        {step === 2 && mode === "upload" && table ? (
          <div className="dx-form">
            <p>
              {fileName} · {table.rows.length} 行{table.truncated ? "（预览已截断）" : ""} · {table.columns.length} 列
            </p>
            <div className="dx-table-wrap">
              <table className="dx-table">
                <thead>
                  <tr>
                    {table.columns.map((column) => (
                      <th key={column}>{column}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {previewRows.map((row, index) => (
                    <tr key={index}>
                      {row.map((cell, cellIndex) => (
                        <td key={`${index}-${cellIndex}`}>{cell || "—"}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ) : null}
        {step === 3 && mode === "existing" ? <p className="dx-form">测点来自模拟快照，这一步不需要字段映射。</p> : null}
        {step === 3 && mode === "upload" && table ? (
          <div className="dx-form dx-map">
            {INPUT_FIELDS.map((field) => (
              <label key={field.id}>
                {field.label}
                <select
                  value={mapping[field.id] ?? ""}
                  onChange={(event) => setMapping({ ...mapping, [field.id]: event.target.value || null })}
                >
                  <option value="">未映射</option>
                  {table.columns.map((column) => (
                    <option key={column} value={column}>
                      {column}
                    </option>
                  ))}
                </select>
              </label>
            ))}
            <div>
              <h3>与预测输入分开的字段</h3>
              <p>{withheld.length > 0 ? withheld.join("、") : "没有识别到故障类型、根因、严重度或建议措施。"}</p>
              <ul>
                {issues.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </div>
          </div>
        ) : null}
        {step === 4 ? (
          <div className="dx-form">
            <label>
              任务名称
              <input value={taskName} onChange={(event) => setTaskName(event.target.value)} />
            </label>
            {mode === "upload" ? (
              <label>
                数据用途
                <select value={role} onChange={(event) => setRole(event.target.value as DatasetRole)}>
                  <option value="training">训练数据</option>
                  <option value="validation">已标注验证数据</option>
                  <option value="inference">待预测测试数据</option>
                </select>
              </label>
            ) : null}
            <label>
              诊断策略
              <select value={strategyId} onChange={(event) => setStrategyId(event.target.value)}>
                {STRATEGIES.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.label}（演示，不执行算法）
                  </option>
                ))}
              </select>
            </label>
            <p>时间范围：{mode === "upload" ? windowLabel : DEMO_LINE.updatedAt}</p>
            <p>确认后任务进入队列并保持等待。这一步不会运行诊断算法。</p>
            {blocked ? <p className="is-alert">待预测数据不能把答案字段映射为输入。</p> : null}
          </div>
        ) : null}
        <footer className="dx-modal-foot">
          {step > 1 ? (
            <button type="button" className="ev-back" onClick={() => setStep((value) => value - 1)}>
              上一步
            </button>
          ) : (
            <span />
          )}
          {step < 4 ? (
            <button
              type="button"
              className="dx-primary"
              disabled={step === 1 && mode === "upload" && !table}
              onClick={() => {
                if (step === 1 && mode === "existing") setTaskName(`${equipment?.name ?? "设备"}手动诊断`)
                setStep((value) => value + 1)
              }}
            >
              下一步
            </button>
          ) : (
            <button type="button" className="dx-primary" disabled={!taskName.trim() || blocked} onClick={create}>
              创建任务
            </button>
          )}
        </footer>
      </div>
    </div>
  )
}
