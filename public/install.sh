#!/usr/bin/env bash
set -euo pipefail

# PostSteward Local installer.
#
# Distribution follows the same broad contract as OpenClaw's domain-hosted
# installer: one curl command, exact resolved revision, optional --no-onboard,
# --verify and --dry-run. The installed local publishing runtime is Post-Once;
# hosted PostSteward and the local runtime keep separate state/authority.

SOURCE_URL="${POSTSTEWARD_LOCAL_SOURCE:-https://github.com/AyobamiH/poststeward.git}"
SOURCE_REF="${POSTSTEWARD_LOCAL_REF:-post-once-runtime-beta}"
REVISION="${POSTSTEWARD_LOCAL_REVISION:-}"
PREFIX="${POSTSTEWARD_LOCAL_PREFIX:-${XDG_DATA_HOME:-$HOME/.local/share}/post-once}"
BIN_DIR="${POSTSTEWARD_BIN_DIR:-$HOME/.local/bin}"
NO_ONBOARD=0
VERIFY=0
DRY_RUN=0
VERBOSE=0

usage() {
  cat <<'EOF'
PostSteward Local installer (Post-Once runtime)

Usage:
  curl -fsSL --proto '=https' --tlsv1.2 https://poststeward.com/install.sh | bash
  curl -fsSL --proto '=https' --tlsv1.2 https://poststeward.com/install.sh | bash -s -- [options]

Options:
  --revision <40-char-sha>  Install one exact published runtime revision.
  --no-onboard              Install only; do not launch Fresh setup.
  --verify                  Verify the installed command/discovery surface.
  --dry-run                 Print the resolved plan without changing files.
  --prefix <absolute-path>  Runtime data prefix (default: ~/.local/share/post-once).
  --bin-dir <absolute-path> User command directory (default: ~/.local/bin).
  --verbose                 Enable shell tracing.
  -h, --help                Show this help.

Installed commands:
  poststeward   PostSteward-branded local entrypoint.
  post-once     Post-Once compatibility/runtime entrypoint.

The installer supports Linux/WSL2 with Python >=3.10 and Git. Installation does
not grant provider authority, create schedules, publish, or enable unattended
automation.
EOF
}

fail() {
  printf 'PostSteward install blocked: %s\n' "$*" >&2
  exit 2
}

log() {
  printf '%s\n' "$*"
}

