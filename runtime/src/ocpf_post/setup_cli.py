"""Terminal setup UX for fresh and explore-only bootstrap flows."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ocpf_post.setup_engine import (
    HARD_CEILING,
    PACE_PROFILES,
    SetupEngine,
    SetupEngineError,
    detect_timezone,
)
from ocpf_post.setup_store import SetupStoreError


def _emit(value: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False))
        return
    session = value["session"]
    print("Post-Once Setup & Recovery")
    print(f"Mode:      {value['mode']}")
    print(f"Stage:     {session['stage']}")
    print(f"Revision:  {session['revision']}")
    if value.get("operation"):
        print(f"Operation: {value['operation']['operation_id']}")
    if value.get("installation"):
        print(f"Install:   {value['installation']['installation_id']}")
    config = value.get("configuration")
    if config:
        print(f"Timezone:  {config['timezone']}")
        print(
            "Pace:      "
            f"{config['pace_profile']} ({config['daily_originals']} logical originals/day)"
        )
        print(f"Ceiling:   {config['hard_ceiling']} logical publications/account/day")
    print(f"Publishing authority: {'YES' if value.get('publishing_authority') else 'NO'}")
    print(f"Automation enabled: {'YES' if value.get('automation_enabled') else 'NO'}")
    if value.get("blockers"):
        print("Still needed:")
        for blocker in value["blockers"]:
            print(f"  - {blocker}")
    if value.get("next_actions"):
        print("Next:")
        for action in value["next_actions"]:
            print(f"  - {action}")


def _error(exc: Exception, *, as_json: bool) -> None:
    code = getattr(exc, "code", "setup.error")
    if as_json:
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "blocked",
                    "code": code,
                    "message": str(exc),
                    "publishing_authority": False,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
    else:
        print(f"Setup blocked [{code}]: {exc}", file=sys.stderr)
    raise SystemExit(3)


def _engine(args: argparse.Namespace) -> SetupEngine:
    return SetupEngine(getattr(args, "workspace", None))


def cmd_setup_start(args: argparse.Namespace) -> None:
    try:
        value = _engine(args).start(
            args.mode,
            operator_label=args.operator_label,
            timezone=args.timezone,
            pace=args.pace,
            daily_originals=args.daily_originals,
            machine_label=args.machine_label,
        )
    except (SetupEngineError, SetupStoreError, ValueError) as exc:
        _error(exc, as_json=args.json)
    _emit(value, as_json=args.json)


def cmd_setup_status(args: argparse.Namespace) -> None:
    try:
        value = _engine(args).status(args.session_id)
    except (SetupEngineError, SetupStoreError, ValueError) as exc:
        _error(exc, as_json=args.json)
    _emit(value, as_json=args.json)


def cmd_setup_resume(args: argparse.Namespace) -> None:
    try:
        value = _engine(args).resume(
            session_id=args.session_id,
            timezone=args.timezone,
            pace=args.pace,
            daily_originals=args.daily_originals,
        )
    except (SetupEngineError, SetupStoreError, ValueError) as exc:
        _error(exc, as_json=args.json)
    _emit(value, as_json=args.json)


def _load_json_file(path: str) -> Any:
    source = Path(path).expanduser()
    if not source.is_file():
        raise SetupEngineError("setup.input.missing", f"Input file does not exist: {source}")
    try:
        return json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SetupEngineError("setup.input.invalid_json", f"Input file is not valid JSON: {source}") from exc


def _observe_fresh_provider(provider: str, expected_account_id: str) -> dict[str, Any]:
    from ocpf_post.provider_readiness import observe_linkedin, observe_threads, observe_x
    from ocpf_post.providers import get_provider

    name = str(provider or "").strip().lower()
    expected = str(expected_account_id or "").strip()
    if name not in {"x", "threads", "linkedin"} or not expected:
        raise SetupEngineError(
            "setup.fresh.expected_identity_invalid",
            "Fresh verification requires a supported provider and explicit immutable account ID",
        )
    client = get_provider(name, **({"actor_urn": expected} if name == "linkedin" else {}))
    if name == "x":
        return observe_x(client, expected_identity=expected)
    if name == "threads":
        return observe_threads(client, expected_identity=expected)
    return observe_linkedin(
        client,
        expected_identity=expected,
        actor_urn=expected,
    )


def cmd_setup_verify_fresh(args: argparse.Namespace) -> None:
    try:
        readiness = _observe_fresh_provider(args.provider, args.expected_account_id)
        value = _engine(args).verify_fresh(
            [readiness],
            [{"provider": args.provider, "account_id": args.expected_account_id}],
            session_id=args.session_id,
        )
    except (SetupEngineError, SetupStoreError, ValueError) as exc:
        _error(exc, as_json=args.json)
    _emit(value, as_json=args.json)


def cmd_setup_connect_x(args: argparse.Namespace) -> None:
    try:
        from ocpf_post.beta_onboarding import (
            BetaOnboardingError,
            prompt_and_connect_x,
        )

        value = prompt_and_connect_x(
            client_id=args.client_id,
            redirect_uri=args.redirect_uri,
            prompt_secret=args.prompt_client_secret,
            client_secret_file=args.client_secret_file,
        )
    except (BetaOnboardingError, SetupEngineError, SetupStoreError, ValueError) as exc:
        _error(exc, as_json=False)

    print("Post-Once X connection")
    print(f"Status:  {value['status']}")
    print(f"Account: {value['display']} ({value['account_id']})")
    print(f"Secret persisted: {'YES' if value['client_secret_persisted'] else 'NO'}")
    print("Provider consequence attempted: NO")
    print("Next: post-once setup onboard-x")


def _onboarding_text(args: argparse.Namespace) -> str:
    if args.text is not None:
        return str(args.text)
    if args.text_file is not None:
        source = Path(args.text_file).expanduser()
        if not source.is_file():
            raise SetupEngineError("setup.onboard_x.text_file_missing", f"Text file does not exist: {source}")
        try:
            return source.read_text(encoding="utf-8").rstrip("\n")
        except (OSError, UnicodeDecodeError) as exc:
            raise SetupEngineError(
                "setup.onboard_x.text_file_unreadable",
                f"Could not read UTF-8 campaign text: {source}",
            ) from exc
    if args.stdin:
        return sys.stdin.read().rstrip("\n")
    raise SetupEngineError("setup.onboard_x.text_required", "One campaign text source is required")


def cmd_setup_onboard_x(args: argparse.Namespace) -> None:
    try:
        from ocpf_post.beta_onboarding import BetaOnboardingError, onboard_x

        value = onboard_x(
            project_id=args.project,
            project_label=args.label,
            campaign_text=_onboarding_text(args),
            campaign_id=args.campaign_id,
            source_id=args.source_id,
            session_id=args.session_id,
            workspace=getattr(args, "workspace", None),
            apply=args.apply,
        )
    except (BetaOnboardingError, SetupEngineError, SetupStoreError, ValueError) as exc:
        _error(exc, as_json=args.json)

    if args.json:
        print(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False))
        return
    print("Post-Once X beta onboarding")
    print(f"Status:  {value['status']}")
    if value.get("account_id"):
        print(f"Account: {value.get('display')} ({value['account_id']})")
    campaign = value.get("campaign") or {}
    if campaign.get("campaign"):
        print(f"Campaign: {campaign['campaign']} (manual-only)")
    print(f"Publishing authority: {'YES' if value.get('publishing_authority') else 'NO'}")
    print(f"Automation enabled: {'YES' if value.get('automation_enabled') else 'NO'}")
    if value.get("next_action"):
        print(f"Next: {value['next_action']}")
    for action in value.get("next_actions", []):
        print(f"Next: {action}")


def cmd_setup_export(args: argparse.Namespace) -> None:
    try:
        value = _engine(args).export_migration(
            state_root=args.source_state_dir,
            config_root=args.source_config_dir,
            output=args.output,
            source_post_once_version=args.source_version,
            source_revision=args.source_revision,
            source_state_registry_version=args.state_registry_version,
            session_id=args.session_id,
            apply=args.apply,
            expected_sha256=args.expected_sha256,
        )
    except (SetupEngineError, SetupStoreError, ValueError) as exc:
        _error(exc, as_json=args.json)
    _emit(value, as_json=args.json)


def cmd_setup_recovery_point(args: argparse.Namespace) -> None:
    try:
        value = _engine(args).recovery_point(
            state_root=args.source_state_dir,
            config_root=args.source_config_dir,
            output=args.output,
            source_post_once_version=args.source_version,
            source_revision=args.source_revision,
            source_state_registry_version=args.state_registry_version,
            session_id=args.session_id,
            apply=args.apply,
            expected_sha256=args.expected_sha256,
        )
    except (SetupEngineError, SetupStoreError, ValueError) as exc:
        _error(exc, as_json=args.json)
    _emit(value, as_json=args.json)


def cmd_setup_inspect_bundle(args: argparse.Namespace) -> None:
    try:
        value = _engine(args).inspect_bundle(
            args.bundle,
            expected_bundle_sha256=args.expected_bundle_sha256,
            recovery=args.recovery,
        )
    except (SetupEngineError, SetupStoreError, ValueError) as exc:
        _error(exc, as_json=args.json)
    if args.json:
        print(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False))
        return
    print(
        "Post-Once dead-host recovery point"
        if value.get("bundle_purpose") == "dead_host_recovery"
        else "Post-Once healthy migration bundle"
    )
    print(f"Status:      {value['status']}")
    print(f"Operation:   {value['operation_id']}")
    print(f"Source:      {value['source_installation_id']}")
    print(f"Generation:  {value['source_authority_generation']}")
    print(f"Bundle SHA:  {value['bundle_sha256']}")
    print(f"Continuity:  {value['source_continuity']}")
    print("Credentials: excluded")
    print("Publishing authority: NO")


def cmd_setup_restore(args: argparse.Namespace) -> None:
    try:
        if args.recovery:
            if args.source_lost_at is None or args.max_data_loss_minutes is None:
                raise SetupEngineError(
                    "setup.recovery.rpo_required",
                    "--recovery requires --source-lost-at and --max-data-loss-minutes",
                )
            value = _engine(args).restore_recovery(
                args.bundle,
                source_lost_at=args.source_lost_at,
                max_data_loss_minutes=args.max_data_loss_minutes,
                expected_bundle_sha256=args.expected_bundle_sha256,
                machine_label=args.machine_label,
            )
        else:
            value = _engine(args).restore_migration(
                args.bundle,
                expected_bundle_sha256=args.expected_bundle_sha256,
                machine_label=args.machine_label,
            )
    except (SetupEngineError, SetupStoreError, ValueError) as exc:
        _error(exc, as_json=args.json)
    _emit(value, as_json=args.json)


def cmd_setup_verify(args: argparse.Namespace) -> None:
    try:
        readiness = _load_json_file(args.provider_readiness)
        if args.recovery:
            value = _engine(args).verify_recovery(
                readiness,
                session_id=args.session_id,
            )
        else:
            value = _engine(args).verify_migration(
                readiness,
                session_id=args.session_id,
            )
    except (SetupEngineError, SetupStoreError, ValueError) as exc:
        _error(exc, as_json=args.json)
    _emit(value, as_json=args.json)


def cmd_setup_activate(args: argparse.Namespace) -> None:
    try:
        recovery_resolution = (
            _load_json_file(args.recovery_resolution)
            if args.recovery_resolution
            else None
        )
        engine = _engine(args)
        if args.apply:
            if not args.expected_sha256:
                raise SetupEngineError(
                    "activation.expected_sha256.required",
                    "--apply requires --expected-sha256 from a fresh activation preview",
                )
            value = engine.activate(
                runtime_root=args.runtime_root,
                state_root=args.state_dir,
                config_root=args.config_dir,
                expected_sha256=args.expected_sha256,
                session_id=args.session_id,
                recovery_resolution=recovery_resolution,
            )
        else:
            value = engine.activation_preview(
                runtime_root=args.runtime_root,
                state_root=args.state_dir,
                config_root=args.config_dir,
                session_id=args.session_id,
                recovery_resolution=recovery_resolution,
            )
    except (SetupEngineError, SetupStoreError, ValueError) as exc:
        _error(exc, as_json=args.json)
    if args.json:
        print(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False))
        return
    print("Post-Once activation gate")
    print(f"Status: {value['status']}")
    if value.get("review_sha256"):
        print(f"Review SHA-256: {value['review_sha256']}")
    print(f"Publishing authority: {'YES' if value.get('publishing_authority') else 'NO'}")
    print(f"Automation enabled: {'YES' if value.get('automation_enabled') else 'NO'}")


def cmd_setup_deactivate(args: argparse.Namespace) -> None:
    try:
        engine = _engine(args)
        if args.apply:
            if not args.expected_sha256:
                raise SetupEngineError(
                    "deactivation.expected_sha256.required",
                    "--apply requires --expected-sha256 from a fresh deactivation preview",
                )
            value = engine.deactivate(
                runtime_root=args.runtime_root,
                state_root=args.state_dir,
                reason=args.reason,
                expected_sha256=args.expected_sha256,
                session_id=args.session_id,
            )
        else:
            value = engine.deactivation_preview(
                runtime_root=args.runtime_root,
                state_root=args.state_dir,
                reason=args.reason,
                session_id=args.session_id,
            )
    except (SetupEngineError, SetupStoreError, ValueError) as exc:
        _error(exc, as_json=args.json)
    if args.json:
        print(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False))
        return
    print("Post-Once deactivation gate")
    print(f"Status: {value['status']}")
    if value.get("review_sha256"):
        print(f"Review SHA-256: {value['review_sha256']}")
    print(f"Publishing authority: {'YES' if value.get('publishing_authority') else 'NO'}")
    print(f"Automation enabled: {'YES' if value.get('automation_enabled') else 'NO'}")


def cmd_setup_beta(args: argparse.Namespace) -> None:
    try:
        from ocpf_post import setup_beta

        if args.action in {"preflight", "enroll", "observe"}:
            missing = [
                name
                for name in ("state_dir", "config_dir", "runtime_root")
                if not getattr(args, name, None)
            ]
            if missing:
                raise setup_beta.BetaError(
                    "beta.input.required",
                    "Beta preflight/enroll/observe requires --state-dir, --config-dir and --runtime-root",
                )
        if args.action in {"preflight", "enroll"} and (not args.ring or not args.target_revision):
            raise setup_beta.BetaError(
                "beta.input.required",
                "Beta preflight/enroll requires --ring and --target-revision",
            )
        if args.action == "observe" and not args.operator_outcome:
            raise setup_beta.BetaError(
                "beta.input.required",
                "Beta observe requires --operator-outcome",
            )

        if args.action == "preflight":
            value = setup_beta.preflight(
                workspace=getattr(args, "workspace", None),
                ring=args.ring,
                target_revision=args.target_revision,
                state_root=args.state_dir,
                config_root=args.config_dir,
                runtime_root=args.runtime_root,
            )
        elif args.action == "enroll":
            value = setup_beta.enroll(
                workspace=getattr(args, "workspace", None),
                ring=args.ring,
                target_revision=args.target_revision,
                state_root=args.state_dir,
                config_root=args.config_dir,
                runtime_root=args.runtime_root,
            )
        elif args.action == "observe":
            value = setup_beta.observe(
                workspace=getattr(args, "workspace", None),
                beta_id=args.beta_id,
                state_root=args.state_dir,
                config_root=args.config_dir,
                runtime_root=args.runtime_root,
                operator_outcome=args.operator_outcome,
            )
        elif args.action == "status":
            value = setup_beta.status(
                getattr(args, "workspace", None),
                beta_id=args.beta_id,
            )
        else:
            value = setup_beta.decide(
                getattr(args, "workspace", None),
                beta_id=args.beta_id,
                minimum_observations=args.minimum_observations,
                minimum_hours=args.minimum_hours,
            )
        print(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False))
    except (setup_beta.BetaError, SetupEngineError, SetupStoreError, ValueError) as exc:
        _error(exc, as_json=True)


def cmd_setup_adoption_review(args: argparse.Namespace) -> None:
    try:
        from ocpf_post import setup_beta

        value = setup_beta.adoption_review(
            getattr(args, "workspace", None),
            beta_id=args.beta_id,
            minimum_observations=args.minimum_observations,
            minimum_hours=args.minimum_hours,
        )
        print(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False))
    except (setup_beta.BetaError, SetupEngineError, SetupStoreError, ValueError) as exc:
        _error(exc, as_json=True)


def cmd_setup_browser(args: argparse.Namespace) -> None:
    try:
        from ocpf_post.setup_browser import SetupBrowserError, serve

        serve(
            getattr(args, "workspace", None),
            port=args.port,
            launch_browser=not args.no_open,
        )
    except (SetupBrowserError, SetupStoreError, ValueError) as exc:
        _error(exc, as_json=False)


def _ask_mode() -> str:
    print("\nWhat are you doing?")
    print("  1. Setting up Post-Once for the first time")
    print("  2. Exploring without publishing")
    while True:
        choice = input("Choose 1 or 2: ").strip()
        if choice == "1":
            return "fresh"
        if choice == "2":
            return "explore"
        print("Enter 1 or 2.")


def _ask_timezone() -> str:
    candidate = detect_timezone()
    while True:
        if candidate:
            value = input(f"Timezone [{candidate}] (Enter confirms): ").strip() or candidate
        else:
            value = input("IANA timezone (for example Europe/London): ").strip()
        if value:
            return value
        print("A timezone is required.")


def _ask_pace() -> tuple[str, int | None]:
    print("\nChoose your normal publishing pace.")
    print("This is not the 100/day safety ceiling; it is the normal daily amount.")
    print("  1. Occasional — 3 logical originals/day")
    print("  2. Regular    — 5/day")
    print("  3. Active     — 10/day")
    print("  4. High       — 20/day")
    print("  5. Custom     — choose 1–100/day")
    mapping = {"1": "occasional", "2": "regular", "3": "active", "4": "high", "5": "custom"}
    while True:
        choice = input("Choose 1–5 (no default): ").strip()
        profile = mapping.get(choice)
        if profile is None:
            print("Enter a number from 1 to 5.")
            continue
        if profile != "custom":
            return profile, None
        raw = input(f"Custom logical originals/day (1–{HARD_CEILING}): ").strip()
        try:
            value = int(raw)
        except ValueError:
            print("Enter a whole number.")
            continue
        if 1 <= value <= HARD_CEILING:
            return profile, value
        print(f"Enter a value from 1 to {HARD_CEILING}.")


def _interactive_configure(engine: SetupEngine, value: dict[str, Any]) -> dict[str, Any]:
    session = value["session"]
    if session["stage"] != "operation_ready":
        return engine.resume(session_id=session["session_id"])
    timezone = _ask_timezone()
    pace, daily = _ask_pace()
    print("\nSafety review")
    print(f"  Timezone: {timezone}")
    normal = daily if pace == "custom" else PACE_PROFILES[pace]
    print(f"  Normal pace: {normal} logical originals/day")
    print(f"  Hard ceiling: {HARD_CEILING} logical publications/account/day")
    print("  Provider credentials: none changed")
    print("  Automation: disabled")
    confirmation = input("Save this setup configuration? [y/N]: ").strip().lower()
    if confirmation not in {"y", "yes"}:
        print("Not saved. Run 'post-once setup resume' when ready.")
        return engine.status(session["session_id"])
    return engine.configure(
        session["session_id"],
        timezone=timezone,
        pace=pace,
        daily_originals=daily,
    )


def cmd_setup_admission(args: argparse.Namespace) -> None:
    try:
        value = _engine(args).installation_admission(
            state_root=args.state_dir,
            config_root=args.config_dir,
            production=args.production,
        )
    except (SetupEngineError, SetupStoreError, ValueError) as exc:
        _error(exc, as_json=args.json)
    if args.json:
        print(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False))
    else:
        installation = value["installation"]
        proof = value["host_capability_proof"]
        print("Post-Once installation admission")
        print(f"Classification: {installation['classification']}")
        print(f"Recommended action: {installation['recommended_action']}")
        print(f"Host proof: {proof['status']} ({proof['profile']})")
        if proof["blockers"]:
            print("Host blockers:")
            for blocker in proof["blockers"]:
                print(f"  - {blocker}")
        if installation["reasons"]:
            print("Classification evidence:")
            for reason in installation["reasons"]:
                print(f"  - {reason}")
        print("Provider consequence attempted: NO")
    if value["status"] == "BLOCKED":
        raise SystemExit(3)


def cmd_setup_interactive(args: argparse.Namespace) -> None:
    engine = _engine(args)
    try:
        admission = engine.installation_admission()
        classification = admission["installation"]["classification"]
        if classification != "new_host" and classification != "incomplete_bootstrap":
            raise SetupEngineError(
                "setup.guided.existing_installation",
                "Guided setup found existing durable installation state "
                f"({classification}); use 'post-once setup admission' and follow "
                f"{admission['installation']['recommended_action']} instead of starting over",
            )

        try:
            current = engine.status()
        except SetupEngineError as exc:
            if exc.code not in {"setup.store.missing", "setup.session.missing"}:
                raise
            current = None
        except SetupStoreError as exc:
            if exc.code not in {"setup.store.missing", "setup.session.missing"}:
                raise
            current = None

        if current is not None and current["session"]["session_status"] == "open":
            print(
                f"An incomplete {current['mode']} setup exists at "
                f"{current['session']['stage']} (revision {current['session']['revision']})."
            )
            answer = input("Resume it? [Y/n]: ").strip().lower()
            if answer in {"", "y", "yes"}:
                current = engine.resume(session_id=current["session"]["session_id"])
                result = _interactive_configure(engine, current)
                _emit(result, as_json=False)
                return
            raise SetupEngineError(
                "setup.session.open_exists",
                "Existing setup remains open; it was not replaced",
            )

        mode = _ask_mode()
        operator = None
        if mode == "fresh":
            while not operator:
                operator = input("Operator/business name: ").strip()
        current = engine.begin(mode, operator_label=operator)
        result = _interactive_configure(engine, current)
        _emit(result, as_json=False)
    except KeyboardInterrupt:
        print("\nSetup paused. Committed progress is safe; run 'ocpf-post setup resume'.", file=sys.stderr)
        raise SystemExit(130)
    except (SetupEngineError, SetupStoreError, ValueError) as exc:
        _error(exc, as_json=False)


def _workspace(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--workspace",
        help="Bootstrap setup workspace (default is a separate post-once-bootstrap XDG state root)",
    )


def _json(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="Return machine-readable setup state")


def _config_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--timezone", help="Explicit IANA timezone, for example Europe/London")
    parser.add_argument(
        "--pace",
        choices=("occasional", "regular", "active", "high", "custom"),
        help="Normal publishing pace; no default is selected",
    )
    parser.add_argument(
        "--daily-originals",
        type=int,
        help="Custom logical originals/day; accepted only with --pace custom",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="post-once setup",
        description="Create, migrate, recover, activate or deactivate Post-Once through reviewed safety gates.",
    )
    _workspace(parser)
    parser.set_defaults(func=cmd_setup_interactive)
    setup_sub = parser.add_subparsers(dest="setup_command", required=False)

    interactive = setup_sub.add_parser(
        "interactive",
        help="Run the resumable terminal setup wizard (bare setup is an alias)",
    )
    _workspace(interactive)
    interactive.set_defaults(func=cmd_setup_interactive)

    start = setup_sub.add_parser(
        "start",
        help="Start fresh/explore setup using explicit non-interactive inputs",
    )
    _workspace(start)
    _json(start)
    start.add_argument(
        "--mode",
        required=True,
        choices=("fresh", "explore", "migrate", "recover", "different_operator"),
    )
    start.add_argument("--operator-label", help="Required for fresh setup")
    start.add_argument("--machine-label", help="Optional descriptive machine label; not authority")
    _config_args(start)
    start.set_defaults(func=cmd_setup_start)

    admission = setup_sub.add_parser(
        "admission",
        help="Prove host capabilities and classify an existing installation before setup",
    )
    _workspace(admission)
    _json(admission)
    admission.add_argument("--state-dir", help="Production state directory to classify; defaults to Post-Once state")
    admission.add_argument("--config-dir", help="Production config directory to classify; defaults to Post-Once config")
    admission.add_argument(
        "--production",
        action="store_true",
        help="Require production host capabilities including a reachable systemd user manager",
    )
    admission.set_defaults(func=cmd_setup_admission)

    status = setup_sub.add_parser(
        "status",
        help="Read the latest or named setup session without creating or mutating setup state",
    )
    _workspace(status)
    _json(status)
    status.add_argument("--session-id")
    status.set_defaults(func=cmd_setup_status)

    resume = setup_sub.add_parser(
        "resume",
        help="Resume one incomplete setup session from its last committed revision",
    )
    _workspace(resume)
    _json(resume)
    resume.add_argument("--session-id")
    _config_args(resume)
    resume.set_defaults(func=cmd_setup_resume)

    verify_fresh = setup_sub.add_parser(
        "verify-fresh",
        help="Read-only verify one connected provider and user-owned content for fresh activation",
    )
    _workspace(verify_fresh)
    _json(verify_fresh)
    verify_fresh.add_argument("--session-id")
    verify_fresh.add_argument(
        "--provider",
        required=True,
        choices=("x", "threads", "linkedin"),
        help="Connected provider to observe without refreshing or publishing",
    )
    verify_fresh.add_argument(
        "--expected-account-id",
        required=True,
        help="Exact immutable provider account ID expected for this fresh installation",
    )
    verify_fresh.set_defaults(func=cmd_setup_verify_fresh)

    connect_x = setup_sub.add_parser(
        "connect-x",
        help="Connect and read-back verify one X account for Fresh beta onboarding",
    )
    _workspace(connect_x)
    connect_x.add_argument("--client-id", required=True, help="X OAuth 2.0 Client ID")
    connect_x.add_argument(
        "--redirect-uri",
        default="http://127.0.0.1:8765/callback",
        help="Registered localhost X OAuth callback URI",
    )
    secret = connect_x.add_mutually_exclusive_group()
    secret.add_argument(
        "--prompt-client-secret",
        action="store_true",
        help="Prompt privately for a confidential-client OAuth 2.0 Client Secret",
    )
    secret.add_argument(
        "--client-secret-file",
        help="Read the OAuth 2.0 Client Secret from a local regular file",
    )
    connect_x.set_defaults(func=cmd_setup_connect_x)

    onboard_x = setup_sub.add_parser(
        "onboard-x",
        help="Bind the connected X identity to one user project and manual-only campaign",
    )
    _workspace(onboard_x)
    _json(onboard_x)
    onboard_x.add_argument("--session-id")
    onboard_x.add_argument("--project", required=True, help="Lowercase hyphenated project ID")
    onboard_x.add_argument("--label", required=True, help="Human-readable project/business label")
    onboard_x.add_argument(
        "--campaign-id",
        help="Optional uppercase campaign ID; defaults to the generated project prefix plus 001",
    )
    onboard_x.add_argument(
        "--source-id",
        default="beta-onboarding",
        help="Owner-approval/source reference stored with the campaign",
    )
    copy = onboard_x.add_mutually_exclusive_group(required=True)
    copy.add_argument("--text", help="Exact reviewed campaign text")
    copy.add_argument("--text-file", help="UTF-8 file containing exact reviewed campaign text")
    copy.add_argument("--stdin", action="store_true", help="Read exact reviewed campaign text from stdin")
    onboard_x.add_argument(
        "--apply",
        action="store_true",
        help="Save the reviewed local project/campaign and advance Fresh verification",
    )
    onboard_x.set_defaults(func=cmd_setup_onboard_x)

    export = setup_sub.add_parser(
        "export",
        help="Preview or seal a credential-free healthy migration bundle from a quiescent source",
    )
    _workspace(export)
    _json(export)
    export.add_argument("--session-id")
    export.add_argument("--source-state-dir", required=True, help="Explicit source Post-Once state directory")
    export.add_argument("--source-config-dir", required=True, help="Explicit source Post-Once config directory")
    export.add_argument("--output", required=True, help="Immutable transfer .tar.gz path")
    export.add_argument("--source-version", required=True, help="Exact source Post-Once release")
    export.add_argument("--source-revision", required=True, help="Exact 40-character source Git revision")
    export.add_argument("--state-registry-version", type=int, default=1)
    export.add_argument("--apply", action="store_true", help="Retire source bootstrap authority and seal the reviewed bundle")
    export.add_argument("--expected-sha256", help="Review SHA-256 returned by the preview")
    export.set_defaults(func=cmd_setup_export)

    recovery_point = setup_sub.add_parser(
        "recovery-point",
        help="Preview or seal an immutable credential-free dead-host recovery point from a quiescent source",
    )
    _workspace(recovery_point)
    _json(recovery_point)
    recovery_point.add_argument("--session-id")
    recovery_point.add_argument("--source-state-dir", required=True, help="Explicit source Post-Once state directory")
    recovery_point.add_argument("--source-config-dir", required=True, help="Explicit source Post-Once config directory")
    recovery_point.add_argument("--output", required=True, help="Immutable recovery-point .tar.gz path")
    recovery_point.add_argument("--source-version", required=True, help="Exact source Post-Once release")
    recovery_point.add_argument("--source-revision", required=True, help="Exact 40-character source Git revision")
    recovery_point.add_argument("--state-registry-version", type=int, default=1)
    recovery_point.add_argument("--apply", action="store_true", help="Seal the reviewed recovery point without retiring the source")
    recovery_point.add_argument("--expected-sha256", help="Review SHA-256 returned by the preview")
    recovery_point.set_defaults(func=cmd_setup_recovery_point)

    inspect_bundle = setup_sub.add_parser(
        "inspect-bundle",
        help="Verify a healthy migration bundle without restoring or granting authority",
    )
    _workspace(inspect_bundle)
    _json(inspect_bundle)
    inspect_bundle.add_argument("--bundle", required=True)
    inspect_bundle.add_argument("--expected-bundle-sha256")
    inspect_bundle.add_argument(
        "--recovery",
        action="store_true",
        help="Interpret the artifact as a dead-host recovery point rather than a healthy migration bundle",
    )
    inspect_bundle.set_defaults(func=cmd_setup_inspect_bundle)

    restore = setup_sub.add_parser(
        "restore",
        help="Verify and quarantine a healthy migration bundle on a new installation",
    )
    _workspace(restore)
    _json(restore)
    restore.add_argument("--bundle", required=True)
    restore.add_argument("--expected-bundle-sha256")
    restore.add_argument("--machine-label", help="Optional descriptive label for this new installation")
    restore.add_argument("--recovery", action="store_true", help="Restore a dead-host recovery point")
    restore.add_argument(
        "--source-lost-at",
        help="When the previous host became unavailable, as an offset-aware ISO timestamp",
    )
    restore.add_argument(
        "--max-data-loss-minutes",
        type=int,
        help="Maximum acceptable gap between the recovery point and declared host loss",
    )
    restore.set_defaults(func=cmd_setup_restore)

    verify = setup_sub.add_parser(
        "verify",
        help="Verify target provider identity evidence and reconcile transferred schedules",
    )
    _workspace(verify)
    _json(verify)
    verify.add_argument("--session-id")
    verify.add_argument(
        "--provider-readiness",
        required=True,
        help="JSON file containing read-only provider readiness projections for the target",
    )
    verify.add_argument(
        "--recovery",
        action="store_true",
        help="Verify a dead-host recovery target and produce the recovery-review gate",
    )
    verify.set_defaults(func=cmd_setup_verify)

    activate = setup_sub.add_parser(
        "activate",
        help="Preview or apply the reviewed unattended-publishing activation gate",
    )
    _workspace(activate)
    _json(activate)
    activate.add_argument("--session-id")
    activate.add_argument("--runtime-root", default=".", help="Exact Post-Once runtime checkout to install")
    activate.add_argument("--state-dir", required=True, help="Target production state directory")
    activate.add_argument("--config-dir", required=True, help="Target production config directory")
    activate.add_argument(
        "--recovery-resolution",
        help="Structured JSON resolving every required dead-host recovery blocker",
    )
    activate.add_argument("--apply", action="store_true")
    activate.add_argument("--expected-sha256")
    activate.set_defaults(func=cmd_setup_activate)

    deactivate = setup_sub.add_parser(
        "deactivate",
        help="Preview or apply marker-first unattended-automation deactivation",
    )
    _workspace(deactivate)
    _json(deactivate)
    deactivate.add_argument("--session-id")
    deactivate.add_argument("--runtime-root", default=".", help="Exact active Post-Once runtime checkout")
    deactivate.add_argument("--state-dir", required=True, help="Active production state directory")
    deactivate.add_argument("--reason", required=True, help="Bounded operator reason recorded with the deactivation")
    deactivate.add_argument("--apply", action="store_true")
    deactivate.add_argument("--expected-sha256")
    deactivate.set_defaults(func=cmd_setup_deactivate)

    beta = setup_sub.add_parser(
        "beta",
        help="Preflight, enroll, observe and decide one local limited-beta ring without changing authority",
    )
    _workspace(beta)
    beta.add_argument("--action", required=True, choices=("preflight", "enroll", "observe", "status", "decide"))
    beta.add_argument("--beta-id")
    beta.add_argument("--ring", choices=("rehearsal", "owner-canary"))
    beta.add_argument("--target-revision")
    beta.add_argument("--runtime-root", default=".")
    beta.add_argument("--state-dir")
    beta.add_argument("--config-dir")
    beta.add_argument("--operator-outcome", choices=("healthy", "issue", "rollback"))
    beta.add_argument("--minimum-observations", type=int)
    beta.add_argument("--minimum-hours", type=int)
    beta.set_defaults(func=cmd_setup_beta)

    adoption = setup_sub.add_parser(
        "adoption-review",
        help="Build an evidence-backed package for bootstrap-owned runtime adoption",
    )
    _workspace(adoption)
    adoption.add_argument("--beta-id")
    adoption.add_argument("--minimum-observations", type=int)
    adoption.add_argument("--minimum-hours", type=int)
    adoption.set_defaults(func=cmd_setup_adoption_review)

    browser = setup_sub.add_parser(
        "browser",
        help="Serve the secure loopback-only browser renderer over the same setup engine",
    )
    _workspace(browser)
    browser.add_argument(
        "--port",
        type=int,
        default=0,
        help="Loopback port; default 0 asks the OS for an ephemeral free port",
    )
    browser.add_argument(
        "--no-open",
        action="store_true",
        help="Print the one-time loopback URL without launching the default browser",
    )
    browser.set_defaults(func=cmd_setup_browser)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
