"""L2 Training Core CLI（Windows UTF-8 友好）。"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from l2.training.artifacts import load_model_bundle, predict_bundle, verify_bundle
from l2.training.evaluation import evaluate_pair
from l2.training.pipeline import load_config, run_training
from l2.training.schema import FEATURE_COLUMNS


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )


def _cmd_train(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    if getattr(args, "dataset_id", None):
        config["dataset_id"] = args.dataset_id
    if getattr(args, "dataset_revision_id", None):
        config["dataset_revision_id"] = args.dataset_revision_id
    if getattr(args, "source_kind", None):
        config["source_kind"] = args.source_kind
    result = run_training(
        config=config,
        features_path=args.features,
        labels_path=args.labels,
        output_root=args.output_root,
    )
    print(json.dumps(
        {
            "bundle_path": result["bundle_path"],
            "split_counts": result["split"]["counts"],
            "binary_accuracy": result["metrics"]["binary"]["final"]["accuracy"],
            "multiclass_accuracy": result["metrics"]["multiclass"]["final"]["accuracy"],
            "binary_macro_f1": result["metrics"]["binary"]["final"]["macro_f1"],
            "multiclass_macro_f1": result["metrics"]["multiclass"]["final"]["macro_f1"],
            "binary_log_loss": result["metrics"]["binary"]["final"]["log_loss"],
            "multiclass_log_loss": result["metrics"]["multiclass"]["final"]["log_loss"],
            "binary_brier": result["metrics"]["binary"]["final"]["brier_score"],
            "multiclass_brier": result["metrics"]["multiclass"]["final"]["brier_score"],
            "conflict_count": result["metrics"]["consistency"]["conflict_count"],
            "calibration": result["calibration"],
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    report = verify_bundle(args.bundle)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def _cmd_evaluate(args: argparse.Namespace) -> int:
    import numpy as np

    from l2.training.data_adapter import load_csv_snapshot
    from l2.training.schema import FAULT_TYPE_COLUMN, RECORD_ID_COLUMN, fault_to_binary, multiclass_index

    bundle = load_model_bundle(args.bundle)
    snapshot = load_csv_snapshot(args.features, args.labels)
    split_ids = json.loads((bundle.path / "split_ids.json").read_text(encoding="utf-8"))
    test_ids = set(split_ids["test_ids"])
    frame = snapshot.frame[snapshot.frame[RECORD_ID_COLUMN].astype(str).isin(test_ids)].copy()
    if frame.empty:
        raise ValueError("评测数据中没有与模型包 test_ids 匹配的记录。")
    records = frame[list(FEATURE_COLUMNS)].to_dict(orient="records")
    preds = predict_bundle(bundle, records)
    y_multi = [multiclass_index(str(v)) for v in frame[FAULT_TYPE_COLUMN]]
    y_bin = [0 if fault_to_binary(str(v)) == "正常" else 1 for v in frame[FAULT_TYPE_COLUMN]]
    binary_final = [[item["binary_proba"]["正常"], item["binary_proba"]["非正常"]] for item in preds]
    multi_final = [[item["multiclass_proba"][name] for name in bundle.label_map["multiclass_labels"]] for item in preds]
    # evaluate 命令复用最终输出概率；对照字段填充同一矩阵。
    bf = np.asarray(binary_final, dtype=float)
    mf = np.asarray(multi_final, dtype=float)
    metrics = evaluate_pair(
        record_ids=frame[RECORD_ID_COLUMN].astype(str).tolist(),
        y_bin_true=np.asarray(y_bin, dtype=int),
        y_multi_true=np.asarray(y_multi, dtype=int),
        binary_raw=bf,
        binary_final=bf,
        multi_raw=mf,
        multi_final=mf,
    )
    print(json.dumps({"bundle": str(bundle.path), "metrics": metrics}, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m l2.training.cli", description="L2 Training Core v1")
    sub = parser.add_subparsers(dest="command", required=True)

    train = sub.add_parser("train", help="执行完整训练流水线")
    train.add_argument("--config", required=True, help="YAML 配置路径")
    train.add_argument("--features", required=True, help="features.csv 路径")
    train.add_argument("--labels", required=True, help="labels.csv 路径")
    train.add_argument("--output-root", required=True, help="模型输出根目录")
    train.add_argument("--dataset-id", default=None, help="覆盖配置中的 dataset_id（如 PED-*）")
    train.add_argument("--dataset-revision-id", default=None, help="覆盖配置中的 dataset_revision_id")
    train.add_argument("--source-kind", default=None, help="数据来源标记，如 l1_prepared / csv_pair")
    train.set_defaults(func=_cmd_train)

    evaluate = sub.add_parser("evaluate", help="读取模型包并在给定数据上评测")
    evaluate.add_argument("--bundle", required=True, help="模型包目录")
    evaluate.add_argument("--features", required=True, help="features.csv")
    evaluate.add_argument("--labels", required=True, help="labels.csv")
    evaluate.set_defaults(func=_cmd_evaluate)

    verify = sub.add_parser("verify-bundle", help="检查模型包完整性与基础预测")
    verify.add_argument("--bundle", required=True, help="模型包目录")
    verify.set_defaults(func=_cmd_verify)
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    _configure_logging()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except Exception as exc:  # noqa: BLE001 - CLI 顶层错误出口
        logging.getLogger("l2.training").exception("训练失败")
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
