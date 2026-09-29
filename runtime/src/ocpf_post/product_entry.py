"""Canonical standalone Post-Once executable entrypoint."""
from __future__ import annotations

import sys

from ocpf_post import __version__
from ocpf_post.product_runtime import apply_environment


def main() -> None:
    apply_environment()
    if sys.argv[1:] == ["--version"]:
        print(f"post-once {__version__}")
        return
    from ocpf_post.dispatch import main as dispatch_main

    dispatch_main()


if __name__ == "__main__":
    main()
