#!/usr/bin/env bash
set -euo pipefail

VERSION="${POSTSTEWARD_INSTALL_VERSION:-main}"
PREFIX="${POSTSTEWARD_INSTALL_PREFIX:-${XDG_DATA_HOME:-$HOME/.local/share}/poststeward}"
BIN_DIR="${POSTSTEWARD_BIN_DIR:-$HOME/.local/bin}"
ORIGIN="${POSTSTEWARD_ORIGIN:-https://poststeward.com}"
SOURCE_BASE="${POSTSTEWARD_INSTALL_SOURCE_BASE:-https://raw.githubusercontent.com/AyobamiH/poststeward}"
NO_ONBOARD=0
DRY_RUN=0
VERIFY=1

usage() {
  cat <<'EOF'
PostSteward installer

Usage:
  curl -fsSL --proto '=https' --tlsv1.2 https://poststeward.com/install.sh | bash
  curl -fsSL --proto '=https' --tlsv1.2 https://poststeward.com/install.sh | bash -s -- [options]

Options:
  --version <ref|sha>       Public poststeward repository ref (default: main)
  --prefix <absolute-path>  Product data prefix (default: ~/.local/share/poststeward)
  --bin-dir <absolute-path> Command directory (default: ~/.local/bin)
  --origin <https-origin>   Hosted PostSteward origin (default: https://poststeward.com)
  --no-onboard              Install and verify without opening guided onboarding
  --no-verify               Skip post-install CLI smoke verification
  --dry-run                 Print the resolved plan without changing files
  -h, --help                Show this help

Environment variables:
  POSTSTEWARD_INSTALL_VERSION
  POSTSTEWARD_INSTALL_PREFIX
  POSTSTEWARD_BIN_DIR
  POSTSTEWARD_ORIGIN
  POSTSTEWARD_INSTALL_SOURCE_BASE
EOF
}

fail() {
  printf 'PostSteward install blocked: %s\n' "$*" >&2
  exit 2
}

while (($#)); do
  case "$1" in
    --version) (($# >= 2)) || fail "--version requires a value"; VERSION="$2"; shift 2 ;;
    --prefix) (($# >= 2)) || fail "--prefix requires a value"; PREFIX="$2"; shift 2 ;;
    --bin-dir) (($# >= 2)) || fail "--bin-dir requires a value"; BIN_DIR="$2"; shift 2 ;;
    --origin) (($# >= 2)) || fail "--origin requires a value"; ORIGIN="$2"; shift 2 ;;
    --no-onboard) NO_ONBOARD=1; shift ;;
    --no-verify) VERIFY=0; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) fail "unknown option: $1" ;;
  esac
done

case "$PREFIX" in /*) ;; *) fail "--prefix must be absolute" ;; esac
case "$BIN_DIR" in /*) ;; *) fail "--bin-dir must be absolute" ;; esac
case "$ORIGIN" in https://*) ;; *) fail "--origin must be HTTPS" ;; esac

command -v python3 >/dev/null 2>&1 || fail "python3 is required"
python3 - <<'PY' || fail "Python 3.10 or newer is required"
import sys
raise SystemExit(0 if sys.version_info >= (3, 10) else 1)
PY

if command -v curl >/dev/null 2>&1; then
  FETCH='curl -fsSL --proto =https --tlsv1.2'
elif command -v wget >/dev/null 2>&1; then
  FETCH='wget -qO-'
else
  fail "curl or wget is required"
fi

RUNTIME="$PREFIX/client"
CLIENT="$RUNTIME/poststeward.py"
SHIM="$BIN_DIR/poststeward"
RECEIPT="${XDG_STATE_HOME:-$HOME/.local/state}/poststeward/install.json"
CLIENT_URL="$SOURCE_BASE/$VERSION/public/poststeward.py"

printf '%s\n' "PostSteward install plan"
printf '  version: %s\n' "$VERSION"
printf '  source:  %s\n' "$CLIENT_URL"
printf '  origin:  %s\n' "$ORIGIN"
printf '  client:  %s\n' "$CLIENT"
printf '  command: %s\n' "$SHIM"
printf '  config:  %s\n' "${XDG_CONFIG_HOME:-$HOME/.config}/poststeward"
printf '  state:   %s\n' "${XDG_STATE_HOME:-$HOME/.local/state}/poststeward"

if ((DRY_RUN)); then
  printf '%s\n' "Dry run only; no files changed."
  exit 0
fi

umask 077
mkdir -p "$RUNTIME" "$BIN_DIR" "$(dirname "$RECEIPT")"

if [[ -e "$SHIM" ]]; then
  [[ -f "$SHIM" && ! -L "$SHIM" ]] || fail "existing poststeward command is not a plain managed file"
  grep -Fq '# managed-by: poststeward-installer' "$SHIM" ||
    fail "refusing to overwrite unrelated command: $SHIM"
fi

TMP_CLIENT="$(mktemp "$RUNTIME/.poststeward.py.XXXXXX")"
TMP_SHIM="$(mktemp "$BIN_DIR/.poststeward.XXXXXX")"
trap 'rm -f -- "$TMP_CLIENT" "$TMP_SHIM"' EXIT

if [[ "$FETCH" == curl* ]]; then
  curl -fsSL --proto '=https' --tlsv1.2 "$CLIENT_URL" >"$TMP_CLIENT"
else
  wget -qO- "$CLIENT_URL" >"$TMP_CLIENT"
fi
python3 -m py_compile "$TMP_CLIENT"
chmod 0600 "$TMP_CLIENT"
mv -f "$TMP_CLIENT" "$CLIENT"

cat >"$TMP_SHIM" <<EOF
#!/bin/sh
# managed-by: poststeward-installer
set -eu
export POSTSTEWARD_DEFAULT_ORIGIN='$ORIGIN'
exec python3 '$CLIENT' "\$@"
EOF
chmod 0755 "$TMP_SHIM"
mv -f "$TMP_SHIM" "$SHIM"
trap - EXIT

POSTSTEWARD_RECEIPT="$RECEIPT" POSTSTEWARD_VERSION="$VERSION" POSTSTEWARD_CLIENT="$CLIENT" POSTSTEWARD_SHIM="$SHIM" POSTSTEWARD_ORIGIN_VALUE="$ORIGIN" python3 - <<'PY'
from datetime import datetime, timezone
import json, os
from pathlib import Path

path = Path(os.environ["POSTSTEWARD_RECEIPT"])
value = {
    "schema_version": 1,
    "product": "poststeward",
    "source_ref": os.environ["POSTSTEWARD_VERSION"],
    "client": os.environ["POSTSTEWARD_CLIENT"],
    "command": os.environ["POSTSTEWARD_SHIM"],
    "origin": os.environ["POSTSTEWARD_ORIGIN_VALUE"],
    "installed_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
}
tmp = path.with_name(path.name + ".tmp")
tmp.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
tmp.chmod(0o600)
tmp.replace(path)
PY

if ((VERIFY)); then
  "$SHIM" --version
  "$SHIM" help --json >/dev/null
fi

printf '%s\n' "Installed PostSteward command: $SHIM"
if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
  printf '%s\n' "PATH note: add $BIN_DIR to PATH."
fi

if ((NO_ONBOARD)); then
  printf '%s\n' "Onboarding skipped. Run: poststeward onboard"
  exit 0
fi

if [[ -t 0 && -t 1 ]]; then
  "$SHIM" onboard
else
  printf '%s\n' "No interactive terminal detected. Run later: poststeward onboard"
fi
