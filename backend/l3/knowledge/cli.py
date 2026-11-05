"""知识导入 CLI：python -m l3.knowledge.cli import-snapshot"""

from __future__ import annotations

import argparse
import json

from l3.knowledge.import_docs import inspect_sources
from l3.knowledge.snapshot import build_snapshot


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m l3.knowledge.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("inspect")
    p_imp = sub.add_parser("import-snapshot")
    p_imp.add_argument("--force-new", action="store_true")
    args = parser.parse_args(argv)
    if args.cmd == "inspect":
        print(json.dumps(inspect_sources(), ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "import-snapshot":
        body = build_snapshot(force_new=args.force_new)
        print(
            json.dumps(
                {
                    "knowledge_snapshot_id": body["knowledge_snapshot_id"],
                    "item_count": body["item_count"],
                    "duplicate_alias_count": body.get("duplicate_alias_count"),
                    "conflicts": body.get("conflicts"),
                    "pending_documents": body.get("pending_documents"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
