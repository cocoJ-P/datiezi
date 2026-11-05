import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react"
import type { DiagnosisRoutingSummary, RoutingGroupSummary } from "../l2/api"
import type { AnalysisRunSummary } from "../l3/api"
import type { PipelineStage } from "../l1/types"
import {
  type FlowPhase,
  type FlowTone,
  type L2MainStatus,
  stagePassed,
} from "./flowModel"
import "./flow.css"

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

type Point = { x: number; y: number }

type Props = {
  phase: FlowPhase
  stages: PipelineStage[]
  l1Ok: boolean
  l1Busy: boolean
  l2Main: L2MainStatus
  l2Tone: FlowTone
  progressText: string | null
  multiHint: string | null
  routing: {
    total: number
    l2Complete: number
    l3Review: number
    groups: RoutingGroupSummary[]
    branches: RoutingGroupSummary[]
  } | null
  routingLoading: boolean
  summaries: DiagnosisRoutingSummary[]
  l3Runs?: Record<string, AnalysisRunSummary | null>
  reasonText: string
  reasonOpen: boolean
  actions: {
    showViewResults: boolean
    showGoTrain: boolean
    showConfigure: boolean
    showViewReason: boolean
    showRetry: boolean
    showEnterL2: boolean
    showEnterL1: boolean
  }
  onOpenResults: () => void
  onOpenTraining: () => void
  onOpenConfigure: () => void
  onOpenReason: () => void
  onRetry: () => void
  onEnterL2: () => void
  onEnterL1: () => void
  onEnterL3: (branch: RoutingGroupSummary, summary?: DiagnosisRoutingSummary) => void
}

function toneClass(tone: FlowTone): string {
  return `is-${tone}`
}

function orthogonalPath(from: Point, to: Point): string {
  const midX = from.x + (to.x - from.x) * 0.55
  return `M ${from.x} ${from.y} L ${midX} ${from.y} L ${midX} ${to.y} L ${to.x} ${to.y}`
}

