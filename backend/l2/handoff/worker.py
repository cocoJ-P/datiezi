"""本地 Handoff Worker：轮询 queued 任务并执行编排。"""

from __future__ import annotations

import argparse
import logging
import threading
import time

from l2.handoff.orchestrator import ORCHESTRATOR

LOG = logging.getLogger("l2.handoff.worker")

_thread: threading.Thread | None = None
_stop = threading.Event()


def process_once() -> bool:
    ORCHESTRATOR.sync_training_progress()
    result = ORCHESTRATOR.process_next()
    return result is not None


def run_loop(*, poll_seconds: float = 2.0, once: bool = False) -> None:
    ORCHESTRATOR.recover_pending()
    if once:
        process_once()
        return
    while not _stop.is_set():
        try:
            worked = process_once()
        except Exception:  # noqa: BLE001
            LOG.exception("handoff worker tick failed")
            worked = False
        _stop.wait(0.2 if worked else poll_seconds)


def start_background_worker(*, poll_seconds: float = 2.0) -> None:
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()

    def _target() -> None:
        LOG.info("L1→L2 handoff worker started")
        run_loop(poll_seconds=poll_seconds, once=False)

    _thread = threading.Thread(target=_target, name="l2-handoff-worker", daemon=True)
    _thread.start()


def stop_background_worker() -> None:
    _stop.set()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m l2.handoff.worker")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    run_loop(poll_seconds=args.poll_seconds, once=args.once)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
