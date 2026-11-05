"""python -m l2.handoff worker"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] != "worker":
        print("用法: python -m l2.handoff worker [--once] [--poll-seconds N]")
        return 1
    from l2.handoff.worker import main as worker_main

    return worker_main(args[1:])


if __name__ == "__main__":
    raise SystemExit(main())
