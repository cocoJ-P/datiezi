import { useState } from "react"
import type { DiagnosisTabId } from "../events/diagnosisNav"
import { PipelinePanel } from "./l1/PipelinePanel"
import { L2Workspace } from "./l2/L2Workspace"
import { L3Workbench } from "./l3/L3Workbench"
import { L4Workbench } from "./l4/L4Workbench"
import { SOURCE_LABEL, STAGE_LABEL, STAGE_STATE_LABEL, TASK_STATUS_LABEL, formatStamp, strategyLabel } from "./labels"
import { consumeL3HandoffFocus } from "./overview/l3HandoffFocus"
import { advanceDemo, markReportHandoff } from "./store"
import type { DiagnosticRun } from "./types"

export function LayerPanel({
  tab,
  run,
  missingId,
  engineId,
}: {
  tab: Exclude<DiagnosisTabId, "overview">
  run?: DiagnosticRun
  missingId?: string
  engineId?: string
}) {
  if (tab === "l1") return <PipelinePanel />
  if (tab === "l2") return <L2Workspace initialView={engineId} />
  if (tab === "l3") return <EngineLayer run={run} engineId={engineId} />
  if (missingId) {
    return <p className="dx-empty">未找到运行任务 {missingId}。没有改用其他任务。</p>
  }
  if (!run) {
    return <p className="dx-empty">这一层需要先从运行总览选定一条诊断任务。选定后切换页签不会重新计算。</p>
  }
  if (tab === "l4") return <L4Layer run={run} />
  if (tab === "l5") return <FeedbackLayer run={run} />
  if (tab === "harness") return <HarnessLayer run={run} />
  return <StrategyLayer run={run} />
}

function L4Layer({ run }: { run?: DiagnosticRun }) {
  return (
    <div className="dx-layer">
      {run ? <RunMeta run={run} /> : null}
      <L4Workbench />
    </div>
  )
}

function RunMeta({ run }: { run: DiagnosticRun }) {
  return (
    <p className="dx-run-meta">
      <span className="dx-demo">Demo</span>
      {run.id} · {SOURCE_LABEL[run.source]} · {run.equipmentLabel} · {TASK_STATUS_LABEL[run.status]} · {run.dataMode === "imported" ? "导入" : run.dataMode === "live" ? "实时" : "模拟"}
    </p>
  )
}

function EngineLayer({ run }: { run?: DiagnosticRun; engineId?: string }) {
  const [handoff] = useState(() => consumeL3HandoffFocus())
  return (
    <div className="dx-layer">
      {run ? <RunMeta run={run} /> : null}
      <L3Workbench handoff={handoff} />
    </div>
  )
}

function FeedbackLayer({ run }: { run: DiagnosticRun }) {
  return (
    <div className="dx-layer">
      <RunMeta run={run} />
      <p>L5 记录复核、知识回写和交接状态。正式报告的编辑、版本和导出属于报告中心。</p>
      <p>交接：{run.reportHandoff === "pending" ? "已标记待送报告中心" : "尚未送出"}</p>
      <button type="button" className="ev-back" onClick={() => markReportHandoff(run.id)}>
        标记待送报告中心
      </button>
    </div>
  )
}

function HarnessLayer({ run }: { run: DiagnosticRun }) {
  return (
    <div className="dx-layer">
      <RunMeta run={run} />
      <p>
        创建 {formatStamp(run.createdAt)}
        {run.startedAt ? ` · 开始 ${formatStamp(run.startedAt)}` : ""}
        {run.completedAt ? ` · 结束 ${formatStamp(run.completedAt)}` : ""}
      </p>
      <ol className="dx-trace">
        {run.stages.map((stage) => (
          <li key={stage.stage}>
            <strong>{STAGE_LABEL[stage.stage]}</strong>
            <span>{STAGE_STATE_LABEL[stage.state]}</span>
            <span>{stage.note}</span>
          </li>
        ))}
      </ol>
      <button type="button" className="ev-back" onClick={() => advanceDemo(run.id)}>
        演示推进
      </button>
      <p>推进只改变前端演示状态，不代表完成了工业诊断。</p>
    </div>
  )
}

function StrategyLayer({ run }: { run: DiagnosticRun }) {
  return (
    <div className="dx-layer">
      <RunMeta run={run} />
      <p>当前策略 {strategyLabel(run.strategyId)}。Jev、Bandit 和强化学习还没有实验记录，也没有训练结果。</p>
      <p>策略页只查看这条任务的调度选择，不在切换页签时重新计算。</p>
    </div>
  )
}