export function DiagnosticFlowCanvas({
  phase,
  stages,
  l1Ok,
  l1Busy,
  l2Main,
  l2Tone,
  progressText,
  multiHint,
  routing,
  routingLoading,
  summaries,
  l3Runs = {},
  reasonText,
  reasonOpen,
  actions,
  onOpenResults,
  onOpenTraining,
  onOpenConfigure,
  onOpenReason,
  onRetry,
  onEnterL2,
  onEnterL1,
  onEnterL3,
}: Props) {
  const rootRef = useRef<HTMLDivElement>(null)
  const l1Ref = useRef<HTMLElement>(null)
  const l2Ref = useRef<HTMLElement>(null)
  const l3Refs = useRef<Record<string, HTMLElement | null>>({})
  const [paths, setPaths] = useState<Array<{ d: string; tone: FlowTone; key: string; dots: Point[] }>>([])

  const l1Tone: FlowTone = l1Ok ? "pass" : l1Busy ? "busy" : stages.length ? "fail" : "idle"
  const l1Status = l1Ok ? "已完成" : l1Busy ? "进行中" : stages.length ? "需要处理" : "未开始"
  const showL2 = phase !== "l1_only"
  const showRouting = phase === "l2_routing"
  const showL3 = showRouting && Boolean(routing && routing.branches.length > 0)

  const summaryByType = useMemo(() => {
    const map = new Map<string, DiagnosisRoutingSummary>()
    for (const item of summaries) map.set(item.equipment_type, item)
    return map
  }, [summaries])

  useLayoutEffect(() => {
    const root = rootRef.current
    if (!root) return

    const measure = () => {
      const rootBox = root.getBoundingClientRect()
      const next: Array<{ d: string; tone: FlowTone; key: string; dots: Point[] }> = []
      const l1 = l1Ref.current?.getBoundingClientRect()
      const l2 = l2Ref.current?.getBoundingClientRect()
      if (l1 && l2) {
        const from = {
          x: l1.right - rootBox.left,
          y: l1.top + l1.height / 2 - rootBox.top,
        }
        const to = {
          x: l2.left - rootBox.left,
          y: l2.top + l2.height / 2 - rootBox.top,
        }
        next.push({
          key: "l1-l2",
          tone: l2Main === "进行中" ? "busy" : l2Main === "已完成" ? "pass" : "idle",
          d: `M ${from.x} ${from.y} L ${to.x} ${to.y}`,
          dots: [from, to],
        })
      }
      if (l2 && showL3 && routing) {
        for (const branch of routing.branches) {
          const node = l3Refs.current[branch.group_key]?.getBoundingClientRect()
          if (!node) continue
          const from = {
            x: l2.right - rootBox.left,
            y: l2.top + l2.height / 2 - rootBox.top,
          }
          const to = {
            x: node.left - rootBox.left,
            y: node.top + node.height / 2 - rootBox.top,
          }
          next.push({
            key: `l2-l3-${branch.group_key}`,
            tone: "review",
            d: orthogonalPath(from, to),
            dots: [from, to],
          })
        }
      }
      setPaths(next)
    }

    measure()
    const observer = typeof ResizeObserver !== "undefined" ? new ResizeObserver(() => measure()) : null
    observer?.observe(root)
    window.addEventListener("resize", measure)
    return () => {
      observer?.disconnect()
      window.removeEventListener("resize", measure)
    }
  }, [phase, l2Main, showL3, routing])

  useEffect(() => {
    // 分支数量变化后重测一次连接线
    const timer = window.setTimeout(() => window.dispatchEvent(new Event("resize")), 40)
    return () => window.clearTimeout(timer)
  }, [routing?.branches.length, phase])

  return (
    <div className="dx-flow-canvas" ref={rootRef}>
      <svg className="dx-flow-svg" aria-hidden="true">
        {paths.map((path) => (
          <g key={path.key}>
            <path className={toneClass(path.tone)} d={path.d} />
            {path.dots.map((dot, index) => (
              <circle
                key={`${path.key}-${index}`}
                cx={dot.x}
                cy={dot.y}
                r={3}
                stroke={path.tone === "review" ? "#d4a45a" : path.tone === "busy" ? "#6f97c8" : "#9fcbb0"}
              />
            ))}
          </g>
        ))}
      </svg>

      <div className={`dx-flow-layout${phase === "l1_only" ? " is-l1-only" : ""}`}>
        <div className="dx-flow-main">
          <article ref={l1Ref} className={`dx-flow-node ${toneClass(l1Tone)}`} aria-label="L1 数据准备">
            <span className="dx-flow-kicker">L1 / DATA</span>
            <h4>数据准备</h4>
            <p className="dx-flow-status">{l1Status}</p>
            <p className="dx-flow-meta">接入 · 标准化 · 特征发布</p>
            <ul className="dx-flow-checklist" aria-label="L1 Pipeline P0–P8">
              {L1_STEPS.map((step) => {
                const done = stagePassed(stages, step.id)
                return (
                  <li key={step.id} className={done ? "is-done" : undefined}>
                    <span className="dx-flow-check" aria-hidden="true">
                      {done ? "✓" : ""}
                    </span>
                    <span>{step.label}</span>
                  </li>
                )
              })}
            </ul>
            {actions.showEnterL1 ? (
              <div className="dx-flow-actions">
                <button type="button" onClick={onEnterL1}>
                  进入 L1
                </button>
              </div>
            ) : null}
          </article>

          {showL2 ? (
            <div className="dx-flow-l2-col">
              <article ref={l2Ref} className={`dx-flow-node ${toneClass(l2Tone)}`} aria-label="L2 设备筛查">
                <span className="dx-flow-kicker">L2 / SCREENING</span>
                <h4>设备筛查</h4>
                <p className="dx-flow-status">{l2Main}</p>
                {multiHint ? <p className="dx-flow-meta">{multiHint}</p> : null}
                {progressText ? <p className="dx-flow-meta">诊断进度 {progressText}</p> : null}
                {showRouting && routing ? (
                  <div className="dx-flow-metrics" aria-label="L2 筛查摘要">
                    <div>
                      <small>已诊断</small>
                      <b>{routing.total.toLocaleString()}</b>
                    </div>
                    <div>
                      <small>筛查完成</small>
                      <b>{routing.l2Complete.toLocaleString()}</b>
                    </div>
                    <div>
                      <small>建议 L3</small>
                      <b>{routing.l3Review.toLocaleString()}</b>
                    </div>
                  </div>
                ) : null}
                {showRouting && routingLoading ? <p className="dx-flow-meta">正在汇总筛查结果…</p> : null}
                {showRouting && routing && !routingLoading ? (
                  routing.l3Review > 0 ? (
                    <p className="dx-flow-meta">
                      共 <strong>{routing.l3Review.toLocaleString()}</strong> 条建议 L3 复核（含初判异常、冲突、不确定）；初判正常{" "}
                      {routing.l2Complete.toLocaleString()} 条留在 L2。
                    </p>
                  ) : (
                    <p className="dx-flow-meta">全部初判正常，当前无数据进入 L3。</p>
                  )
                ) : null}
                {reasonOpen ? <p className="dx-flow-meta">{reasonText}</p> : null}
                <div className="dx-flow-actions">
                  {actions.showViewResults ? (
                    <button type="button" className="dx-primary" onClick={onOpenResults}>
                      查看结果
                    </button>
                  ) : null}
                  {actions.showConfigure ? (
                    <button type="button" className="dx-primary" onClick={onOpenConfigure}>
                      配置设备
                    </button>
                  ) : null}
                  {actions.showGoTrain ? (
                    <button type="button" className="dx-primary" onClick={onOpenTraining}>
                      去训练
                    </button>
                  ) : null}
                  {actions.showViewReason ? (
                    <button type="button" onClick={onOpenReason}>
                      查看原因
                    </button>
                  ) : null}
                  {actions.showRetry ? (
                    <button type="button" onClick={onRetry}>
                      重试
                    </button>
                  ) : null}
                  {actions.showEnterL2 ? (
                    <button type="button" onClick={onEnterL2}>
                      进入 L2
                    </button>
                  ) : null}
                </div>
              </article>
            </div>
          ) : null}
        </div>

        {showL3 && routing ? (
          <div className="dx-flow-l3-col" aria-label="L3 深度分析分支">
            {routing.branches.map((branch) => {
              const parent = summaryByType.get(branch.equipment_type)
              const analysis = parent ? l3Runs[parent.diagnosis_run_id] : null
              const statusLabel = !analysis
                ? "待入队"
                : analysis.status === "succeeded"
                  ? "分析完成"
                  : analysis.status === "partial"
                    ? "部分完成"
                    : analysis.status === "running"
                      ? "分析中"
                      : analysis.status === "failed"
                        ? "分析失败"
                        : "排队中"
              const statusColor =
                analysis?.status === "succeeded"
                  ? "#1f7a4d"
                  : analysis?.status === "failed"
                    ? "#b42318"
                    : "#b86a1c"
              return (
                <article
                  key={branch.group_key}
                  className="dx-flow-l3-card"
                  ref={(node) => {
                    l3Refs.current[branch.group_key] = node
                  }}
                >
                  <header>
                    <div>
                      <span className="dx-flow-kicker">L3 / ANALYSIS</span>
                      <h5>{branch.display_name}</h5>
                    </div>
                    <em className="dx-flow-status" style={{ color: statusColor }}>
                      {statusLabel}
                    </em>
                  </header>
                  <p className="dx-flow-meta">
                    建议 L3 复核：{branch.l3_review_count} 条
                    {branch.abnormal_count ? ` · ABNORMAL ${branch.abnormal_count}` : ""}
                    {branch.conflict_count ? ` · CONFLICT ${branch.conflict_count}` : ""}
                    {branch.uncertain_count ? ` · UNCERTAIN ${branch.uncertain_count}` : ""}
                  </p>
                  {analysis ? (
                    <p className="dx-flow-meta">
                      进度 {analysis.processed_record_count}/{analysis.selected_record_count}
                      {analysis.failed_record_count ? ` · 失败 ${analysis.failed_record_count}` : ""}
                      {analysis.inconclusive_record_count ? ` · 不可判定 ${analysis.inconclusive_record_count}` : ""}
                    </p>
                  ) : (
                    <p className="dx-flow-meta">等待 L3 AnalysisRun 自动入队…</p>
                  )}
                  <div className="dx-flow-actions">
                    <button type="button" className="dx-primary" onClick={() => onEnterL3(branch, parent)}>
                      进入 L3
                    </button>
                    <button type="button" onClick={() => onEnterL3(branch, parent)}>
                      查看证据
                    </button>
                  </div>
                </article>
              )
            })}
          </div>
        ) : null}
      </div>

      <p className="dx-flow-legend" aria-label="状态图例">
        <span className="is-pass">初判正常（留在 L2）</span>
        <span className="is-busy">进行中</span>
        <span className="is-review">异常 / 冲突 / 不确定 → L3</span>
        <span className="is-fail">运行失败</span>
      </p>
    </div>
  )
}
