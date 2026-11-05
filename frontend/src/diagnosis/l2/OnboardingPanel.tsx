import { useEffect, useMemo, useState } from "react"
import {
  confirmEquipmentOnboarding,
  getEquipmentOnboarding,
  mapL2Error,
  prepareEquipmentOnboarding,
  type OnboardingDraft,
} from "./api"
import type { L2HandoffFocus } from "./handoffFocus"

type Props = {
  focus: L2HandoffFocus
  onCompleted: (result: Record<string, unknown>) => void
}

export function OnboardingPanel({ focus, onCompleted }: Props) {
  const [draft, setDraft] = useState<OnboardingDraft | null>(null)
  const [equipmentType, setEquipmentType] = useState("")
  const [displayName, setDisplayName] = useState("")
  const [normalLabel, setNormalLabel] = useState("")
  const [selectedIds, setSelectedIds] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const [notice, setNotice] = useState("")

  useEffect(() => {
    let cancelled = false
    setBusy(true)
    setError("")
    const load = async () => {
      try {
        let body: OnboardingDraft
        if (focus.onboarding_id) {
          body = await getEquipmentOnboarding(focus.onboarding_id)
        } else if (focus.dataset_id && focus.pd_revision_id) {
          body = await prepareEquipmentOnboarding({
            dataset_id: focus.dataset_id,
            pd_revision_id: focus.pd_revision_id,
            equipment_category: focus.equipment_category,
          })
        } else {
          throw new Error("缺少 onboarding 或 PD 参数。")
        }
        if (cancelled) return
        setDraft(body)
        setEquipmentType(body.equipment_type_candidate || "")
        setDisplayName(body.display_name_candidate || "")
        setNormalLabel(body.normal_label_candidate || "")
        setSelectedIds(
          (body.feature_candidates || [])
            .filter((item) => item.selected && !item.leakage)
            .map((item) => item.feature_id),
        )
        if (body.already_registered && !body.needs_confirmation) {
          setNotice("该设备配置已注册，可直接训练或诊断。")
        }
      } catch (err) {
        if (!cancelled) setError(mapL2Error(err))
      } finally {
        if (!cancelled) setBusy(false)
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [focus.onboarding_id, focus.dataset_id, focus.pd_revision_id, focus.equipment_category])

  const classEntries = useMemo(() => Object.entries(draft?.class_counts || {}), [draft])

  function toggleFeature(featureId: string, enabled: boolean) {
    setSelectedIds((prev) => {
      if (enabled) return prev.includes(featureId) ? prev : [...prev, featureId]
      return prev.filter((id) => id !== featureId)
    })
  }

  async function onConfirm() {
    if (!draft?.onboarding_id) return
    setBusy(true)
    setError("")
    setNotice("")
    try {
      const result = await confirmEquipmentOnboarding(draft.onboarding_id, {
        equipment_type: equipmentType || undefined,
        display_name: displayName || undefined,
        normal_label: normalLabel || undefined,
        selected_feature_ids: selectedIds,
        handoff_id: focus.handoff_id,
        handoff_item_id: focus.handoff_item_id,
        continue_handoff: true,
      })
      setNotice("设备配置已保存，后续同类数据将自动复用。")
      onCompleted(result)
    } catch (err) {
      setError(mapL2Error(err))
    } finally {
      setBusy(false)
    }
  }

  if (!draft && busy) {
    return <p className="l2-note">正在生成设备配置建议…</p>
  }
  if (!draft) {
    return error ? <p className="l2-note is-error">{error}</p> : null
  }

  return (
    <article className="l2-card l2-onboarding" aria-label="设备首次配置">
      <header>
        <h4>设备首次配置</h4>
        <span>{draft.configuration_status}</span>
      </header>
      <p className="l2-note">系统已根据 L1 确认字段生成推荐配置。默认只需确认后保存。</p>

      <div className="l2-onboarding-grid">
        <section>
          <h5>设备信息</h5>
          <label className="l2-field">
            <span>显示名称</span>
            <input value={displayName} onChange={(event) => setDisplayName(event.target.value)} />
          </label>
          <label className="l2-field">
            <span>equipment_type</span>
            <input value={equipmentType} onChange={(event) => setEquipmentType(event.target.value)} />
          </label>
          <p className="l2-note">
            数据集 {draft.dataset_id} · PD {draft.pd_revision_id} · 记录 {draft.record_count ?? "—"}
          </p>
        </section>

        <section>
          <h5>特征与标签</h5>
          <p className="l2-note">
            已选特征 {selectedIds.length} · 目标 {draft.target_candidate || "故障类型"} · 类别{" "}
            {draft.class_count ?? 0}
          </p>
          <label className="l2-field">
            <span>正常标签</span>
            <select value={normalLabel} onChange={(event) => setNormalLabel(event.target.value)}>
              <option value="">请选择</option>
              {(draft.class_labels || []).map((label) => (
                <option key={label} value={label}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <div className="l2-feature-checklist">
            {(draft.feature_candidates || []).map((item) => (
              <label key={item.feature_id} className={item.leakage ? "is-blocked" : undefined}>
                <input
                  type="checkbox"
                  checked={selectedIds.includes(item.feature_id)}
                  disabled={Boolean(item.leakage)}
                  onChange={(event) => toggleFeature(item.feature_id, event.target.checked)}
                />
                <span>
                  {item.name}
                  {item.unit ? ` (${item.unit})` : ""}
                  <small>
                    {item.feature_id} · 有效 {item.numeric_count ?? 0}
                    {item.leakage ? " · 泄漏字段" : ""}
                  </small>
                </span>
              </label>
            ))}
          </div>
          <div className="l2-meta-block">
            {classEntries.map(([label, count]) => (
              <p key={label}>
                {label}：{count}
              </p>
            ))}
          </div>
        </section>

        <section>
          <h5>训练准备检查</h5>
          <ul className="l2-check-list">
            <li className={selectedIds.length ? "is-ok" : "is-bad"}>特征已选择</li>
            <li className={normalLabel ? "is-ok" : "is-bad"}>正常类别已确认</li>
            <li className={draft.training_readiness?.has_labels ? "is-ok" : "is-bad"}>存在故障标签</li>
            <li className={draft.training_readiness?.has_abnormal_label ? "is-ok" : "is-bad"}>存在非正常类别</li>
            <li className={draft.training_readiness?.min_samples_ok ? "is-ok" : "is-bad"}>样本数量基本满足</li>
            <li className={draft.training_readiness?.ready ? "is-ok" : "is-bad"}>
              {draft.training_readiness?.ready ? "可进入训练" : "暂不可训练"}
            </li>
          </ul>
          {(draft.warnings || []).length ? (
            <div className="l2-meta-block">
              {draft.warnings?.map((item) => (
                <p key={item} className="l2-note is-warn">
                  {item}
                </p>
              ))}
            </div>
          ) : null}
        </section>
      </div>

      <div className="l2-actions">
        <button type="button" className="l2-primary" disabled={busy} onClick={() => void onConfirm()}>
          {busy ? "保存中…" : "确认设备配置"}
        </button>
      </div>
      {error ? <p className="l2-note is-error">{error}</p> : null}
      {notice ? <p className="l2-note is-ok">{notice}</p> : null}
    </article>
  )
}
