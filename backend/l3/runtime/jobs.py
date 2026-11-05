"""L3 AnalysisRun 创建、执行与自动衔接。"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from l2.handoff.store import HANDOFF_STORE, HandoffStore
from l2.inference.config import PROJECT_ROOT
from l2.routing_policy import ROUTING_POLICY_VERSION
from l3 import ANALYZER_CONFIG_VERSION
from l3.core.input_adapter import InputAdapter
from l3.core.registry import DEFAULT_REGISTRY, AnalyzerRegistry
from l3.evidence.builder import build_evidence_bundle
from l3.runtime.store import ANALYSIS_STORE, AnalysisStore

LOG = logging.getLogger("l3.jobs")

BATCH_SIZE = 100


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def analyzer_config_hash() -> str:
    raw = f"{ANALYZER_CONFIG_VERSION}|{ROUTING_POLICY_VERSION}|stat+thr+mech+review"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def content_key_for(diagnosis_run_id: str, dataset_revision_id: str) -> str:
    raw = f"{diagnosis_run_id}|{dataset_revision_id}|{analyzer_config_hash()}|{ROUTING_POLICY_VERSION}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _result_root() -> Path:
    path = PROJECT_ROOT / "data" / "runtime" / "l3_analysis_runs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def enqueue_from_diagnosis_run(
    diagnosis_run_id: str,
    *,
    handoff_store: HandoffStore | None = None,
    analysis_store: AnalysisStore | None = None,
    force_new: bool = False,
    record_ids: list[str] | None = None,
    manual: bool = False,
) -> dict[str, Any]:
    """根据 DiagnosisRun 按分流策略创建或复用 AnalysisRun。"""
    hstore = handoff_store or HANDOFF_STORE
    astore = analysis_store or ANALYSIS_STORE
    run = hstore.get_diagnosis_run(diagnosis_run_id)
    if run.get("status") not in {"completed", "completed_with_errors"}:
        raise RuntimeError(f"DiagnosisRun 尚未完成：{run.get('status')}")

    # 入队前校验 PED 仍可解析，避免创建永远失败的排队任务。
    try:
        from l2.prepared.catalog import get_published_dataset

        get_published_dataset(str(run["prepared_dataset_id"]))
    except KeyError as exc:
        raise RuntimeError(
            f"PED 不存在或已失效：{run.get('prepared_dataset_id')}，无法创建 L3 AnalysisRun。"
        ) from exc

    if manual and record_ids is not None:
        # 手动请求允许指定任意已有记录（含 NORMAL），但仍需存在于 DiagnosisRun
        all_page = hstore.list_diagnosis_records(diagnosis_run_id, offset=0, limit=10_000_000)
        by_id = {str(i["record_id"]): i for i in all_page["items"]}
        targets = [by_id[str(rid)] for rid in record_ids if str(rid) in by_id]
    else:
        targets = hstore.iter_l3_target_records(diagnosis_run_id)
        if record_ids is not None:
            wanted = set(str(x) for x in record_ids)
            targets = [t for t in targets if str(t["record_id"]) in wanted]

    if not targets:
        return {
            "skipped": True,
            "reason": "no_l3_targets",
            "source_diagnosis_run_id": diagnosis_run_id,
            "selected_record_count": 0,
        }

    key = content_key_for(diagnosis_run_id, str(run.get("dataset_revision_id") or ""))
    analysis_run, created = astore.create_or_get_run(
        content_key=key,
        source_diagnosis_run_id=diagnosis_run_id,
        prepared_dataset_id=str(run["prepared_dataset_id"]),
        dataset_revision_id=str(run["dataset_revision_id"]),
        equipment_type=str(run["equipment_type"]),
        model_bundle_id=run.get("model_bundle_id"),
        selected_record_count=len(targets),
        analyzer_config_hash=analyzer_config_hash(),
        routing_policy_version=ROUTING_POLICY_VERSION,
        force_new=force_new,
    )
    return {
        "skipped": False,
        "created": created,
        "reused": not created,
        "manual": manual,
        **analysis_run,
    }


def execute_analysis_run(
    analysis_run_id: str,
    *,
    handoff_store: HandoffStore | None = None,
    analysis_store: AnalysisStore | None = None,
    registry: AnalyzerRegistry | None = None,
    batch_size: int = BATCH_SIZE,
) -> dict[str, Any]:
    hstore = handoff_store or HANDOFF_STORE
    astore = analysis_store or ANALYSIS_STORE
    reg = registry or DEFAULT_REGISTRY
    run = astore.get_run(analysis_run_id)
    if run["status"] in {"succeeded", "partial", "failed"}:
        return run

    def _fail(message: str) -> dict[str, Any]:
        LOG.error("analysis_run=%s failed setup: %s", analysis_run_id, message)
        return astore.update_run(
            analysis_run_id,
            status="failed",
            started_at=run.get("started_at") or _now_iso(),
            finished_at=_now_iso(),
            error_summary=message[:2000],
            processed_record_count=int(run.get("processed_record_count") or 0),
            succeeded_record_count=int(run.get("succeeded_record_count") or 0),
            failed_record_count=int(run.get("failed_record_count") or 0),
            inconclusive_record_count=int(run.get("inconclusive_record_count") or 0),
        )

    try:
        diagnosis = hstore.get_diagnosis_run(run["source_diagnosis_run_id"])
    except KeyError:
        return _fail(f"找不到 DiagnosisRun：{run.get('source_diagnosis_run_id')}")

    targets = hstore.iter_l3_target_records(run["source_diagnosis_run_id"])
    # 若任务选定数量与当前策略不一致，仍以创建时 selected 为准：用当前目标全集执行
    adapter = InputAdapter(hstore)
    try:
        ped_index = adapter.load_ped_index(
            prepared_dataset_id=str(run["prepared_dataset_id"]),
            equipment_type=str(run["equipment_type"]),
        )
    except KeyError as exc:
        return _fail(
            f"PED 不存在或已从目录移除：{run.get('prepared_dataset_id')}（{exc}）。"
            "请使用当前仍有效的 DiagnosisRun / PED 重新入队，或重新发布数据集。"
        )
    except FileNotFoundError as exc:
        return _fail(f"PED 快照文件缺失：{run.get('prepared_dataset_id')}（{exc}）")
    except Exception as exc:  # noqa: BLE001 - 启动失败必须出队，避免 worker 死循环
        return _fail(f"加载 PED / 建立索引失败：{exc}")

    astore.update_run(analysis_run_id, status="running", started_at=run.get("started_at") or _now_iso())
    out_dir = _result_root() / analysis_run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    succeeded = 0
    failed = 0
    inconclusive = 0
    processed = 0
    error_samples: list[str] = []

    for start in range(0, len(targets), batch_size):
        chunk = targets[start : start + batch_size]
        rows_to_save: list[dict[str, Any]] = []
        for row in chunk:
            try:
                context = adapter.build_context(
                    analysis_run_id=analysis_run_id,
                    diagnosis_run=diagnosis,
                    record_row=row,
                    ped_index=ped_index,
                )
                analyzer_results, review = reg.run_all(context)
                bundle = build_evidence_bundle(context, analyzer_results, review)
                status = "ok"
                if review.get("evidence_sufficiency") == "insufficient":
                    status = "inconclusive"
                    inconclusive += 1
                else:
                    succeeded += 1
                # 落盘单条证据
                (out_dir / f"{context.record_id}.json").write_text(
                    json.dumps(bundle, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                rows_to_save.append(
                    {
                        "record_id": context.record_id,
                        "equipment_id": context.equipment_id,
                        "status": status,
                        "trigger_reason": context.trigger_reason,
                        "screening_result": context.screening_result,
                        "evidence": bundle,
                    }
                )
            except Exception as exc:  # noqa: BLE001 - 单条失败不污染整批
                failed += 1
                msg = str(exc)
                if len(error_samples) < 5:
                    error_samples.append(f"{row.get('record_id')}: {msg}")
                LOG.exception("L3 record failed run=%s record=%s", analysis_run_id, row.get("record_id"))
                rows_to_save.append(
                    {
                        "record_id": str(row.get("record_id")),
                        "equipment_id": None,
                        "status": "failed",
                        "trigger_reason": None,
                        "screening_result": row.get("screening_result"),
                        "evidence": {"error": msg},
                        "error_message": msg,
                    }
                )
        astore.save_records(analysis_run_id, rows_to_save)
        processed = succeeded + failed + inconclusive
        astore.update_run(
            analysis_run_id,
            status="running",
            processed_record_count=processed,
            succeeded_record_count=succeeded,
            failed_record_count=failed,
            inconclusive_record_count=inconclusive,
            result_dir=str(out_dir),
        )

    if failed and succeeded == 0 and inconclusive == 0:
        final = "failed"
    elif failed:
        final = "partial"
    else:
        final = "succeeded"

    return astore.update_run(
        analysis_run_id,
        status=final,
        processed_record_count=processed,
        succeeded_record_count=succeeded,
        failed_record_count=failed,
        inconclusive_record_count=inconclusive,
        result_dir=str(out_dir),
        finished_at=_now_iso(),
        error_summary="; ".join(error_samples) if error_samples else None,
    )


def compensate_missing_analysis_runs(
    *,
    handoff_store: HandoffStore | None = None,
    analysis_store: AnalysisStore | None = None,
) -> list[dict[str, Any]]:
    """扫描已完成 DiagnosisRun，补登记遗漏的 L3 任务。"""
    hstore = handoff_store or HANDOFF_STORE
    astore = analysis_store or ANALYSIS_STORE
    created: list[dict[str, Any]] = []
    # 使用 handoff DB 中已完成的 diagnosis runs
    from l2.handoff.database import connect as hconnect

    connection = hconnect(hstore.db_path)
    try:
        rows = connection.execute(
            """
            SELECT diagnosis_run_id FROM diagnosis_runs
            WHERE status IN ('completed', 'completed_with_errors')
            ORDER BY finished_at DESC
            LIMIT 50
            """
        ).fetchall()
    finally:
        connection.close()

    for row in rows:
        diagnosis_run_id = row["diagnosis_run_id"]
        existing = astore.get_run_by_diagnosis(diagnosis_run_id)
        if existing and existing["status"] in {"queued", "running", "succeeded", "partial", "interrupted"}:
            continue
        targets = hstore.iter_l3_target_records(diagnosis_run_id)
        if not targets:
            continue
        try:
            body = enqueue_from_diagnosis_run(diagnosis_run_id, handoff_store=hstore, analysis_store=astore)
            if not body.get("skipped"):
                created.append(body)
        except RuntimeError as exc:
            # 常见：历史 DiagnosisRun 绑定的 PED 已被替换/删除
            LOG.warning("compensate skip diagnosis_run=%s: %s", diagnosis_run_id, exc)
        except Exception:  # noqa: BLE001
            LOG.exception("compensate enqueue failed diagnosis_run=%s", diagnosis_run_id)
    return created
