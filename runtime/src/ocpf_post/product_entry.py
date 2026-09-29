"""Canonical PostSteward local-runtime executable entrypoint."""
from __future__ import annotations

import sys

from ocpf_post import __version__
from ocpf_post.product_runtime import apply_environment


def main() -> None:
    apply_environment()
    argv = sys.argv[1:]
    if argv == ["--version"]:
        print(f"poststeward {__version__}")
        return
    if argv and argv[0] == "onboard":
        from ocpf_post.poststeward_cloud import main as cloud_main

        raise SystemExit(cloud_main(["onboard", *argv[1:]]))
    if argv and argv[0] == "cloud":
        from ocpf_post.poststeward_cloud import main as cloud_main

        raise SystemExit(cloud_main(argv[1:]))
    from ocpf_post.dispatch import main as dispatch_main

    dispatch_main()


if __name__ == "__main__":
    main()
