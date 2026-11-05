from __future__ import annotations

import threading
import time

from l2.management import worker as training_worker


def test_start_background_worker_is_idempotent(monkeypatch, tmp_path):
    monkeypatch.setenv("L2_MANAGEMENT_DB", str(tmp_path / "mgmt.sqlite3"))
    monkeypatch.setenv("L2_TRAINING_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("L2_MODELS_ROOT", str(tmp_path / "models"))
    monkeypatch.setenv("L2_WORKER_POLL_INTERVAL", "0.05")

    training_worker.stop_background_worker()
    # 等待可能残留的旧线程观察到 stop
    time.sleep(0.05)
    training_worker._thread = None
    training_worker._stop = threading.Event()

    training_worker.start_background_worker()
    first = training_worker._thread
    assert first is not None and first.is_alive()
    training_worker.start_background_worker()
    assert training_worker._thread is first

    training_worker.stop_background_worker()
    first.join(timeout=2.0)
    assert not first.is_alive()


def test_run_worker_once_exits_without_jobs(monkeypatch, tmp_path):
    monkeypatch.setenv("L2_MANAGEMENT_DB", str(tmp_path / "mgmt.sqlite3"))
    monkeypatch.setenv("L2_TRAINING_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("L2_MODELS_ROOT", str(tmp_path / "models"))
    training_worker.run_worker(once=True)
