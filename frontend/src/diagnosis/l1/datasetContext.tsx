import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react"
import { L1ApiError, listDatasets, uploadDataset } from "./api"

const DATASET_KEY = "l1.datasetId"

type DatasetContextValue = {
  selectedId: string
  filename: string
  status: string
  addFile: (file: File, options: { encoding: string; delimiter: string }) => Promise<string>
  registerStageReset: (reset: () => void) => () => void
}

const DatasetContext = createContext<DatasetContextValue | null>(null)

export function L1DatasetProvider({ children }: { children: ReactNode }) {
  const [selectedId, setSelectedId] = useState(() => readStorage(DATASET_KEY) ?? "")
  const [filename, setFilename] = useState("")
  const [status, setStatus] = useState("")
  const resetRef = useRef<(() => void) | null>(null)
  const registerStageReset = useCallback((reset: () => void) => {
    resetRef.current = reset
    return () => {
      if (resetRef.current === reset) resetRef.current = null
    }
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    listDatasets(controller.signal)
      .then((items) => {
        if (controller.signal.aborted) return
        const stored = readStorage(DATASET_KEY) ?? ""
        const next = stored && items.some((item) => item.dataset_id === stored) ? stored : items[0]?.dataset_id ?? ""
        setSelectedId(next)
        setFilename(items.find((item) => item.dataset_id === next)?.original_filename ?? "")
        if (next) writeStorage(DATASET_KEY, next)
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) setStatus(messageOf(error))
      })
    return () => controller.abort()
  }, [])

  async function addFile(file: File, options: { encoding: string; delimiter: string }) {
    const lower = file.name.toLowerCase()
    if (lower.endsWith(".xls") && !lower.endsWith(".xlsx")) {
      setStatus("暂不支持旧版 .xls，请另存为 .xlsx 后再上传。")
      throw new Error("暂不支持旧版 .xls，请另存为 .xlsx 后再上传。")
    }
    if (!lower.endsWith(".csv") && !lower.endsWith(".xlsx")) {
      setStatus("请选择 .csv 或 .xlsx 文件。")
      throw new Error("请选择 .csv 或 .xlsx 文件。")
    }
    setStatus("正在上传")
    try {
      const saved = await uploadDataset(file, options, (ratio) => setStatus(`正在上传 ${Math.round(ratio * 100)}%`))
      setSelectedId(saved.dataset_id)
      setFilename(saved.original_filename)
      writeStorage(DATASET_KEY, saved.dataset_id)
      setStatus(saved.duplicate ? "这个文件之前已经上传，已切换到已有数据集。" : "上传完成，原始文件已存档。")
      resetRef.current?.()
      return saved.dataset_id
    } catch (error: unknown) {
      setStatus(messageOf(error))
      throw error
    }
  }

  return <DatasetContext.Provider value={{ selectedId, filename, status, addFile, registerStageReset }}>{children}</DatasetContext.Provider>
}

export function useL1Dataset() {
  const value = useContext(DatasetContext)
  if (!value) throw new Error("useL1Dataset 需要放在 L1DatasetProvider 内")
  return value
}

function readStorage(key: string): string | null {
  try {
    return window.localStorage.getItem(key)
  } catch {
    return null
  }
}

function writeStorage(key: string, value: string) {
  try {
    window.localStorage.setItem(key, value)
  } catch {
    /* 浏览器禁用本地存储时只保持本次页面状态。 */
  }
}

function messageOf(error: unknown): string {
  if (error instanceof L1ApiError) return error.message
  if (error instanceof Error && error.message) return error.message
  return "请求没有完成。"
}
