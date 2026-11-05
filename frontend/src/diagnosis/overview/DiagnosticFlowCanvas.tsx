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

export type DownstreamStageStats = {
  statusLabel: string
  tone: FlowTone
  analyzedRecords: number
  candidateCount: number
  reasoningDone: number
  insufficientOrConflict: number
  l4RunId?: string | null
  knowledgeNote?: string
}

export type L5StageStats = {
  statusLabel: string
  tone: FlowTone
  modelId: string
  equipmentCategories: string[]
  reportCount: number
  reportRecords: number
  synthesisCompleted: number
  predictionCoverageNote: string
  knowledgeNote: string
  primaryL5RunId?: string | null
  primaryRecordId?: string | null
}

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
  l4StatsByBranch?: Record<string, DownstreamStageStats>
  l5Stats?: L5StageStats | null
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
  onEnterL4: (branch?: RoutingGroupSummary, summary?: DiagnosisRoutingSummary) => void
  onEnterL5: () => void
  onOpenL5Report: () => void
}

function toneClass(tone: FlowTone): string {
  return `is-${tone}`
}

function orthogonalPath(from: Point, to: Point): string {
  const midX = from.x + (to.x - from.x) * 0.55
  return `M ${from.x} ${from.y} L ${midX} ${from.y} L ${midX} ${to.y} L ${to.x} ${to.y}`
}

