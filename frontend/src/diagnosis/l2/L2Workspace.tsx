import { useMemo, useState } from "react"
import { DiagnosisPanel } from "./DiagnosisPanel"
import { consumeL2HandoffFocus, type L2HandoffFocus } from "./handoffFocus"
import { ModelsPanel } from "./ModelsPanel"
import { TrainingPanel } from "./TrainingPanel"
import type { DiagnosisResult, L2ViewId } from "./types"
import "./l2.css"

const VIEWS: { id: L2ViewId; label: string; hint: string }[] = [
  { id: "diagnosis", label: "设备诊断", hint: "PED / 特征输入 · 双分类器" },
  { id: "models", label: "模型管理", hint: "激活回滚 · 基于此版本再训练" },
  { id: "training", label: "训练管理", hint: "新增训练 · 任务状态 · 日志" },
]

function asView(value: string | undefined): L2ViewId {
  if (value === "models" || value === "training" || value === "diagnosis") return value
  return "diagnosis"
}

type L3Payload = {
  contract_version: string
  equipment_type: string
  record_ids: string[]
  prepared_dataset_id?: string
  dataset_revision_id?: string
  model_bundle_id?: string
  diagnosis_run_id?: string
  trigger_reason: "auto_review" | "manual_request"
  decision?: DiagnosisResult["decision"]
  binary?: DiagnosisResult["binary_classifier"]
  multiclass?: DiagnosisResult["multiclass_classifier"]
  consistency?: DiagnosisResult["consistency"]
  spectral_reference?: DiagnosisResult["spectral_reference"]
  l2_results: Array<Record<string, unknown>>
}

export function L2Workspace({ initialView }: { initialView?: string }) {
  const [handoffFocus] = useState<L2HandoffFocus | null>(() => consumeL2HandoffFocus())
  const [view, setView] = useState<L2ViewId>(() => asView(handoffFocus?.view || initialView))
  const [focusJobId, setFocusJobId] = useState<string | null>(null)
  const [l3Payload, setL3Payload] = useState<L3Payload | null>(null)

  const title = useMemo(() => VIEWS.find((item) => item.id === view)?.label || "L2", [view])

  return (
    <section className="l2-shell" aria-label="L2 设备状态筛查与初步故障诊断">
      <header className="l2-head">
        <div>
          <p className="l2-kicker">L2 Workspace</p>
          <h2>{title}</h2>
          <p>以设备诊断为中心，训练与模型管理为支撑。概率用于模型判断参考，不等于物理故障真实发生概率。</p>
          {handoffFocus?.reason ? <p className="l2-note is-warn">来自总览：{handoffFocus.reason}</p> : null}
          {handoffFocus?.prepared_dataset_id || handoffFocus?.ped_id ? (
            <p className="l2-note">
              任务来源 PED {handoffFocus.prepared_dataset_id || handoffFocus.ped_id}
              {handoffFocus.dataset_revision_id ? ` · ${handoffFocus.dataset_revision_id}` : ""}
              {handoffFocus.equipment_type ? ` · ${handoffFocus.equipment_type}` : ""}
            </p>
          ) : null}
        </div>
        <nav className="l2-tabs" aria-label="L2 工作区">
          {VIEWS.map((item) => (
            <button
              key={item.id}
              type="button"
              className={item.id === view ? "is-active" : undefined}
              onClick={() => setView(item.id)}
            >
              <strong>{item.label}</strong>
              <span>{item.hint}</span>
            </button>
          ))}
        </nav>
      </header>

      {view === "diagnosis" ? (
        <DiagnosisPanel
          initialFocus={handoffFocus}
          onOpenModels={() => setView("models")}
          onOpenTraining={() => setView("training")}
          onHandOffToL3={(payload) => setL3Payload(payload)}
        />
      ) : null}
      {view === "models" ? (
        <ModelsPanel
          onOpenTraining={(jobId) => {
            setFocusJobId(jobId)
            setView("training")
          }}
        />
      ) : null}
      {view === "training" ? (
        <TrainingPanel
          initialFocus={handoffFocus}
          focusJobId={focusJobId}
          onFocusJobConsumed={() => setFocusJobId(null)}
          onOpenModels={() => setView("models")}
        />
      ) : null}

      {l3Payload ? (
        <div className="l2-modal" role="dialog" aria-modal="true" aria-label="L3 交接数据">
          <div className="l2-modal-card">
            <h3>L3 交接契约（未接入）</h3>
            <p className="l2-note">
              触发来源 {l3Payload.trigger_reason}。以下为交接数据预览，不会改写原始 L2 分流，也不会发起 L3 API。
            </p>
            <pre className="l2-log">{JSON.stringify(l3Payload, null, 2)}</pre>
            <div className="l2-actions">
              <button type="button" onClick={() => setL3Payload(null)}>
                关闭
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  )
}
