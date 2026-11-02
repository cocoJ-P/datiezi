"""独立 Training Worker：轮询任务并调用 Training Core CLI。"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from l2.inference.config import PROJECT_ROOT
from l2.management.audit import append_event
from l2.management.config import load_management_config
from l2.management.database import connect, init_db
from l2.management.jobs import (
    claim_next_job,
    mark_stale_running_jobs,
    update_job_status,
    verify_job_input_hashes,
)
from l2.management.registry import register_bundle

LOG = logging.getLogger("l2.management.worker")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _append_log(log_path: Path, message: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"{_now()} {message}\n")


def _training_config_path(job: dict, fallback: Path) -> Path:
    try:
        payload = json.loads(job.get("training_config_json") or "{}")
        raw = payload.get("config_path")
        if raw:
            path = Path(str(raw))
            if path.is_file():
                return path
    except json.JSONDecodeError:
        pass
    return fallback


def _run_training_subprocess(job: dict, config_path: Path, python_exe: str) -> tuple[int, str, str]:
    cmd = [
        python_exe,
        "-m",
        "l2.training.cli",
        "train",
        "--config",
        str(config_path),
        "--features",
        str(job["features_path"]),
        "--labels",
        str(job["labels_path"]),
        "--output-root",
        str(job["output_root"]),
        "--dataset-id",
        str(job.get("dataset_id") or ""),
        "--dataset-revision-id",
        str(job.get("dataset_revision_id") or ""),
    ]
    try:
        training_meta = json.loads(job.get("training_config_json") or "{}")
        source_type = training_meta.get("source_type")
        if source_type:
            cmd.extend(["--source-kind", str(source_type)])
    except json.JSONDecodeError:
        pass
    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    backend_root = str((PROJECT_ROOT / "backend").resolve())
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = backend_root if not existing else backend_root + os.pathsep + existing
    completed = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(PROJECT_ROOT),
        env=env,
        shell=False,
        check=False,
    )
    return completed.returncode, completed.stdout or "", completed.stderr or ""


def _extract_bundle_path(stdout: str, equipment_type: str, models_root: Path) -> Path | None:
    text = stdout.strip()
    if not text:
        return None
    # CLI 输出 JSON；取最后一个 JSON 对象
    try:
        payload = json.loads(text)
        if isinstance(payload, dict) and payload.get("bundle_path"):
            return Path(str(payload["bundle_path"]))
    except json.JSONDecodeError:
        pass
    # 回退：扫描输出中的 TR- 路径
    for line in reversed(text.splitlines()):
        if "TR-" in line and equipment_type in line:
            candidate = line.strip().strip('"').strip(",")
            path = Path(candidate)
            if path.is_dir():
                return path
    # 再回退：找最新模型目录
    root = models_root / equipment_type
    if not root.is_dir():
        return None
    dirs = [p for p in root.iterdir() if p.is_dir() and p.name.startswith("TR-")]
    if not dirs:
        return None
    return max(dirs, key=lambda p: p.stat().st_mtime)


def process_one_job(connection, config, job: dict) -> None:
    job_id = job["job_id"]
    log_path = Path(job["log_path"])
    _append_log(log_path, f"任务开始 job_id={job_id} dataset={job['dataset_id']}")
    _append_log(log_path, f"features={job['features_path']}")
    _append_log(log_path, f"labels={job['labels_path']}")
    _append_log(log_path, f"config_hash={job['training_config_hash']}")
    try:
        verify_job_input_hashes(job)
        _append_log(log_path, "输入哈希校验通过")
        train_cfg = _training_config_path(job, config.training_config_path)
        _append_log(log_path, f"training_config={train_cfg}")
        code, stdout, stderr = _run_training_subprocess(
            job,
            train_cfg,
            sys.executable,
        )
        if stdout:
            _append_log(log_path, "=== training stdout ===")
            for line in stdout.splitlines()[:500]:
                _append_log(log_path, line)
        if stderr:
            _append_log(log_path, "=== training stderr ===")
            for line in stderr.splitlines()[:500]:
                _append_log(log_path, line)
        if code != 0:
            raise RuntimeError(f"Training Core 退出码 {code}")

        bundle_path = _extract_bundle_path(stdout, job["equipment_type"], config.models_root)
        if bundle_path is None or not bundle_path.is_dir():
            raise RuntimeError("训练成功但未找到新模型包路径。")
        _append_log(log_path, f"发现模型包 {bundle_path}")

        # verify-bundle
        verify_cmd = [
            sys.executable,
            "-m",
            "l2.training.cli",
            "verify-bundle",
            "--bundle",
            str(bundle_path),
        ]
        env = os.environ.copy()
        env.setdefault("PYTHONUTF8", "1")
        backend_root = str((PROJECT_ROOT / "backend").resolve())
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = backend_root if not existing else backend_root + os.pathsep + existing
        verified = subprocess.run(
            verify_cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(PROJECT_ROOT),
            env=env,
            shell=False,
            check=False,
        )
        _append_log(log_path, f"verify-bundle exit={verified.returncode}")
        if verified.stdout:
            for line in verified.stdout.splitlines()[:100]:
                _append_log(log_path, line)
        if verified.returncode != 0:
            raise RuntimeError("verify-bundle 失败，不登记模型。")

        registered = register_bundle(
            connection,
            config,
            bundle_path,
            training_job_id=job_id,
            expected_equipment_type=job["equipment_type"],
            force_status="candidate",
        )
        update_job_status(
            connection,
            job_id,
            new_status="succeeded",
            model_bundle_id=registered["model_bundle_id"],
        )
        append_event(
            connection,
            event_type="training_job_succeeded",
            status="ok",
            message=f"训练成功，生成 Candidate {registered['model_bundle_id']}",
            equipment_type=job["equipment_type"],
            model_bundle_id=registered["model_bundle_id"],
            training_job_id=job_id,
        )
        connection.commit()
        _append_log(log_path, f"任务成功 model_bundle_id={registered['model_bundle_id']}")
    except Exception as exc:  # noqa: BLE001 - Worker 必须记录并标记失败
        connection.rollback()
        connection = connect(config.db_path)
        try:
            update_job_status(
                connection,
                job_id,
                new_status="failed",
                error_code="TRAINING_FAILED",
                error_message=str(exc)[:1000],
            )
            append_event(
                connection,
                event_type="training_job_failed",
                status="failed",
                message=f"训练失败：{exc}",
                equipment_type=job.get("equipment_type"),
                training_job_id=job_id,
            )
            connection.commit()
        finally:
            connection.close()
        _append_log(log_path, f"任务失败：{exc}")
        LOG.exception("任务失败 job_id=%s", job_id)


def run_worker(*, once: bool = False) -> None:
    config = load_management_config()
    init_db(config.db_path)
    connection = connect(config.db_path)
    try:
        interrupted = mark_stale_running_jobs(connection)
        if interrupted:
            connection.commit()
            LOG.warning("已将 %s 个过期 running 任务标记为 interrupted", interrupted)
    finally:
        connection.close()

    LOG.info("Training Worker 启动 db=%s", config.db_path)
    while True:
        connection = connect(config.db_path)
        try:
            job = claim_next_job(connection, worker_pid=os.getpid())
        finally:
            connection.close()
        if job is None:
            if once:
                LOG.info("无排队任务，once 模式退出")
                return
            time.sleep(config.poll_interval_sec)
            continue

        LOG.info("领取任务 %s", job["job_id"])
        connection = connect(config.db_path)
        try:
            process_one_job(connection, config, job)
        finally:
            connection.close()
        if once:
            return


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m l2.management.worker")
    parser.add_argument("--once", action="store_true", help="处理至多一个任务后退出")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    run_worker(once=args.once)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