while (($#)); do
  case "$1" in
    --revision)
      (($# >= 2)) || fail "--revision requires a value"
      REVISION="$2"
      shift 2
      ;;
    --no-onboard)
      NO_ONBOARD=1
      shift
      ;;
    --verify)
      VERIFY=1
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --prefix)
      (($# >= 2)) || fail "--prefix requires a value"
      PREFIX="$2"
      shift 2
      ;;
    --bin-dir)
      (($# >= 2)) || fail "--bin-dir requires a value"
      BIN_DIR="$2"
      shift 2
      ;;
    --verbose)
      VERBOSE=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "unknown option: $1"
      ;;
  esac
done

((VERBOSE)) && set -x

case "$PREFIX" in
  /*) ;;
  *) fail "--prefix must be an absolute path" ;;
esac
case "$BIN_DIR" in
  /*) ;;
  *) fail "--bin-dir must be an absolute path" ;;
esac

[[ "$(uname -s)" == "Linux" ]] || fail "current beta supports Linux/WSL2 only"
command -v git >/dev/null 2>&1 || fail "git is required"
command -v python3 >/dev/null 2>&1 || fail "python3 is required"
python3 - <<'PY' || fail "Python 3.10 or newer is required"
import sys
raise SystemExit(0 if sys.version_info >= (3, 10) else 1)
PY

if [[ -n "$REVISION" && ! "$REVISION" =~ ^[0-9a-fA-F]{40}$ ]]; then
  fail "--revision must be an exact 40-character Git commit"
fi
REVISION="${REVISION,,}"

resolve_channel_revision() {
  git ls-remote "$SOURCE_URL" "refs/heads/$SOURCE_REF" |
    awk 'NR==1 {print $1}'
}

if [[ -z "$REVISION" ]]; then
  REVISION="$(resolve_channel_revision)"
  [[ "$REVISION" =~ ^[0-9a-f]{40}$ ]] ||
    fail "could not resolve the published PostSteward Local runtime channel"
fi

RUNTIME_DIR="$PREFIX/runtime"
STATE_HOME="${XDG_STATE_HOME:-$HOME/.local/state}"
INSTALL_STATE_DIR="$STATE_HOME/poststeward"
RECEIPT="$INSTALL_STATE_DIR/local-install.json"
POST_ONCE_SHIM="$BIN_DIR/post-once"
POSTSTEWARD_SHIM="$BIN_DIR/poststeward"

log "PostSteward Local install plan"
log "  distribution: https://poststeward.com/install.sh"
log "  runtime source: $SOURCE_URL"
log "  runtime channel: $SOURCE_REF"
log "  revision: $REVISION"
log "  runtime: $RUNTIME_DIR"
log "  commands: $POSTSTEWARD_SHIM , $POST_ONCE_SHIM"
log "  state: ${XDG_STATE_HOME:-$HOME/.local/state}/post-once"
log "  config: ${XDG_CONFIG_HOME:-$HOME/.config}/post-once"
log "  provider authority: unchanged"
log "  unattended automation: disabled until explicit activation"

if ((DRY_RUN)); then
  log "Dry run only; no files changed."
  exit 0
fi

umask 077
mkdir -p "$PREFIX" "$BIN_DIR" "$INSTALL_STATE_DIR"

managed_shim() {
  local path="$1"
  [[ ! -e "$path" ]] && return 0
  [[ -f "$path" && ! -L "$path" ]] ||
    fail "existing command is not a plain managed file: $path"
  grep -Fq '# managed-by: poststeward-local' "$path" ||
    fail "refusing to overwrite unrelated command: $path"
}

managed_shim "$POST_ONCE_SHIM"
managed_shim "$POSTSTEWARD_SHIM"

if [[ -e "$RUNTIME_DIR" ]]; then
  [[ -d "$RUNTIME_DIR/.git" ]] ||
    fail "runtime path exists but is not a managed Git checkout: $RUNTIME_DIR"
  ORIGIN="$(git -C "$RUNTIME_DIR" config --get remote.origin.url || true)"
  [[ "$ORIGIN" == "$SOURCE_URL" ]] ||
    fail "existing local runtime origin does not match PostSteward distribution"
  [[ -z "$(git -C "$RUNTIME_DIR" status --porcelain=v1 --untracked-files=all)" ]] ||
    fail "existing local runtime checkout is dirty"
  CURRENT="$(git -C "$RUNTIME_DIR" rev-parse --verify 'HEAD^{commit}')"
  [[ "$CURRENT" == "$REVISION" ]] ||
    fail "local runtime is $CURRENT but this install requests $REVISION; use the reviewed runtime upgrade path"
else
  STAGING="$PREFIX/.runtime-staging-$$"
  cleanup() {
    rm -rf -- "$STAGING"
  }
  trap cleanup EXIT INT TERM

  git clone     --quiet     --no-checkout     --filter=blob:none     --single-branch     --branch "$SOURCE_REF"     "$SOURCE_URL"     "$STAGING"

  if ! git -C "$STAGING" cat-file -e "$REVISION^{commit}" 2>/dev/null; then
    git -C "$STAGING" fetch --quiet --no-tags origin "$REVISION" ||
      fail "requested runtime revision is unavailable"
  fi
  RESOLVED="$(git -C "$STAGING" rev-parse --verify "$REVISION^{commit}")"
  [[ "$RESOLVED" == "$REVISION" ]] ||
    fail "requested runtime revision did not resolve exactly"

  git -C "$STAGING" checkout --quiet --detach "$REVISION"
  [[ -z "$(git -C "$STAGING" status --porcelain=v1 --untracked-files=all)" ]] ||
    fail "published runtime checkout is unexpectedly dirty"
  [[ -f "$STAGING/post-once" ]] ||
    fail "published runtime has no canonical post-once launcher"
  [[ -f "$STAGING/pyproject.toml" ]] ||
    fail "published runtime has no package metadata"

  mv "$STAGING" "$RUNTIME_DIR"
  trap - EXIT INT TERM
fi

install_shim() {
  local path="$1"
  local label="$2"
  local tmp
  tmp="$(mktemp "$BIN_DIR/.poststeward.XXXXXX")"
  cat >"$tmp" <<EOF
#!/bin/sh
# managed-by: poststeward-local
# entrypoint: $label
set -eu
exec /bin/sh "$RUNTIME_DIR/post-once" "\$@"
EOF
  chmod 0755 "$tmp"
  mv -f "$tmp" "$path"
}

install_shim "$POST_ONCE_SHIM" "post-once"
install_shim "$POSTSTEWARD_SHIM" "poststeward"

VERSION_OUTPUT="$("$POST_ONCE_SHIM" --version)"
[[ -n "$VERSION_OUTPUT" ]] || fail "installed Post-Once runtime did not execute"

POSTSTEWARD_INSTALL_REVISION="$REVISION" POSTSTEWARD_INSTALL_RUNTIME="$RUNTIME_DIR" POSTSTEWARD_INSTALL_RECEIPT="$RECEIPT" python3 - <<'PY'
from __future__ import annotations
from datetime import datetime, timezone
import json
import os
from pathlib import Path

path = Path(os.environ["POSTSTEWARD_INSTALL_RECEIPT"])
value = {
    "schema_version": 1,
    "distribution": "poststeward.com",
    "product": "PostSteward Local",
    "runtime": "Post-Once",
    "runtime_lineage": "post-once-bootstrap-runtime-v1",
    "revision": os.environ["POSTSTEWARD_INSTALL_REVISION"],
    "runtime_root": os.environ["POSTSTEWARD_INSTALL_RUNTIME"],
    "installed_at": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
    "publishing_authority_granted_by_install": False,
    "automation_enabled_by_install": False,
}
tmp = path.with_name(path.name + ".tmp")
tmp.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
tmp.chmod(0o600)
tmp.replace(path)
PY

log "Installed: PostSteward Local"
log "Runtime:   $VERSION_OUTPUT"
log "Revision:  $REVISION"
log "Command:   $POSTSTEWARD_SHIM"
log "Compat:    $POST_ONCE_SHIM"

if ((VERIFY)); then
  "$POST_ONCE_SHIM" --version >/dev/null
  "$POST_ONCE_SHIM" help --json >/dev/null
  log "Verify: PASS"
fi

if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
  log "PATH note: add $BIN_DIR to PATH to run 'poststeward' or 'post-once' directly."
fi

if ((NO_ONBOARD)); then
  log "Onboarding skipped."
  log "Run: poststeward setup interactive"
  exit 0
fi

if [[ -t 1 && -r /dev/tty && -w /dev/tty ]]; then
  log ""
  log "Starting PostSteward Local onboarding..."
  "$POSTSTEWARD_SHIM" setup interactive </dev/tty
  log ""
  log "Connect one or more publishing destinations next:"
  log "  poststeward setup connect-x ..."
  log "  poststeward setup connect-threads ..."
  log "  poststeward setup connect-linkedin ..."
  log "Then bind reviewed copy with:"
  log "  poststeward setup onboard-social ..."
else
  log "No interactive terminal detected."
  log "Run later: poststeward setup interactive"
fi