function convergePath(from: Point, to: Point): string {
  const midX = from.x + (to.x - from.x) * 0.45
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
  l4StatsByBranch = {},
  l5Stats = null,
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
  onEnterL4,
  onEnterL5,
  onOpenL5Report,
}: Props) {
  const rootRef = useRef<HTMLDivElement>(null)
  const l1Ref = useRef<HTMLElement>(null)
  const l2Ref = useRef<HTMLElement>(null)
  const l5Ref = useRef<HTMLElement>(null)
  const l3Refs = useRef<Record<string, HTMLElement | null>>({})
  const l4Refs = useRef<Record<string, HTMLElement | null>>({})
  const [paths, setPaths] = useState<Array<{ d: string; tone: FlowTone; key: string; dots: Point[] }>>([])

  const l1Tone: FlowTone = l1Ok ? "pass" : l1Busy ? "busy" : stages.length ? "fail" : "idle"
  const l1Status = l1Ok ? "已完成" : l1Busy ? "进行中" : stages.length ? "需要处理" : "未开始"
  const showL2 = phase !== "l1_only"
  const showRouting = phase === "l2_routing"
  const showL3 = showRouting && Boolean(routing && routing.branches.length > 0)
  const branchKeys = routing?.branches.map((b) => b.group_key).join("|") || ""
  const l4ToneKey = Object.entries(l4StatsByBranch)
    .map(([k, v]) => `${k}:${v.tone}`)
    .sort()
    .join("|")

  const summaryByType = useMemo(() => {
    const map = new Map<string, DiagnosisRoutingSummary>()
    for (const item of summaries) map.set(item.equipment_type, item)
    return map
  }, [summaries])

  const l3CompleteByBranch = useMemo(() => {
    const map: Record<string, boolean> = {}
    if (!routing?.branches.length) return map
    for (const branch of routing.branches) {
      const parent = summaryByType.get(branch.equipment_type)
      const analysis = parent ? l3Runs[parent.diagnosis_run_id] : null
      map[branch.group_key] =
        analysis?.status === "succeeded" || analysis?.status === "partial"
    }
    return map
  }, [routing, summaryByType, l3Runs])

  // 仅作 effect 依赖：按分支顺序记录完成位，避免 group_key 含 |/: 时解析错乱
  const l3CompleteKey = useMemo(
    () => (routing?.branches || []).map((b) => (l3CompleteByBranch[b.group_key] ? "1" : "0")).join(""),
    [routing?.branches, l3CompleteByBranch],
  )

  const l4VisibleKeys = useMemo(
    () => (routing?.branches || []).filter((b) => l3CompleteByBranch[b.group_key]).map((b) => b.group_key),
    [routing?.branches, l3CompleteByBranch],
  )

  const showL5 = useMemo(() => {
    if (!showL3 || !routing?.branches.length) return false
    if (!l4VisibleKeys.length) return false
    return l4VisibleKeys.every((key) => {
      const stats = l4StatsByBranch[key]
      return Boolean(stats?.l4RunId) && stats?.tone === "pass"
    })
  }, [showL3, routing, l4VisibleKeys, l4StatsByBranch])

  const l4ToneByBranchRef = useRef(l4StatsByBranch)
  l4ToneByBranchRef.current = l4StatsByBranch
  const l5ToneRef = useRef(l5Stats?.tone)
  l5ToneRef.current = l5Stats?.tone
  const l3CompleteRef = useRef(l3CompleteByBranch)
  l3CompleteRef.current = l3CompleteByBranch
  const showL5Ref = useRef(showL5)
  showL5Ref.current = showL5

  useLayoutEffect(() => {
    const root = rootRef.current
    if (!root) return
    let raf = 0

    const pathsEqual = (
      a: Array<{ d: string; tone: FlowTone; key: string }>,
      b: Array<{ d: string; tone: FlowTone; key: string }>,
    ) =>
      a.length === b.length &&
      a.every((item, index) => item.key === b[index].key && item.d === b[index].d && item.tone === b[index].tone)

    const measure = () => {
      const rootBox = root.getBoundingClientRect()
      const next: Array<{ d: string; tone: FlowTone; key: string; dots: Point[] }> = []
      const l1 = l1Ref.current?.getBoundingClientRect()
      const l2 = l2Ref.current?.getBoundingClientRect()
      const branchStats = l4ToneByBranchRef.current
      const l5Tone = l5ToneRef.current
      const completeMap = l3CompleteRef.current
      const drawL5 = showL5Ref.current
      if (l1 && l2) {
        const from = {
          x: Math.round(l1.right - rootBox.left),
          y: Math.round(l1.top + l1.height / 2 - rootBox.top),
        }
        const to = {
          x: Math.round(l2.left - rootBox.left),
          y: Math.round(l2.top + l2.height / 2 - rootBox.top),
        }
        next.push({
          key: "l1-l2",
          tone: l2Main === "进行中" ? "busy" : l2Main === "已完成" ? "pass" : "idle",
          d: `M ${from.x} ${from.y} L ${to.x} ${to.y}`,
          dots: [from, to],
        })
      }

      const l5 = drawL5 ? l5Ref.current?.getBoundingClientRect() : null

      if (l2 && showL3 && routing) {
        for (const branch of routing.branches) {
          const l3Node = l3Refs.current[branch.group_key]?.getBoundingClientRect()
          const showL4 = Boolean(completeMap[branch.group_key])
          const l4Node = showL4 ? l4Refs.current[branch.group_key]?.getBoundingClientRect() : null
          const l4Tone = branchStats[branch.group_key]?.tone || "idle"

          if (l3Node) {
            const from = {
              x: Math.round(l2.right - rootBox.left),
              y: Math.round(l2.top + l2.height / 2 - rootBox.top),
            }
            const to = {
              x: Math.round(l3Node.left - rootBox.left),
              y: Math.round(l3Node.top + l3Node.height / 2 - rootBox.top),
            }
            next.push({
              key: `l2-l3-${branch.group_key}`,
              tone: "review",
              d: orthogonalPath(from, to),
              dots: [from, to],
            })
          }

          if (l3Node && l4Node) {
            const from = {
              x: Math.round(l3Node.right - rootBox.left),
              y: Math.round(l3Node.top + l3Node.height / 2 - rootBox.top),
            }
            const to = {
              x: Math.round(l4Node.left - rootBox.left),
              y: Math.round(l4Node.top + l4Node.height / 2 - rootBox.top),
            }
            next.push({
              key: `l3-l4-${branch.group_key}`,
              tone: l4Tone === "pass" ? "pass" : "idle",
              d: `M ${from.x} ${from.y} L ${to.x} ${to.y}`,
              dots: [from, to],
            })
          }

          if (l4Node && l5) {
            const from = {
              x: Math.round(l4Node.right - rootBox.left),
              y: Math.round(l4Node.top + l4Node.height / 2 - rootBox.top),
            }
            const to = {
              x: Math.round(l5.left - rootBox.left),
              y: Math.round(l5.top + l5.height / 2 - rootBox.top),
            }
            next.push({
              key: `l4-l5-${branch.group_key}`,
              tone: l5Tone === "pass" ? "pass" : "idle",
              d: convergePath(from, to),
              dots: [from, to],
            })
          }
        }
      }

      // 路径未变时跳过 setState，避免 ResizeObserver → setPaths → 重排死循环
      setPaths((prev) => (pathsEqual(prev, next) ? prev : next))
    }

    const scheduleMeasure = () => {
      cancelAnimationFrame(raf)
      raf = requestAnimationFrame(measure)
    }

    measure()
    const observer = typeof ResizeObserver !== "undefined" ? new ResizeObserver(scheduleMeasure) : null
    observer?.observe(root)
    window.addEventListener("resize", scheduleMeasure)
    return () => {
      cancelAnimationFrame(raf)
      observer?.disconnect()
      window.removeEventListener("resize", scheduleMeasure)
    }
  }, [phase, l2Main, showL3, showL5, branchKeys, l3CompleteKey, l4ToneKey, l5Stats?.tone, routing?.branches])

  useEffect(() => {
    const timer = window.setTimeout(() => window.dispatchEvent(new Event("resize")), 40)
    return () => window.clearTimeout(timer)
  }, [branchKeys, phase, l3CompleteKey, l4ToneKey, showL5])

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
          <div className="dx-flow-branch-rail" aria-label="L3→L4 设备分支">
            {routing.branches.map((branch, index) => {
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
              const l4 = l4StatsByBranch[branch.group_key]
              const l4Tone = l4?.tone || "idle"
              const showL4 = Boolean(l3CompleteByBranch[branch.group_key])
              if (!showL4) l4Refs.current[branch.group_key] = null
              return (
                <div
                  key={branch.group_key}
                  className="dx-flow-branch-row"
                  style={{ animationDelay: `${index * 60}ms` }}
                >
                  <article
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

                  {showL4 ? (
                    <article
                      className={`dx-flow-l4-card ${toneClass(l4Tone)}`}
                      ref={(node) => {
                        l4Refs.current[branch.group_key] = node
                      }}
                      aria-label={`${branch.display_name} L4 根因`}
                    >
                      <header>
                        <div>
                          <span className="dx-flow-kicker">L4 / ROOT CAUSE</span>
                          <h5>根因推理</h5>
                        </div>
                        <em className={`dx-flow-status ${toneClass(l4Tone)}`}>{l4?.statusLabel || "未运行"}</em>
                      </header>
                      <div className="dx-flow-metrics" aria-label="L4 摘要">
                        <div>
                          <small>已分析</small>
                          <b>{l4?.analyzedRecords ?? 0}</b>
                        </div>
                        <div>
                          <small>根因候选</small>
                          <b>{l4?.candidateCount ?? 0}</b>
                        </div>
                        <div>
                          <small>推理</small>
                          <b>{l4?.reasoningDone ?? 0}</b>
                        </div>
                      </div>
                      <p className="dx-flow-meta">
                        证据不足/冲突 {l4?.insufficientOrConflict ?? 0}
                        {l4?.l4RunId ? ` · ${l4.l4RunId}` : ""}
                      </p>
                      <p className="dx-flow-meta">{l4?.knowledgeNote || "对应本设备 L3；完成后可进入 L4"}</p>
                      <div className="dx-flow-actions">
                        <button type="button" className="dx-primary" onClick={() => onEnterL4(branch, parent)}>
                          进入 L4
                        </button>
                        <button type="button" onClick={() => onEnterL4(branch, parent)}>
                          查看证据
                        </button>
                      </div>
                    </article>
                  ) : null}
                </div>
              )
            })}
          </div>
        ) : null}

        {showL5 ? (
          <div className="dx-flow-l5-col" aria-label="L5 综合诊断">
            <article ref={l5Ref} className={`dx-flow-node ${toneClass(l5Stats?.tone || "idle")}`} aria-label="L5 综合诊断">
              <span className="dx-flow-kicker">L5 / DIAGNOSIS</span>
              <h4>综合诊断</h4>
              <p className="dx-flow-status">{l5Stats?.statusLabel || "未运行"}</p>
              <p className="dx-flow-meta">模型 {l5Stats?.modelId || "L5P-v1"}</p>
              <div className="dx-flow-metrics" aria-label="L5 摘要">
                <div>
                  <small>报告数</small>
                  <b>{l5Stats?.reportCount ?? 0}</b>
                </div>
                <div>
                  <small>十字段记录</small>
                  <b>{l5Stats?.reportRecords ?? 0}</b>
                </div>
                <div>
                  <small>Synthesis</small>
                  <b>{l5Stats?.synthesisCompleted ?? 0}</b>
                </div>
              </div>
              <p className="dx-flow-meta">{l5Stats?.predictionCoverageNote || "六类设备预测头已加载时可独立推理"}</p>
              <p className="dx-flow-meta">{l5Stats?.knowledgeNote || "各设备 L4 完成后在此收敛为综合诊断"}</p>
              <div className="dx-flow-actions">
                <button type="button" className="dx-primary" onClick={onEnterL5}>
                  进入综合诊断
                </button>
                <button type="button" onClick={onOpenL5Report}>
                  查看最终报告
                </button>
              </div>
            </article>
          </div>
        ) : null}
      </div>

      <p className="dx-flow-legend" aria-label="状态图例">
        <span className="is-pass">初判正常 / L5 可用</span>
        <span className="is-busy">进行中</span>
        <span className="is-review">异常 / 冲突 / 不确定 → L3</span>
        <span className="is-fail">运行失败</span>
        <span className="is-idle">未运行 / 知识增强未启动</span>
      </p>
    </div>
  )
}
