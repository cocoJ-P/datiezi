import { useEffect, useRef, useState } from "react"
import type { DatasetDetail } from "./types"

const STATUS_LABEL: Record<string, string> = {
  parsed: "已索引",
  parsed_with_errors: "已索引，含异常记录",
  failed: "解析失败",
}

export function DatasetReport({ detail }: { detail: DatasetDetail | null }) {
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLSpanElement>(null)

  useEffect(() => {
    if (!open) return
    function onPointer(event: MouseEvent) {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false)
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false)
    }
    document.addEventListener("mousedown", onPointer)
    document.addEventListener("keydown", onKey)
    return () => {
      document.removeEventListener("mousedown", onPointer)
      document.removeEventListener("keydown", onKey)
    }
  }, [open])

  const status = detail ? (STATUS_LABEL[detail.parse_status] ?? detail.parse_status) : ""

  return (
    <span className="l1-report" ref={rootRef}>
      <button type="button" aria-expanded={open} aria-haspopup="dialog" disabled={!detail} onClick={() => setOpen((current) => !current)}>
        报告
        <span className="l1-report-caret" aria-hidden="true" />
      </button>
      {open && detail ? (
        <span className="l1-report-menu" role="dialog" aria-label="数据集报告">
          <span className="l1-report-head">
            <strong>{detail.original_filename}</strong>
            <span className={`l1-report-status is-${detail.parse_status}`}>{status}</span>
          </span>
          <span className="l1-report-stats">
            <span>
              <b>{detail.row_count}</b>
              <small>行</small>
            </span>
            <span>
              <b>{detail.column_count}</b>
              <small>列</small>
            </span>
            <span>
              <b>{detail.cell_count}</b>
              <small>单元格</small>
            </span>
          </span>
          <span className="l1-report-fields">
            <span>
              <small>DatasetID</small>
              <b>{detail.dataset_id}</b>
            </span>
            <span>
              <small>SHA-256</small>
              <b>{detail.sha256}</b>
            </span>
          </span>
          <a className="l1-report-download" href={detail.file_url}>
            下载原件
          </a>
          {detail.warnings.length > 0 || detail.error || !detail.storage_consistent ? (
            <span className="l1-report-notes">
              {detail.warnings.map((item) => (
                <span key={item.message}>{item.message}</span>
              ))}
              {detail.error ? <span className="is-error">{detail.error.message} 原始文件仍可下载。</span> : null}
              {!detail.storage_consistent ? <span className="is-error">原始文件与索引不一致。</span> : null}
            </span>
          ) : null}
        </span>
      ) : null}
    </span>
  )
}
