import { createContext, useContext, useEffect, useState, type ReactNode } from "react"
import { diagnosisTabQuery } from "./events/diagnosisNav"

export type PageId = "portal" | "graph" | "monitor" | "events" | "diagnosis" | "health" | "report"

export type AppRoute = {
  page: PageId
  eventId?: string
  eventType?: string
  range?: string
  equipmentId?: string
  diagnosisId?: string
  tab?: string
  runId?: string
  engine?: string
  scope?: "global"
}

const RouteContext = createContext<{ route: AppRoute; navigate: (next: AppRoute, mode?: "push" | "replace") => void } | null>(null)

const PAGES = new Set<PageId>(["portal", "graph", "monitor", "events", "diagnosis", "health", "report"])
// 设备健康中心使用 /health，报告中心使用 /report，未知路径仍回到产线总览。

export function readRoute(location: Location = window.location): AppRoute {
  const path = location.pathname.replace(/\/+$/, "") || "/"
  const name = path === "/" ? "portal" : (path.slice(1) as PageId)
  const page: PageId = PAGES.has(name) ? name : "portal"
  const params = new URLSearchParams(location.search)
  const pick = (key: string) => {
    const value = params.get(key)
    return value ? value : undefined
  }
  return {
    page,
    eventId: pick("eventId"),
    eventType: pick("eventType"),
    range: pick("range"),
    equipmentId: pick("equipmentId"),
    diagnosisId: pick("diagnosisId"),
    tab: diagnosisTabQuery(params.get("tab")),
    runId: routeToken(params.get("runId")),
    engine: routeToken(params.get("engine")),
    scope: params.get("scope") === "global" ? "global" : undefined,
  }
}

function routeToken(value: string | null): string | undefined {
  if (!value || !/^[A-Za-z0-9_-]{1,40}$/.test(value)) return undefined
  return value
}

export function toHref(route: AppRoute): string {
  const params = new URLSearchParams()
  if (route.eventId) params.set("eventId", route.eventId)
  if (route.eventType) params.set("eventType", route.eventType)
  if (route.range) params.set("range", route.range)
  if (route.equipmentId) params.set("equipmentId", route.equipmentId)
  if (route.diagnosisId) params.set("diagnosisId", route.diagnosisId)
  if (route.page === "diagnosis" && route.tab && route.tab !== "overview" && route.tab !== "result") params.set("tab", route.tab)
  if (route.page === "diagnosis" && route.runId) params.set("runId", route.runId)
  if (route.page === "diagnosis" && route.engine) params.set("engine", route.engine)
  if (route.page === "diagnosis" && route.scope === "global") params.set("scope", "global")
  const path = route.page === "portal" ? "/" : `/${route.page}`
  const query = params.toString()
  return query ? `${path}?${query}` : path
}

export function RouteProvider({ children }: { children: ReactNode }) {
  const [route, setRoute] = useState<AppRoute>(() => readRoute())

  useEffect(() => {
    const sync = () => setRoute(readRoute())
    window.addEventListener("popstate", sync)
    return () => window.removeEventListener("popstate", sync)
  }, [])

  function navigate(next: AppRoute, mode: "push" | "replace" = "push") {
    const href = toHref(next)
    const current = `${window.location.pathname}${window.location.search}`
    if (current !== href) {
      if (mode === "replace") history.replaceState(null, "", href)
      else history.pushState(null, "", href)
    }
    setRoute(readRoute())
  }

  return <RouteContext.Provider value={{ route, navigate }}>{children}</RouteContext.Provider>
}

export function useRoute() {
  const value = useContext(RouteContext)
  if (!value) {
    throw new Error("useRoute 需要放在 RouteProvider 内")
  }
  return value
}
