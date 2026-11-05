import { useEffect, useMemo, useRef, useState } from "react"
import {
  diagnoseByFeatures,
  diagnosePreparedDataset,
  getDiagnosisRun,
  listDatasets,
  listDiagnosisRunRecords,
  listEquipmentTypes,
  listPedRows,
  mapL2Error,
  type DiagnosisRunSummary,
} from "./api"
import {
  calibrationHint,
  decisionTitle,
  decisionTone,
  formatPercent,
  screeningActionLabel,
  sortedProbabilities,
  sourceTypeLabel,
} from "./format"
import type { L2HandoffFocus } from "./handoffFocus"
import type { DatasetSummary, DiagnosisResult, EquipmentTypeSummary, PedRecordRow } from "./types"

type Props = {
  initialFocus?: L2HandoffFocus | null
  onOpenModels: () => void
  onOpenTraining: () => void
  onHandOffToL3: (payload: {
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
  }) => void
}

function pickCalibration(result: DiagnosisResult, key: "binary" | "multiclass"): string | null {
  const specific =
    key === "binary" ? result.binary_classifier.calibration_status : result.multiclass_classifier.calibration_status
  if (typeof specific === "string" && specific) return specific
  const top = result.calibration_status
  if (top && typeof top === "object" && typeof top[key] === "string") return top[key]
  return null
}

