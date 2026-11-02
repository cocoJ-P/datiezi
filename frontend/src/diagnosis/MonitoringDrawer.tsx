import { useState } from "react"
import { DEMO_LINE } from "../overview/demoData"
import { FAILURE_LABEL, STRATEGIES, TRIGGER_LABEL, formatStamp, strategyLabel } from "./labels"
import { savePlan, useDiagnosticStore } from "./store"
import type { FailurePolicy, MonitoringPlan, TriggerType } from "./types"

interface PlanDraft {
  id: string
  equipmentId: string
  enabled: boolean
  dataSource: string
  triggerType: TriggerType
  periodMinutes: number
  anomalyRule: string
  strategyId: string
  failurePolicy: FailurePolicy
}

const EMPTY: PlanDraft = {
  id: "",
  equipmentId: DEMO_LINE.equipment[0]?.id ?? "",
  enabled: false,
  dataSource: "模拟快照，未连接采集服务",
  triggerType: "new_data",
  periodMinutes: 30,
  anomalyRule: "",
  strategyId: STRATEGIES[0].id,
  failurePolicy: "record",
}

export function MonitoringDrawer({ onClose }: { onClose: () => void }) {
  const { plans, runs } = useDiagnosticStore()
  const [draft, setDraft] = useState(EMPTY)
  const [error, setError] = useState("")

  function edit(plan: MonitoringPlan) {
    setError("")
    setDraft({
      id: plan.id,
      equipmentId: plan.equipmentId,
      enabled: plan.enabled,
      dataSource: plan.dataSource,
      triggerType: plan.triggerType,
      periodMinutes: plan.periodMinutes ?? 30,
      anomalyRule: plan.anomalyRule ?? "",
      strategyId: plan.strategyId,
      failurePolicy: plan.failurePolicy,
    })
  }

  function save() {
    if (!draft.equipmentId) {
      setError("请选择监测设备。")
      return
    }
    if (!draft.dataSource.trim()) {
      setError("请填写数据源说明。")
      return
    }
    if (draft.triggerType === "periodic" && (!Number.isFinite(draft.periodMinutes) || draft.periodMinutes <= 0)) {
      setError("周期触发需要大于 0 的分钟数。")
      return
    }
    if (draft.triggerType === "anomaly" && !draft.anomalyRule.trim()) {
      setError("异常事件触发需要填写条件。")
      return
    }
    savePlan({
      id: draft.id || undefined,
      equipmentId: draft.equipmentId,
      enabled: draft.enabled,
      dataSource: draft.dataSource,
      triggerType: draft.triggerType,
      periodMinutes: draft.periodMinutes,
      anomalyRule: draft.anomalyRule,
      strategyId: draft.strategyId,
      failurePolicy: draft.failurePolicy,
    })
    setDraft(EMPTY)
    setError("")
  }

  return (
    <div className="dx-modal-back" role="presentation" onMouseDown={onClose}>
      <aside className="dx-drawer" role="dialog" aria-modal="true" aria-label="自动监测配置" onMouseDown={(event) => event.stopPropagation()}>
        <header className="dx-modal-head">
          <div>
            <p>演示配置</p>
            <h2>自动监测</h2>
          </div>
          <button type="button" className="ev-back" onClick={onClose}>
            关闭
          </button>
        </header>
        <p className="dx-demo-note">开关只改变这份前端配置。没有接入调度后端，不会启动真实的持续监测服务。</p>
        <ul className="dx-plan-list">
          {plans.map((plan) => {
            const history = runs.filter((item) => item.monitoringPlanId === plan.id)
            return (
              <li key={plan.id}>
                <button type="button" onClick={() => edit(plan)}>
                  <strong>{plan.equipmentLabel}</strong>
                  <span>{TRIGGER_LABEL[plan.triggerType]}</span>
                  <span>{plan.enabled ? "配置为启用" : "配置为停用"}</span>
                  <span>最近检查 {plan.lastRunAt ? formatStamp(plan.lastRunAt) : "无"}</span>
                  <span>策略 {strategyLabel(plan.strategyId)}</span>
                  <span>关联任务 {history.length} 条</span>
                </button>
              </li>
            )
          })}
        </ul>
        <form
          className="dx-form"
          onSubmit={(event) => {
            event.preventDefault()
            save()
          }}
        >
          <h3>{draft.id ? `编辑 ${draft.id}` : "新增监测配置"}</h3>
          <label>
            监测设备
            <select value={draft.equipmentId} onChange={(event) => setDraft({ ...draft, equipmentId: event.target.value })}>
              {DEMO_LINE.equipment.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name} {item.code}
                </option>
              ))}
            </select>
          </label>
          <label>
            数据源说明
            <input value={draft.dataSource} onChange={(event) => setDraft({ ...draft, dataSource: event.target.value })} />
          </label>
          <label className="dx-check">
            <input type="checkbox" checked={draft.enabled} onChange={(event) => setDraft({ ...draft, enabled: event.target.checked })} />
            在演示配置中标记为启用
          </label>
          <label>
            触发方式
            <select value={draft.triggerType} onChange={(event) => setDraft({ ...draft, triggerType: event.target.value as TriggerType })}>
              <option value="periodic">周期触发</option>
              <option value="new_data">新数据触发</option>
              <option value="anomaly">异常事件触发</option>
            </select>
          </label>
          {draft.triggerType === "periodic" ? (
            <label>
              周期（分钟）
              <input type="number" min={1} value={draft.periodMinutes} onChange={(event) => setDraft({ ...draft, periodMinutes: Number(event.target.value) })} />
            </label>
          ) : null}
          {draft.triggerType === "anomaly" ? (
            <label>
              异常条件
              <input value={draft.anomalyRule} onChange={(event) => setDraft({ ...draft, anomalyRule: event.target.value })} />
            </label>
          ) : null}
          <label>
            诊断策略
            <select value={draft.strategyId} onChange={(event) => setDraft({ ...draft, strategyId: event.target.value })}>
              {STRATEGIES.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            失败时
            <select value={draft.failurePolicy} onChange={(event) => setDraft({ ...draft, failurePolicy: event.target.value as FailurePolicy })}>
              {Object.entries(FAILURE_LABEL).map(([id, label]) => (
                <option key={id} value={id}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          {error ? <p className="is-alert">{error}</p> : null}
          <button type="submit" className="dx-primary">
            保存演示配置
          </button>
        </form>
      </aside>
    </div>
  )
}
