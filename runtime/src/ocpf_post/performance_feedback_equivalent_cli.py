from __future__ import annotations

import json
import sys

from ocpf_post.performance_feedback_equivalent import build


def main() -> None:
    args = set(sys.argv[1:])
    unknown = args - {"--apply", "--disable", "--enable"}
    if unknown or ("--disable" in args and "--enable" in args):
        raise SystemExit("Usage: ocpf-post performance feedback [--apply] [--disable|--enable]")
    enable = False if "--disable" in args else True if "--enable" in args else None
    print(json.dumps(build(apply="--apply" in args, enable=enable), indent=2, ensure_ascii=False))
