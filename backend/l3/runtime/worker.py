"""L3 Analysis Worker：轮询 queued 任务并执行。"""

from __future__ import annotations

import argparse
import logging
import threading

from l3.runtime.jobs import compensate_missing_analysis_runs, execute_analysis_run
from l3.runtime.store import ANALYSIS_STORE

LOG = logging.getLogger("l3.worker")

_thread: threading.Thread | None = None
_stop = threading.Event()


def process_once() -> bool:
    queued = ANALYSIS_STORE.list_queued(limit=1)
    if not queued:
        return False
    run = queued[0]
    LOG.info("executing analysis_run=%s records=%s", run["analysis_run_id"], run["selected_record_count"])
    try:
        execute_analysis_run(run["analysis_run_id"])
    except Exception as exc:  # noqa: BLE001 - 兜底出队，防止同一任务无限重试
        from datetime import datetime, timezone

        LOG.exception("analysis_run=%s crashed", run["analysis_run_id"])
        ANALYSIS_STORE.update_run(
            run["analysis_run_id"],
            status="failed",
            error_summary=f"worker_crash: {exc}"[:2000],
            finished_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        )
    return True


def run_loop(*, poll_seconds: float = 2.0, once: bool = False) -> None:
    compensate_missing_analysis_runs()
    for run in ANALYSIS_STORE.list_incomplete_for_compensation():
        if run["status"] == "running":
            ANALYSIS_STORE.update_run(run["analysis_run_id"], status="interrupted")
    if once:
        process_once()
        return
    while not _stop.is_set():
        try:
            worked = process_once()
        except Exception:  # noqa: BLE001
            LOG.exception("l3 worker tick failed")
            worked = False
        _stop.wait(0.2 if worked else poll_seconds)


def start_background_worker(*, poll_seconds: float = 2.0) -> None:
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()

    def _target() -> None:
        LOG.info("L3 analysis worker started")
        run_loop(poll_seconds=poll_seconds, once=False)

    _thread = threading.Thread(target=_target, name="l3-analysis-worker", daemon=True)
    _thread.start()


def stop_background_worker() -> None:
    _stop.set()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m l3.runtime.worker")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    run_loop(poll_seconds=args.poll_seconds, once=args.once)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
