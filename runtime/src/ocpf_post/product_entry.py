"""Canonical PostSteward local-runtime executable entrypoint."""
from __future__ import annotations

import json
import sys

from ocpf_post import __version__
from ocpf_post.product_runtime import apply_environment


def main() -> None:
    apply_environment()
    argv = sys.argv[1:]
    if argv == ["--version"]:
        print(f"poststeward {__version__}")
        return
    if argv in (["--help"], ["-h"]):
        from ocpf_post.cli_catalog import render_text

        print(
            "PostSteward local runtime\n\n"
            "Product commands:\n"
            "  onboard     Pair this machine to the owner workspace\n"
            "  configure   Bind hosted X/Threads/LinkedIn identities to Fresh local state\n"
            "  cloud       Inspect pairing, providers, executor heartbeat and remote MCP info\n"
            "\nRuntime commands:\n"
        )
        print(render_text([]), end="")
        return
    if argv == ["help", "--json"]:
        from ocpf_post.cli_catalog import catalogue

        value = catalogue()
        value.update(
            {
                "product": "poststeward",
                "product_commands": [
                    {
                        "path": "onboard",
                        "consequence": "LOCAL_STATE_WRITE",
                        "summary": "Pair this installation to a PostSteward owner workspace.",
                    },
                    {
                        "path": "configure",
                        "consequence": "LOCAL_STATE_WRITE",
                        "safe_form": "omit --apply",
                        "summary": "Bind hosted X, Threads and LinkedIn identities to Fresh local project/campaign state.",
                    },
                    {
                        "path": "cloud status",
                        "consequence": "READ_ONLY",
                        "summary": "Inspect cloud bindings and executor generation.",
                    },
                    {
                        "path": "cloud heartbeat",
                        "consequence": "LOCAL_STATE_WRITE",
                        "summary": "Renew the exact local executor lease; never changes executor generation.",
                    },
                    {
                        "path": "cloud mcp",
                        "consequence": "READ_ONLY",
                        "summary": "Show the remote MCP endpoint without printing credentials.",
                    },
                ],
                "authority_model": (
                    "Provider OAuth/admin authority stays human-owned in PostSteward Cloud; "
                    "the local runtime requires an exact cloud executor generation before unattended activation."
                ),
            }
        )
        print(json.dumps(value, indent=2, ensure_ascii=False))
        return
    if argv and argv[0] == "onboard":
        from ocpf_post.poststeward_cloud import main as cloud_main

        raise SystemExit(cloud_main(["onboard", *argv[1:]]))
    if argv and argv[0] == "cloud":
        from ocpf_post.poststeward_cloud import main as cloud_main

        raise SystemExit(cloud_main(argv[1:]))
    if argv and argv[0] == "configure":
        from ocpf_post.poststeward_onboarding import main as onboarding_main

        raise SystemExit(onboarding_main(argv[1:]))
    from ocpf_post.dispatch import main as dispatch_main

    dispatch_main()


if __name__ == "__main__":
    main()
