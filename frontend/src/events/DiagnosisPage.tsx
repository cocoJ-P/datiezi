import { useLayoutEffect, useRef, type KeyboardEvent as ReactKeyboardEvent } from "react"
import { LayerPanel } from "../diagnosis/LayerPanels"
import { L1DatasetProvider } from "../diagnosis/l1/datasetContext"
import { TASK_STATUS_LABEL } from "../diagnosis/labels"
import { DiagnosticOverview } from "../diagnosis/Overview"
import { useDiagnosticStore } from "../diagnosis/store"
import { useRoute, type AppRoute } from "../routing"
import { eventById } from "./catalog"
import { DIAGNOSIS_GROUPS, DIAGNOSIS_TABS, diagnosisTabById, diagnosisTabId, type DiagnosisTab, type DiagnosisTabId } from "./diagnosisNav"
import "./events.css"
import "../diagnosis/workspace.css"

function revealTab(scroller: HTMLElement | null, tab: HTMLElement | null) {
  if (!scroller || !tab) return
  const tabRect = tab.getBoundingClientRect()
  const box = scroller.getBoundingClientRect()
  if (tabRect.left < box.left) scroller.scrollLeft -= box.left - tabRect.left + 12
  else if (tabRect.right > box.right) scroller.scrollLeft += tabRect.right - box.right + 12
}

