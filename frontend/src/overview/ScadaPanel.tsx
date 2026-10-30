import { useEffect, useState } from "react"
import { TREND_RANGES } from "./demoData"
import { equipmentProfile } from "./equipmentProfile"
import { activeAlarms, activeMetrics, equipmentOf, headline, type AlarmEvent, type LineSnapshot, type MetricPoint, type ViewSelection } from "./model"

function TrendChart({ metric, count }: { metric: MetricPoint; count: number }) {
  const series = metric.series.slice(-count)
  const width = 360
  const height = 112
  const pad = 16
  const min = Math.min(...series)
  const max = Math.max(...series)
  const span = max - min || 1
  const points = series
    .map((value, index) => {
      const x = pad + (index / Math.max(series.length - 1, 1)) * (width - pad * 2)
      const y = pad + (1 - (value - min) / span) * (height - pad * 2)
      return `${x},${y}`
    })
    .join(" ")
  return (
    <svg className="ov-trend" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${metric.label}趋势`}>
      <line x1={pad} y1={height - pad} x2={width - pad} y2={height - pad} className="ov-trend-axis" />
      <polyline points={points} className="ov-trend-line" />
    </svg>
  )
}

function statusText(status: "normal" | "warn" | "mixed"): string {
  if (status === "warn") {
    return "警告"
  }
  if (status === "mixed") {
    return "有待关注项"
  }
  return "正常"
}

export function ScadaPanel({
  line,
  selection,
  onAlarm,
}: {
  line: LineSnapshot
  selection: ViewSelection
  onAlarm: (equipmentId: string) => void
}) {
  const metrics = activeMetrics(line, selection)
  const trendMetrics = metrics.filter((item) => Number.isFinite(Number(item.value)))
  const alarms = activeAlarms(line, selection)
  const head = headline(line, selection)
  const [metricId, setMetricId] = useState(trendMetrics[0]?.id ?? "")
  const [rangeId, setRangeId] = useState<(typeof TREND_RANGES)[number]["id"]>("1h")
  const selectionKey = selection.type === "overview" ? "overview" : selection.type === "system" ? selection.id : selection.id

  useEffect(() => {
    const next = activeMetrics(line, selection).find((item) => Number.isFinite(Number(item.value)))
    setMetricId(next?.id ?? "")
  }, [selectionKey, line, selection])

  const metric = trendMetrics.find((item) => item.id === metricId) ?? trendMetrics[0]
  const range = TREND_RANGES.find((item) => item.id === rangeId) ?? TREND_RANGES[1]
  const equipment = selection.type === "equipment" ? equipmentOf(line, selection.id) : undefined
  const profile = equipment ? equipmentProfile(equipment.id) : undefined

  return (
    <aside className="ov-scada" aria-label="运行数据">
      <header className="ov-scada-head">
        <p>当前对象</p>
        <h2>{profile?.name ?? head.title}</h2>
        <div className="ov-scada-meta">
          <span>{head.code}</span>
          <span className={head.status === "normal" ? "ov-ok" : "ov-warn"}>{statusText(head.status)}</span>
          <span className="ov-tabular">{line.updatedAt}</span>
        </div>
        {equipment ? <p className="ov-scada-note">左侧已定位到该设备。</p> : null}
        {selection.type === "overview" ? <p className="ov-scada-note">{line.condition}</p> : null}
      </header>

      {profile ? (
        <section aria-label="设备基本信息">
          <h3>设备基本信息</h3>
          <dl className="ov-spec">
            <div>
              <dt>设备名称</dt>
              <dd>{profile.name}</dd>
            </div>
            <div>
              <dt>设备类型</dt>
              <dd>{profile.type}</dd>
            </div>
            <div>
              <dt>典型型号</dt>
              <dd>{profile.models}</dd>
            </div>
            <div>
              <dt>额定功率</dt>
              <dd>{profile.power}</dd>
            </div>
            <div>
              <dt>额定转速</dt>
              <dd>{profile.speed}</dd>
            </div>
          </dl>
        </section>
      ) : null}

      <section className="ov-scada-status" aria-label="运行状态">
        <div>
          <span>运行状态</span>
          <strong className={head.status === "normal" ? "ov-ok" : "ov-warn"}>{statusText(head.status)}</strong>
        </div>
        <div>
          <span>当前报警</span>
          <strong className="ov-tabular">{alarms.length}</strong>
        </div>
        <div>
          <span>当前工况</span>
          <strong>模拟</strong>
        </div>
      </section>

      <section>
        <h3>实时参数</h3>
        <ul className="ov-metrics">
          {metrics.map((item) => (
            <li key={item.id}>
              <span>{item.label}</span>
              <strong className="ov-tabular">
                {item.value}
                {item.unit ? <small>{item.unit}</small> : null}
              </strong>
              <em>{item.direction === "up" ? "上升" : item.direction === "down" ? "下降" : "平稳"}</em>
            </li>
          ))}
        </ul>
      </section>

      {metric ? (
        <section>
          <div className="ov-trend-bar">
            <h3>运行趋势</h3>
            <div>
              {trendMetrics.map((item) => (
                <button key={item.id} type="button" aria-pressed={item.id === metric.id} onClick={() => setMetricId(item.id)}>
                  {item.label}
                </button>
              ))}
            </div>
          </div>
          <div className="ov-ranges">
            {TREND_RANGES.map((item) => (
              <button key={item.id} type="button" aria-pressed={item.id === range.id} onClick={() => setRangeId(item.id)}>
                {item.label}
              </button>
            ))}
          </div>
          <TrendChart metric={metric} count={range.count} />
          <p className="ov-scada-note">趋势由模拟序列生成，不是现场历史曲线。</p>
        </section>
      ) : null}

      <section>
        <h3>报警与诊断</h3>
        {alarms.length === 0 ? <p className="ov-scada-note">当前范围内没有报警。</p> : null}
        <ul className="ov-alarms">
          {alarms.map((alarm) => (
            <li key={alarm.id}>
              <button type="button" onClick={() => onAlarm(alarm.equipmentId)}>
                <AlarmBody alarm={alarm} name={equipmentOf(line, alarm.equipmentId)?.name ?? "设备"} />
              </button>
            </li>
          ))}
        </ul>
      </section>
    </aside>
  )
}

function AlarmBody({ alarm, name }: { alarm: AlarmEvent; name: string }) {
  return (
    <>
      <span className={alarm.kind === "sensor" ? "ov-kind" : "ov-kind ov-kind-infer"}>{alarm.kind === "sensor" ? "传感器" : "推断"}</span>
      <strong>{alarm.title}</strong>
      <span>
        {name} · {alarm.level === "warn" ? "警告" : "异常"} · <span className="ov-tabular">{alarm.time}</span>
      </span>
      <em>{alarm.note}</em>
    </>
  )
}
