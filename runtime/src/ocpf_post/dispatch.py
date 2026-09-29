from __future__ import annotations

import os
import sys

from ocpf_post.state import config_dir, provider_token_file


def _bootstrap_x_runtime() -> None:
    token_file = provider_token_file("x")
    if token_file.exists() and os.environ.get("OCPF_POST_PREFER_ENV_TOKEN", "0") != "1":
        os.environ.pop("X_USER_ACCESS_TOKEN", None)
        os.environ.pop("X_OAUTH2_ACCESS_TOKEN", None)
        os.environ.pop("X_REFRESH_TOKEN", None)

    if not os.environ.get("X_CLIENT_SECRET"):
        secret_file = config_dir() / "x-client-secret"
        if secret_file.is_file():
            try:
                secret = secret_file.read_text(encoding="utf-8").strip()
            except OSError:
                secret = ""
            if secret:
                os.environ["X_CLIENT_SECRET"] = secret


def _catalogue_help(argv: list[str]) -> bool:
    if not argv:
        return False
    if argv[0] in {"-h", "--help"}:
        from ocpf_post.cli_catalog import render_text

        print(render_text([]), end="")
        return True
    if argv[0] != "help":
        return False
    from ocpf_post.cli_catalog import render_json, render_text

    json_mode = "--json" in argv[1:]
    scope = [part for part in argv[1:] if part != "--json"]
    try:
        output = render_json(scope) if json_mode else render_text(scope)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(2)
    print(output, end="")
    return True


def main() -> None:
    argv = sys.argv[1:]
    if _catalogue_help(argv):
        return

    command = argv[0] if argv else ""
    if command == "setup":
        from ocpf_post.setup_cli import main as setup_main

        sys.argv = [f"{sys.argv[0]} setup", *sys.argv[2:]]
        setup_main()
        return

    _bootstrap_x_runtime()

    if command == "console":
        from ocpf_post.observer_cli import main as console_main

        sys.argv = [f"{sys.argv[0]} console", *sys.argv[2:]]
        console_main()
        return
    if command == "health":
        from ocpf_post.health_attested import main as health_main

        sys.argv = [f"{sys.argv[0]} health", *sys.argv[2:]]
        health_main()
        return
    if command in {"threads", "linkedin"}:
        from ocpf_post.provider_runner import main as provider_main

        provider_main()
        return
    if command in {"schedule", "run-due"}:
        from ocpf_post.scheduler_cli import main as scheduler_main

        scheduler_main()
        return
    if command == "registry":
        from ocpf_post.registry_cli import main as registry_main

        sys.argv = [f"{sys.argv[0]} registry", *sys.argv[2:]]
        registry_main()
        return
    if command == "performance":
        if len(argv) > 1 and argv[1] == "feedback":
            from ocpf_post.performance_feedback_equivalent_cli import main as feedback_main

            sys.argv = [f"{sys.argv[0]} performance feedback", *sys.argv[3:]]
            feedback_main()
            return
        from ocpf_post.performance_cli import main as performance_main

        sys.argv = [f"{sys.argv[0]} performance", *sys.argv[2:]]
        performance_main()
        return
    if command == "portfolio":
        if len(argv) > 1 and argv[1] == "policy":
            from ocpf_post.portfolio_policy_cli import main as policy_main

            sys.argv = [f"{sys.argv[0]} portfolio policy", *sys.argv[3:]]
            policy_main()
            return
        from ocpf_post.portfolio_cli import main as portfolio_main

        sys.argv = [f"{sys.argv[0]} portfolio", *sys.argv[2:]]
        portfolio_main()
        return
    if command == "replenish":
        from ocpf_post.replenisher_cli import main as replenisher_main

        sys.argv = [f"{sys.argv[0]} replenish", *sys.argv[2:]]
        replenisher_main()
        return

    from ocpf_post.cli import main as cli_main

    cli_main()


if __name__ == "__main__":
    main()
