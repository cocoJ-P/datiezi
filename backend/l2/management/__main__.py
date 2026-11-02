"""python -m l2.management worker|bootstrap"""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m l2.management")
    sub = parser.add_subparsers(dest="command", required=True)

    worker = sub.add_parser("worker", help="启动训练 Worker")
    worker.add_argument("--once", action="store_true")

    boot = sub.add_parser("bootstrap", help="初始化 DB 并登记本地 Candidate 模型")
    boot.add_argument("--equipment-type", default="main_mill_motor")

    args = parser.parse_args(argv)
    if args.command == "worker":
        from l2.management.worker import main as worker_main

        return worker_main(["--once"] if args.once else [])
    if args.command == "bootstrap":
        from l2.management.service import ManagementService

        service = ManagementService()
        result = service.bootstrap_models(equipment_type=args.equipment_type)
        print(result)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