export function DiagnosisPage() {
  const { route, navigate } = useRoute()
  const store = useDiagnosticStore()
  const active = diagnosisTabById(diagnosisTabId(route.tab))
  const scrollRef = useRef<HTMLDivElement>(null)
  const pendingFocus = useRef<DiagnosisTabId | null>(null)
  const explicit = route.runId ? store.runs.find((item) => item.id === route.runId) : undefined
  const missingId = route.runId && !explicit ? route.runId : undefined
  const linked =
    !route.runId && route.scope !== "global" && route.diagnosisId ? store.runs.find((item) => item.diagnosisId === route.diagnosisId) : undefined
  const run = explicit ?? linked
  const event = route.eventId ? eventById(route.eventId) : undefined
  const unrelated = !run && event && event.type !== "diagnosis"

  useLayoutEffect(() => {
    const moveFocus = pendingFocus.current
    pendingFocus.current = null
    const node = document.getElementById(`dx-tab-${active.id}`)
    if (moveFocus && node instanceof HTMLElement) node.focus()
    revealTab(scrollRef.current, node instanceof HTMLElement ? node : null)
  }, [active.id])

  function diagnosisRoute(patch: Partial<AppRoute>): AppRoute {
    return {
      page: "diagnosis",
      eventId: route.eventId,
      eventType: route.eventType,
      range: route.range,
      equipmentId: route.equipmentId,
      diagnosisId: route.diagnosisId,
      tab: route.tab,
      runId: route.runId ?? run?.id,
      engine: route.engine,
      scope: route.scope,
      ...patch,
    }
  }

  function openTab(id: DiagnosisTabId, moveFocus = false) {
    if (moveFocus) pendingFocus.current = id
    if (id === active.id) {
      if (moveFocus) document.getElementById(`dx-tab-${id}`)?.focus()
      return
    }
    navigate(diagnosisRoute({ tab: id === "overview" ? undefined : id }))
  }

  function onTabKey(keyEvent: ReactKeyboardEvent<HTMLButtonElement>) {
    const index = DIAGNOSIS_TABS.findIndex((item) => item.id === active.id)
    let next = -1
    if (keyEvent.key === "ArrowRight") next = (index + 1) % DIAGNOSIS_TABS.length
    else if (keyEvent.key === "ArrowLeft") next = (index - 1 + DIAGNOSIS_TABS.length) % DIAGNOSIS_TABS.length
    else if (keyEvent.key === "Home") next = 0
    else if (keyEvent.key === "End") next = DIAGNOSIS_TABS.length - 1
    else return
    keyEvent.preventDefault()
    openTab(DIAGNOSIS_TABS[next].id, true)
  }

  return (
    <L1DatasetProvider>
    <main className="diagnosis-page">
      <header className="dx-title">
        <div>
          <h1>智能诊断工作台</h1>
          <p>多引擎分析 · 物理证据验证 · 智能诊断</p>
        </div>
        <button
          type="button"
          className="ev-back"
          onClick={() => navigate({ page: "events", eventId: route.eventId ?? run?.eventId, eventType: event?.type === "diagnosis" || run?.eventId ? "diagnosis" : undefined })}
        >
          返回事件与日志
        </button>
      </header>
      <div className="dx-task" aria-label="诊断任务上下文">
        {missingId ? (
          <span className="dx-task-note">未找到运行任务 {missingId}。没有改用其他任务。</span>
        ) : run ? (
          <>
            <span>
              <b>{run.equipmentLabel}</b>
            </span>
            <span>
              任务 <span className="ev-tabular">{run.id}</span>
            </span>
            <span>{run.name}</span>
            <span>
              时间窗口 <span className="ev-tabular">{run.windowLabel}</span>
            </span>
            <span>数据 {run.dataMode === "imported" ? "导入" : run.dataMode === "live" ? "实时" : "模拟"}</span>
            <span>状态 {TASK_STATUS_LABEL[run.status]}</span>
            <span className="dx-demo">演示</span>
            <button
              type="button"
              className="dx-text"
              onClick={() =>
                navigate({
                  page: "diagnosis",
                  eventId: route.eventId,
                  eventType: route.eventType,
                  diagnosisId: route.diagnosisId,
                  scope: "global",
                })
              }
            >
              返回运行总览
            </button>
          </>
        ) : (
          <span className="dx-task-note">
            {unrelated ? `事件 ${event?.id} 不是诊断任务。当前仍是全局运行视图。` : "全局运行视图。未锁定单条诊断任务。"}
          </span>
        )}
      </div>
      <div className="dx-arch-scroll" ref={scrollRef}>
        <div className="dx-arch-row">
          <div className="dx-arch-tabs" role="tablist" aria-label="工作台架构" aria-orientation="horizontal">
          {DIAGNOSIS_GROUPS.map((group) => (
            <div className="dx-arch-group" key={group.group}>
              <p className="dx-arch-label" aria-hidden="true">
                {group.groupLabel}
              </p>
              <div className="dx-arch-items">
                {group.items.map((item) => {
                  const selected = item.id === active.id
                  return (
                    <button
                      key={item.id}
                      id={`dx-tab-${item.id}`}
                      type="button"
                      role="tab"
                      className="dx-tab"
                      aria-selected={selected}
                      aria-controls={`dx-panel-${item.id}`}
                      tabIndex={selected ? 0 : -1}
                      onClick={() => openTab(item.id)}
                      onKeyDown={onTabKey}
                    >
                      {"prefix" in item && item.prefix ? <span className="dx-prefix">{item.prefix}</span> : null}
                      {item.label}
                    </button>
                  )
                })}
              </div>
            </div>
          ))}
          </div>
        </div>
      </div>
      <section className="dx-panel" hidden={active.id !== "overview"} role={active.id === "overview" ? "tabpanel" : undefined} id="dx-panel-overview" aria-labelledby="dx-tab-overview" tabIndex={active.id === "overview" ? 0 : -1}>
        <PanelHead tab={diagnosisTabById("overview")} />
        <DiagnosticOverview onOpenLayer={(id) => openTab(id)} />
      </section>
      {active.id !== "overview" ? (
        <section className={active.id === "l1" ? "dx-panel is-flush" : "dx-panel"} role="tabpanel" id={`dx-panel-${active.id}`} aria-labelledby={`dx-tab-${active.id}`} tabIndex={0}>
          {active.id === "l1" ? null : <PanelHead tab={active} />}
          <div className="dx-workspace">
            <LayerPanel tab={active.id} run={run} missingId={missingId} engineId={route.engine} />
          </div>
        </section>
      ) : null}
    </main>
    </L1DatasetProvider>
  )
}

function PanelHead({ tab }: { tab: DiagnosisTab }) {
  return (
    <header className="dx-section-head">
      <p className="dx-crumb">{tab.crumb}</p>
      <h2>{tab.title}</h2>
      <p className="dx-summary">{tab.summary}</p>
    </header>
  )
}