export function DiagnosisPanel({ initialFocus = null, onOpenModels, onOpenTraining, onHandOffToL3 }: Props) {
  const [equipment, setEquipment] = useState<EquipmentTypeSummary[]>([])
  const [datasets, setDatasets] = useState<DatasetSummary[]>([])
  const [equipmentType, setEquipmentType] = useState("")
  const [datasetId, setDatasetId] = useState("")
  const [records, setRecords] = useState<PedRecordRow[]>([])
  const [recordTotal, setRecordTotal] = useState(0)
  const [recordOffset, setRecordOffset] = useState(0)
  const [selectedRecordId, setSelectedRecordId] = useState("")
  const [recordFilter, setRecordFilter] = useState("")
  const [result, setResult] = useState<DiagnosisResult | null>(null)
  const [manualFeatures, setManualFeatures] = useState<Record<string, string>>({})
  const [loadingList, setLoadingList] = useState(true)
  const [loadingRecords, setLoadingRecords] = useState(false)
  const [diagnosing, setDiagnosing] = useState(false)
  const [error, setError] = useState("")
  const [diagnosisRunId, setDiagnosisRunId] = useState("")
  const [diagnosisRun, setDiagnosisRun] = useState<DiagnosisRunSummary | null>(null)
  const [savedByRecord, setSavedByRecord] = useState<Record<string, DiagnosisResult>>({})
  const [screenFilter, setScreenFilter] = useState("")
  const [focusReady, setFocusReady] = useState(false)
  const requestSeq = useRef(0)
  const focusApplied = useRef(false)

  const selectedEquipment = equipment.find((item) => item.equipment_type === equipmentType)
  const equipmentDatasets = useMemo(
    () => datasets.filter((item) => item.equipment_type === equipmentType),
    [datasets, equipmentType],
  )
  const selectedDataset = equipmentDatasets.find((item) => item.dataset_id === datasetId || item.prepared_dataset_id === datasetId)
  const isPed = selectedDataset?.source_type === "l1_prepared"
  const pedId = selectedDataset?.prepared_dataset_id || (selectedDataset?.dataset_id?.startsWith("PED-") ? selectedDataset.dataset_id : "")
  const filteredRecords = useMemo(() => {
    const q = recordFilter.trim().toLowerCase()
    let rows = records
    if (screenFilter) {
      rows = rows.filter((row) => {
        const saved = savedByRecord[row.record_id]
        const screen = saved?.decision?.screening_result || ""
        return screen === screenFilter
      })
    }
    if (!q) return rows
    return rows.filter((row) =>
      [row.record_id, row.equipment_id, row.collection_time].some((value) => String(value || "").toLowerCase().includes(q)),
    )
  }, [records, recordFilter, screenFilter, savedByRecord])

  useEffect(() => {
    const controller = new AbortController()
    setLoadingList(true)
    const focus = initialFocus
    Promise.all([listEquipmentTypes(controller.signal), listDatasets(controller.signal)])
      .then(([eq, ds]) => {
        setError("")
        setEquipment(eq)
        setDatasets(ds)
        const pedKey = focus?.prepared_dataset_id || focus?.ped_id
        if (focus?.equipment_type && eq.some((item) => item.equipment_type === focus.equipment_type)) {
          setEquipmentType(focus.equipment_type)
          focusApplied.current = true
          if (pedKey) setDatasetId(pedKey)
          if (focus.diagnosis_run_id) setDiagnosisRunId(focus.diagnosis_run_id)
          if (focus.record_id) setSelectedRecordId(focus.record_id)
        } else {
          const preferred = eq.find((item) => item.equipment_type === "high_pressure_descaling_pump") || eq[0]
          if (preferred) setEquipmentType(preferred.equipment_type)
        }
        setFocusReady(true)
      })
      .catch((err) => {
        if (err instanceof Error && err.name === "AbortError") return
        setError(mapL2Error(err))
        setFocusReady(true)
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoadingList(false)
      })
    return () => controller.abort()
  }, [])

  useEffect(() => {
    if (!focusReady) return
    setResult(null)
    if (!focusApplied.current) {
      setSelectedRecordId("")
      setDiagnosisRunId("")
      setDiagnosisRun(null)
      setSavedByRecord({})
    }
    setRecords([])
    setRecordTotal(0)
    setRecordOffset(0)
    setRecordFilter("")
    setScreenFilter("")
    setError("")
    if (!focusApplied.current || !datasetId) {
      const nextDatasets = datasets.filter((item) => item.equipment_type === equipmentType)
      const preferred = nextDatasets.find((item) => item.source_type === "l1_prepared" && item.inference_ready) || nextDatasets[0]
      setDatasetId(preferred?.dataset_id || "")
    }
    focusApplied.current = false
    const columns = selectedEquipment?.feature_columns || []
    const blank: Record<string, string> = {}
    for (const name of columns) blank[name] = ""
    setManualFeatures(blank)
  }, [equipmentType, datasets, selectedEquipment?.feature_columns, focusReady])

  useEffect(() => {
    if (!diagnosisRunId) {
      setDiagnosisRun(null)
      setSavedByRecord({})
      return
    }
    const controller = new AbortController()
    Promise.all([
      getDiagnosisRun(diagnosisRunId, controller.signal),
      listDiagnosisRunRecords(diagnosisRunId, { offset: 0, limit: 200 }, controller.signal),
    ])
      .then(([run, page]) => {
        setDiagnosisRun(run)
        const map: Record<string, DiagnosisResult> = {}
        for (const item of page.items) {
          if (item.result) map[item.record_id] = item.result
        }
        setSavedByRecord(map)
        const firstOk = page.items.find((item) => item.status === "ok")
        if (firstOk && !selectedRecordId) {
          setSelectedRecordId(firstOk.record_id)
          setResult(firstOk.result)
        } else if (selectedRecordId && map[selectedRecordId]) {
          setResult(map[selectedRecordId])
        }
      })
      .catch((err) => {
        if (err instanceof Error && err.name === "AbortError") return
        setError(mapL2Error(err))
      })
    return () => controller.abort()
  }, [diagnosisRunId])

  useEffect(() => {
    if (!isPed || !pedId) {
      setRecords([])
      setRecordTotal(0)
      return
    }
    const controller = new AbortController()
    const seq = ++requestSeq.current
    setLoadingRecords(true)
    setError("")
    listPedRows(pedId, { offset: recordOffset, limit: 20 }, controller.signal)
      .then((page) => {
        if (seq !== requestSeq.current) return
        setRecords(page.rows)
        setRecordTotal(page.total ?? page.total_records ?? page.rows.length)
        if (!selectedRecordId && page.rows[0]?.record_id) setSelectedRecordId(page.rows[0].record_id)
      })
      .catch((err) => {
        if (seq !== requestSeq.current) return
        setError(mapL2Error(err))
        setRecords([])
      })
      .finally(() => {
        if (seq === requestSeq.current) setLoadingRecords(false)
      })
    return () => controller.abort()
  }, [isPed, pedId, recordOffset])

  async function runPedDiagnose() {
    if (!pedId || !selectedRecordId || !selectedDataset) return
    if (!selectedEquipment?.has_active_model) {
      setError("该设备暂无已发布诊断模型。")
      return
    }
    const seq = ++requestSeq.current
    setDiagnosing(true)
    setError("")
    try {
      const response = await diagnosePreparedDataset(pedId, {
        record_ids: [selectedRecordId],
        revision_id: selectedDataset.revision_id || undefined,
      })
      if (seq !== requestSeq.current) return
      const first = response.results?.[0]
      if (!first) throw new Error("诊断结果为空。")
      setResult({
        ...first,
        prepared_dataset_id: response.prepared_dataset_id || pedId,
        dataset_revision_id: response.dataset_revision_id || selectedDataset.revision_id || undefined,
      })
    } catch (err) {
      if (seq !== requestSeq.current) return
      setResult(null)
      setError(mapL2Error(err))
    } finally {
      if (seq === requestSeq.current) setDiagnosing(false)
    }
  }

  async function runManualDiagnose() {
    if (!selectedEquipment) return
    if (!selectedEquipment.has_active_model) {
      setError("该设备暂无已发布诊断模型。")
      return
    }
    const features: Record<string, number> = {}
    for (const name of selectedEquipment.feature_columns) {
      const raw = manualFeatures[name]
      const value = Number(raw)
      if (raw === "" || Number.isNaN(value)) {
        setError(`请填写有效数值特征：${name}`)
        return
      }
      features[name] = value
    }
    const seq = ++requestSeq.current
    setDiagnosing(true)
    setError("")
    try {
      const response = await diagnoseByFeatures({
        equipment_type: selectedEquipment.equipment_type,
        record_id: selectedRecordId || undefined,
        features,
      })
      if (seq !== requestSeq.current) return
      setResult(response)
    } catch (err) {
      if (seq !== requestSeq.current) return
      setResult(null)
      setError(mapL2Error(err))
    } finally {
      if (seq === requestSeq.current) setDiagnosing(false)
    }
  }

  const selectedRow = records.find((row) => row.record_id === selectedRecordId)
  const binaryItems = sortedProbabilities(result?.binary_classifier?.probabilities)
  const multiItems = sortedProbabilities(result?.multiclass_classifier?.probabilities)
  const tone = decisionTone(result)
  const binaryCalib = result ? pickCalibration(result, "binary") : null
  const multiCalib = result ? pickCalibration(result, "multiclass") : null

  return (
    <div className="l2-split">
      <aside className="l2-side" aria-label="设备与记录">
        <h3>设备与数据</h3>
        {loadingList ? <p className="l2-note">正在加载设备与数据集…</p> : null}
        <label className="l2-field">
          <span>设备类别</span>
          <select value={equipmentType} onChange={(event) => setEquipmentType(event.target.value)} disabled={!equipment.length}>
            {equipment.map((item) => (
              <option key={item.equipment_type} value={item.equipment_type}>
                {item.display_name}（{item.feature_count} 特征 / {item.class_count} 类）
              </option>
            ))}
          </select>
        </label>
        {selectedEquipment ? (
          <div className="l2-meta-block">
            <p>Schema {selectedEquipment.schema_version}</p>
            <p>
              Active{" "}
              {selectedEquipment.has_active_model ? selectedEquipment.active_model_bundle_id || "已加载" : "无"}
            </p>
            {!selectedEquipment.has_active_model ? (
              <p className="l2-note is-warn">
                该设备暂无已发布诊断模型。
                <button type="button" className="l2-link" onClick={onOpenModels}>
                  模型管理
                </button>
                <button type="button" className="l2-link" onClick={onOpenTraining}>
                  去训练
                </button>
              </p>
            ) : null}
          </div>
        ) : null}

        <label className="l2-field">
          <span>数据集</span>
          <select value={datasetId} onChange={(event) => setDatasetId(event.target.value)} disabled={!equipmentDatasets.length}>
            {equipmentDatasets.length === 0 ? <option value="">无可用数据集</option> : null}
            {equipmentDatasets.map((item) => (
              <option key={item.dataset_id} value={item.dataset_id}>
                {sourceTypeLabel(item.source_type)} · {item.dataset_id}
                {item.revision_id ? ` / ${item.revision_id}` : ""}
              </option>
            ))}
          </select>
        </label>
        {selectedDataset ? (
          <div className="l2-meta-block">
            <p>
              {sourceTypeLabel(selectedDataset.source_type)} · {selectedDataset.release_level || "—"} ·{" "}
              {selectedDataset.release_status || "—"}
            </p>
            <p>
              记录 {selectedDataset.record_count ?? "—"} · 特征 {selectedDataset.feature_count ?? "—"} · 类别{" "}
              {selectedDataset.label_count ?? "—"}
            </p>
            {selectedDataset.source_type === "legacy_csv" ? (
              <p className="l2-note">Legacy CSV 不是 L1 已发布 PED。当前可用手工特征输入进行诊断。</p>
            ) : null}
            {selectedDataset.warning_count ? <p className="l2-note is-warn">警告 {selectedDataset.warning_count} 项</p> : null}
          </div>
        ) : null}

        {diagnosisRun ? (
          <div className="l2-meta-block" aria-label="批量诊断摘要">
            <p>
              批次 {diagnosisRun.diagnosis_run_id} · {diagnosisRun.status}
            </p>
            <p>
              已诊断 {diagnosisRun.succeeded_records}/{diagnosisRun.total_records}
            </p>
            <p>
              NORMAL {diagnosisRun.counts?.NORMAL || 0} · ABNORMAL {diagnosisRun.counts?.ABNORMAL || 0} · CONFLICT{" "}
              {diagnosisRun.counts?.CONFLICT || 0} · UNCERTAIN {diagnosisRun.counts?.UNCERTAIN || 0}
            </p>
            <p>
              模型{" "}
              <button type="button" className="l2-link" onClick={onOpenModels}>
                {diagnosisRun.model_bundle_id}
              </button>
            </p>
            <p className="l2-note">默认展示已保存结果；仅点击「重新诊断」时才会再次调用推理服务。</p>
          </div>
        ) : null}

        {isPed ? (
          <>
            <div className="l2-row-tools">
              <label className="l2-field is-grow">
                <span>当前页筛选</span>
                <input value={recordFilter} onChange={(event) => setRecordFilter(event.target.value)} placeholder="record / 设备 / 时间" />
              </label>
              <button type="button" disabled={recordOffset <= 0 || loadingRecords} onClick={() => setRecordOffset((value) => Math.max(0, value - 20))}>
                上一页
              </button>
              <button
                type="button"
                disabled={loadingRecords || recordOffset + 20 >= recordTotal}
                onClick={() => setRecordOffset((value) => value + 20)}
              >
                下一页
              </button>
            </div>
            <p className="l2-note">
              {loadingRecords ? "加载记录中…" : `共 ${recordTotal} 条，偏移 ${recordOffset}；筛选仅作用于当前页。`}
            </p>
            <div className="l2-record-list" role="listbox" aria-label="PED 记录">
              {filteredRecords.map((row) => (
                <button
                  key={row.record_id}
                  type="button"
                  role="option"
                  aria-selected={row.record_id === selectedRecordId}
                  className={row.record_id === selectedRecordId ? "is-selected" : undefined}
                  onClick={() => {
                    setSelectedRecordId(row.record_id)
                    const saved = savedByRecord[row.record_id]
                    setResult(saved || null)
                  }}
                >
                  <strong>{row.record_id}</strong>
                  <span>
                    {row.equipment_id || "—"} · {row.collection_time || "—"}
                    {savedByRecord[row.record_id]?.decision?.screening_result
                      ? ` · ${savedByRecord[row.record_id].decision.screening_result}`
                      : ""}
                  </span>
                </button>
              ))}
              {!loadingRecords && filteredRecords.length === 0 ? <p className="l2-note">当前页没有匹配记录。</p> : null}
            </div>
            <label className="l2-field">
              <span>诊断状态过滤</span>
              <select value={screenFilter} onChange={(event) => setScreenFilter(event.target.value)}>
                <option value="">全部</option>
                <option value="NORMAL">NORMAL</option>
                <option value="ABNORMAL">ABNORMAL</option>
                <option value="CONFLICT">CONFLICT</option>
                <option value="UNCERTAIN">UNCERTAIN</option>
              </select>
            </label>
            <button type="button" className="l2-primary" disabled={!selectedRecordId || diagnosing || !selectedEquipment?.has_active_model} onClick={runPedDiagnose}>
              {diagnosing ? "诊断中…" : savedByRecord[selectedRecordId] ? "重新诊断" : "诊断所选记录"}
            </button>
          </>
        ) : (
          <>
            <p className="l2-note">按设备 Schema 手工输入特征后调用 `/l2/diagnose`。不会读取磁盘 CSV，也不会伪装为 PED。</p>
            <div className="l2-feature-grid">
              {(selectedEquipment?.feature_columns || []).map((name) => (
                <label key={name} className="l2-field">
                  <span>{name}</span>
                  <input
                    inputMode="decimal"
                    value={manualFeatures[name] || ""}
                    onChange={(event) => setManualFeatures((prev) => ({ ...prev, [name]: event.target.value }))}
                  />
                </label>
              ))}
            </div>
            <button type="button" className="l2-primary" disabled={diagnosing || !selectedEquipment?.has_active_model} onClick={runManualDiagnose}>
              {diagnosing ? "诊断中…" : "提交特征诊断"}
            </button>
          </>
        )}
      </aside>

      <section className="l2-main" aria-label="诊断结果">
        {error ? <p className="l2-note is-error">{error}</p> : null}
        {!result ? (
          <div className="l2-empty">
            <h3>诊断结果</h3>
            <p>选择设备与记录后执行诊断。不会在切换设备时自动批量推理全部记录。</p>
          </div>
        ) : (
          <>
            <header className={`l2-result-head is-${tone}`}>
              <div>
                <p className="l2-kicker">{selectedEquipment?.display_name || result.equipment_type}</p>
                <h3>{decisionTitle(result)}</h3>
                <p>
                  {result.record_id || selectedRecordId || "手工输入"}
                  {selectedRow?.collection_time ? ` · ${selectedRow.collection_time}` : ""}
                </p>
              </div>
              <div className="l2-result-tags">
                <span>{result.decision?.screening_result || result.decision?.status || "—"}</span>
                <span>{result.model_bundle_id || "—"}</span>
              </div>
            </header>

            <div className="l2-prob-grid">
              <article className="l2-card">
                <header>
                  <h4>二分类器</h4>
                  <span>{result.binary_classifier.predicted_label || "—"}</span>
                </header>
                <ul className="l2-prob-list">
                  {binaryItems.map((item) => (
                    <li key={item.label}>
                      <div className="l2-prob-label">
                        <span>{item.label}</span>
                        <b>{formatPercent(item.value)}</b>
                      </div>
                      <div className="l2-prob-track" aria-hidden="true">
                        <span style={{ width: `${Math.min(100, Math.max(0, item.value * 100))}%` }} />
                      </div>
                    </li>
                  ))}
                </ul>
                {calibrationHint(binaryCalib) ? <p className="l2-note is-warn">{calibrationHint(binaryCalib)}</p> : null}
              </article>

              <article className="l2-card">
                <header>
                  <h4>多分类器</h4>
                  <span>{result.multiclass_classifier.predicted_label || "—"}</span>
                </header>
                <ul className="l2-prob-list">
                  {multiItems.map((item) => (
                    <li key={item.label}>
                      <div className="l2-prob-label">
                        <span>{item.label}</span>
                        <b>{formatPercent(item.value)}</b>
                      </div>
                      <div className="l2-prob-track" aria-hidden="true">
                        <span style={{ width: `${Math.min(100, Math.max(0, item.value * 100))}%` }} />
                      </div>
                    </li>
                  ))}
                </ul>
                <p className="l2-note">
                  Top1–Top2 差值 {formatPercent(result.multiclass_classifier.top1_top2_margin)} · 类别数 {multiItems.length}
                </p>
                {calibrationHint(multiCalib) ? <p className="l2-note is-warn">{calibrationHint(multiCalib)}</p> : null}
              </article>
            </div>

            <article className="l2-card">
              <header>
                <h4>模型一致性</h4>
                <span>ΔP {formatPercent(result.consistency.probability_gap)}</span>
              </header>
              <div className="l2-consistency">
                <p>
                  Classifier A 非正常 {formatPercent(result.consistency.p_a_abnormal)}（{result.consistency.binary_direction || "—"}）
                </p>
                <p>
                  Classifier B 非正常 {formatPercent(result.consistency.p_b_abnormal)}（{result.consistency.multiclass_aggregate_direction || "—"}）
                </p>
                <p>
                  分流 {result.decision.route || "—"} · {result.decision.status || "—"} · {screeningActionLabel(result)}
                </p>
                <p className="l2-note">
                  {(result.decision.reason_codes || []).join(" · ") || "无 reason_codes"}。模型一致不等于根因已验证。
                </p>
              </div>
            </article>

            {result.spectral_reference ? (
              <article className="l2-card l2-spectral-ref">
                <header>
                  <h4>典型频谱模式｜参考</h4>
                  <span>{result.spectral_reference.fault_class || "—"}</span>
                </header>
                <p>{result.spectral_reference.description}</p>
                {(result.spectral_reference.related_features || []).length ? (
                  <p className="l2-note">
                    相关特征：{(result.spectral_reference.related_features || []).join(" · ")}
                  </p>
                ) : null}
                <p className="l2-note is-warn">参考模式，尚未进行独立频谱验证。</p>
                <details>
                  <summary>模板来源与版本</summary>
                  <p className="l2-note">
                    {result.spectral_reference.source || "equipment_template"} ·{" "}
                    {result.spectral_reference.template_version || "—"} ·{" "}
                    {result.spectral_reference.verification_status || "reference_only"}
                  </p>
                </details>
              </article>
            ) : null}

            <article className="l2-card">
              <header>
                <h4>来源追溯</h4>
              </header>
              <div className="l2-provenance">
                <p>模型 {result.model_bundle_id || "—"}</p>
                <p>Schema {result.schema_version || selectedEquipment?.schema_version || "—"}</p>
                <p>数据集 {result.prepared_dataset_id || selectedDataset?.dataset_id || "手工特征"}</p>
                <p>修订 {result.dataset_revision_id || selectedDataset?.revision_id || "—"}</p>
                <p className="l2-note">概率不等于物理故障真实发生概率；页面不展示原始故障标签作为预测。</p>
              </div>
            </article>

            <div className="l2-l3-slot">
              <div>
                <h4>深入分析（L3）</h4>
                {result.decision?.screening_result === "NORMAL" ? (
                  <p className="l2-note">L2 初判正常。筛查完成；无需自动进入 L3。</p>
                ) : result.decision?.screening_result === "ABNORMAL" ||
                  result.decision?.screening_result === "CONFLICT" ||
                  result.decision?.screening_result === "UNCERTAIN" ||
                  result.decision?.route === "l3_review_recommended" ? (
                  <p className="l2-note">
                    {result.decision?.screening_result === "ABNORMAL"
                      ? "L2 初判异常，已自动建议进入 L3。L3 深度分析尚未接入，此处仅保留交接数据查看。"
                      : "建议 L3 复核。L3 深度分析尚未接入，此处仅保留交接数据查看。"}
                  </p>
                ) : (
                  <p className="l2-note">L2 筛查已完成。</p>
                )}
              </div>
              {result.decision?.screening_result === "ABNORMAL" ||
              result.decision?.screening_result === "CONFLICT" ||
              result.decision?.screening_result === "UNCERTAIN" ||
              result.decision?.route === "l3_review_recommended" ? (
                <button
                  type="button"
                  onClick={() => {
                    const recordId = result.record_id || selectedRecordId || null
                    onHandOffToL3({
                      contract_version: "l2_l3_handoff_v1",
                      equipment_type: result.equipment_type,
                      record_ids: recordId ? [String(recordId)] : [],
                      prepared_dataset_id: result.prepared_dataset_id,
                      dataset_revision_id: result.dataset_revision_id,
                      model_bundle_id: result.model_bundle_id,
                      trigger_reason: "auto_review",
                      decision: result.decision,
                      binary: result.binary_classifier,
                      multiclass: result.multiclass_classifier,
                      consistency: result.consistency,
                      spectral_reference: result.spectral_reference ?? null,
                      l2_results: [
                        {
                          record_id: recordId,
                          screening_result: result.decision?.screening_result,
                          preliminary_fault: result.decision?.preliminary_fault ?? null,
                          route: result.decision?.route,
                          reason_codes: result.decision?.reason_codes || [],
                          binary: result.binary_classifier,
                          multiclass: result.multiclass_classifier,
                          consistency: result.consistency,
                          spectral_reference: result.spectral_reference ?? null,
                        },
                      ],
                    })
                  }}
                >
                  查看 L3 交接数据
                </button>
              ) : null}
            </div>
          </>
        )}
      </section>
    </div>
  )
}
