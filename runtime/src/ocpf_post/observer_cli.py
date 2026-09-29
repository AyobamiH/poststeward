from __future__ import annotations

import argparse
import json
import os

from ocpf_post.execution_observer import build_execution_snapshot
from ocpf_post.observer_server import DEFAULT_PORT, serve


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ocpf-post console",
        description="Serve the loopback-only, read-only post-once observability console.",
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Loopback TCP port (default {DEFAULT_PORT})")
    parser.add_argument(
        "--snapshot",
        action="store_true",
        help="Print one local read-only JSON snapshot and exit instead of serving HTTP",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    # Prevent read-only Git observations from opportunistically refreshing the
    # index or taking optional locks in the live checkout.
    os.environ.setdefault("GIT_OPTIONAL_LOCKS", "0")
    if args.snapshot:
        print(json.dumps(build_execution_snapshot(), indent=2, ensure_ascii=False, allow_nan=False))
        return
    serve(port=args.port)


if __name__ == "__main__":
    main()
