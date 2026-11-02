import { useEffect, useRef, useState } from "react"
import type { DiagnosisTabId } from "../events/diagnosisNav"
import { L1ApiError, getDataset, runFullL1Pipeline } from "./l1/api"
import { useL1Dataset } from "./l1/datasetContext"
import type { PipelineStage } from "./l1/types"

const LAYERS: { id: DiagnosisTabId; code: string; title: string; hint: string }[] = [
  { id: "l1", code: "L1", title: "数据", hint: "接入 · 标准化 · 特征准备" },
  { id: "l2", code: "L2", title: "调度", hint: "智能推理与任务编排" },
  { id: "l3", code: "L3", title: "引擎", hint: "多引擎专业分析" },
  { id: "l4", code: "L4", title: "证据", hint: "融合验证与不确定性" },
  { id: "l5", code: "L5", title: "反馈", hint: "交付复核与知识回写" },
]

const L1_STEPS: { id: string; label: string }[] = [
  { id: "p0", label: "P0 接入" },
  { id: "p1", label: "P1 区域" },
  { id: "p2", label: "P2 分组" },
  { id: "p3", label: "P3 映射" },
  { id: "p4", label: "P4 标准化" },
  { id: "p5", label: "P5 质量" },
  { id: "p6", label: "P6 清洗" },
  { id: "p7", label: "P7 特征" },
  { id: "p8", label: "P8 发布" },
]

const L1_PASS = new Set(["completed", "completed_with_warnings", "partial"])

function stagePassed(stages: PipelineStage[], stageId: string): boolean {
  const stage = stages.find((item) => item.stage_id === stageId)
  return Boolean(stage && L1_PASS.has(stage.status))
}

function l1Passed(stages: PipelineStage[]): boolean {
  return L1_STEPS.every((step) => stagePassed(stages, step.id))
}

export function DiagnosticOverview({ onOpenLayer }: { onOpenLayer: (id: DiagnosisTabId) => void }) {
  const { selectedId, filename, status, addFile } = useL1Dataset()
  const [encoding, setEncoding] = useState("auto")
  const [delimiter, setDelimiter] = useState("auto")
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState("")
  const [failed, setFailed] = useState(false)
  const [stages, setStages] = useState<PipelineStage[]>([])
  const fileRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (!selectedId) {
      setStages([])
      return
    }
    const controller = new AbortController()
    getDataset(selectedId, controller.signal)
      .then((detail) => {
        if (!controller.signal.aborted) setStages(detail.stages ?? [])
      })
      .catch(() => {
        if (!controller.signal.aborted) setStages([])
      })
    return () => controller.abort()
  }, [selectedId, notice])

  async function onFile(file: File | undefined) {
    if (!file) return
    setBusy(true)
    setFailed(false)
    setNotice("正在上传文件…")
    try {
      const nextId = await addFile(file, { encoding, delimiter })
      const afterUpload = await getDataset(nextId)
      setStages(afterUpload.stages ?? [])
      setNotice("上传完成，正在自动执行 L1 数据处理流水线…")
      const { prepare } = await runFullL1Pipeline(
        nextId,
        setNotice,
        (detail) => setStages(detail.stages ?? []),
      )
      const detail = await getDataset(nextId)
      setStages(detail.stages ?? [])
      const ready = l1Passed(detail.stages ?? [])
      setFailed(!ready)
      setNotice(
        ready
          ? `已完成 L1 准备（${prepare.status}）。版本 ${prepare.version_id || "—"}。点击下方 L1 进入技术诊断体系。`
          : `L1 流水线已结束，状态 ${prepare.status}，尚未全部通过。可点击 L1 查看详情。`,
      )
    } catch (error: unknown) {
      setFailed(true)
      setNotice(error instanceof L1ApiError || error instanceof Error ? error.message : "文件处理没有完成。")
    } finally {
      setBusy(false)
      if (fileRef.current) fileRef.current.value = ""
    }
  }

  const l1Ok = l1Passed(stages)

  return (
    <section className="dx-control-overview" aria-label="诊断与控制总览">
      <header className="dx-control-head">
        <div>
          <h3>数据接入</h3>
          <p className="dx-control-meta">当前数据集：{filename || "尚未添加"}{status ? ` · ${status}` : ""}</p>
        </div>
        <div className="dx-control-upload">
          <select aria-label="文件编码" value={encoding} disabled={busy} onChange={(event) => setEncoding(event.target.value)}>
            <option value="auto">编码自动</option>
            <option value="utf-8">UTF-8</option>
            <option value="utf-8-sig">UTF-8 BOM</option>
            <option value="gb18030">GB18030</option>
            <option value="gbk">GBK</option>
          </select>
          <select aria-label="分隔符" value={delimiter} disabled={busy} onChange={(event) => setDelimiter(event.target.value)}>
            <option value="auto">分隔符自动</option>
            <option value="comma">逗号</option>
            <option value="tab">制表符</option>
            <option value="semicolon">分号</option>
            <option value="pipe">竖线</option>
          </select>
          <button type="button" className="dx-primary" disabled={busy} onClick={() => fileRef.current?.click()}>
            {busy ? "处理中…" : "添加新文件"}
          </button>
          <input
            ref={fileRef}
            type="file"
            accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            hidden
            aria-label="添加新文件"
            disabled={busy}
            onChange={(event) => void onFile(event.target.files?.[0])}
          />
        </div>
      </header>
      {notice ? (
        <p className={`dx-control-note${failed ? " is-alert" : ""}`} role="status">
          {notice}
        </p>
      ) : (
        <p className="dx-control-note" role="status">
          添加 CSV/XLSX 后将自动执行 L1 数据处理 Pipeline（P0–P8）。
        </p>
      )}

      <ol className="dx-layer-flow" aria-label="L1 至 L5 诊断流程">
        {LAYERS.map((layer) => {
          const passed = layer.id === "l1" ? l1Ok : false
          const tone = passed ? "is-pass" : "is-fail"
          return (
            <li key={layer.id} className="dx-layer-step">
              <button type="button" className={`dx-layer-node ${tone}`} onClick={() => onOpenLayer(layer.id)}>
                <span className="dx-layer-code">{layer.code}</span>
                <strong>{layer.title}</strong>
                <small>{layer.hint}</small>
                <em>{passed ? "已完成" : "未完成"}</em>
              </button>
              {layer.id === "l1" ? (
                <ul className="dx-l1-checklist" aria-label="L1 Pipeline P0–P8">
                  {L1_STEPS.map((step) => {
                    const done = stagePassed(stages, step.id)
                    return (
                      <li key={step.id} className={done ? "is-done" : undefined}>
                        <span className="dx-l1-check" aria-hidden="true">
                          {done ? "✓" : ""}
                        </span>
                        <span>{step.label}</span>
                      </li>
                    )
                  })}
                </ul>
              ) : null}
            </li>
          )
        })}
      </ol>
    </section>
  )
}
