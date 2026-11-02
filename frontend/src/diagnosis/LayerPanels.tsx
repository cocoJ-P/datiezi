import { eventById } from "../events/catalog"
import type { DiagnosisTabId } from "../events/diagnosisNav"
import { PipelinePanel } from "./l1/PipelinePanel"
import { AVAILABILITY_LABEL, EXECUTION_LABEL, SOURCE_LABEL, STAGE_LABEL, STAGE_STATE_LABEL, TASK_STATUS_LABEL, formatStamp, strategyLabel } from "./labels"
import { advanceDemo, engineBoard, markReportHandoff, useDiagnosticStore } from "./store"
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
  if (missingId) {
    return <p className="dx-empty">未找到运行任务 {missingId}。没有改用其他任务。</p>
  }
  if (!run) {
    return <p className="dx-empty">这一层需要先从运行总览选定一条诊断任务。选定后切换页签不会重新计算。</p>
  }
  if (tab === "l2") return <ScheduleLayer run={run} />
  if (tab === "l3") return <EngineLayer run={run} engineId={engineId} />
  if (tab === "l4") return <EvidenceLayer run={run} />
  if (tab === "l5") return <FeedbackLayer run={run} />
  if (tab === "harness") return <HarnessLayer run={run} />
  return <StrategyLayer run={run} />
}

function RunMeta({ run }: { run: DiagnosticRun }) {
  return (
    <p className="dx-run-meta">
      <span className="dx-demo">Demo</span>
      {run.id} · {SOURCE_LABEL[run.source]} · {run.equipmentLabel} · {TASK_STATUS_LABEL[run.status]} · {run.dataMode === "imported" ? "导入" : run.dataMode === "live" ? "实时" : "模拟"}
    </p>
  )
}

function ScheduleLayer({ run }: { run: DiagnosticRun }) {
  const stage = run.stages.find((item) => item.stage === "l2")
  return (
    <div className="dx-layer">
      <RunMeta run={run} />
      <p>策略 {strategyLabel(run.strategyId)}</p>
      <p>
        调度步骤：{stage ? STAGE_STATE_LABEL[stage.state] : "未开始"}。{stage?.note}
      </p>
      <p>{run.cycleNote}</p>
    </div>
  )
}

function EngineLayer({ run, engineId }: { run: DiagnosticRun; engineId?: string }) {
  const engines = engineBoard(useDiagnosticStore().runs)
  return (
    <div className="dx-layer">
      <RunMeta run={run} />
      <ul className="dx-engines">
        {engines.map((engine) => (
          <li key={engine.engineId} className={engine.engineId === engineId ? "is-current" : undefined}>
            <div className="dx-engine dx-engine-static">
              <span>
                <strong>{engine.shortName}</strong>
                <small>{engine.name}</small>
              </span>
              <span>
                <small>能力</small>
                <b>{AVAILABILITY_LABEL[engine.availability]}</b>
              </span>
              <span>
                <small>执行</small>
                <b>{EXECUTION_LABEL[engine.execution]}</b>
              </span>
            </div>
            <p>{engine.lastResult}。本任务阶段 {run.currentStage ? STAGE_LABEL[run.currentStage] : "未开始"}。</p>
          </li>
        ))}
      </ul>
    </div>
  )
}

function EvidenceLayer({ run }: { run: DiagnosticRun }) {
  const event = run.eventId ? eventById(run.eventId) : undefined
  return (
    <div className="dx-layer">
      <RunMeta run={run} />
      <p>这里保留假设、支持证据和反证。演示任务没有已确认故障。</p>
      {event?.diagnosis ? (
        <>
          <h3>事件日志中的未确认推断</h3>
          <ul>
            {event.diagnosis.candidates.map((item) => (
              <li key={item}>{item}（未确认）</li>
            ))}
          </ul>
          <ul>
            {event.diagnosis.evidence.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
          <p>{event.diagnosis.modelNote}</p>
        </>
      ) : (
        <p>这条任务还没有可核对的证据条目。</p>
      )}
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
