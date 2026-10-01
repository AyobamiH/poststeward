#!/usr/bin/env bash
set -euo pipefail

VERSION="${POSTSTEWARD_INSTALL_VERSION:-stable}"
PREFIX="${POSTSTEWARD_INSTALL_PREFIX:-${XDG_DATA_HOME:-$HOME/.local/share}/poststeward}"
BIN_DIR="${POSTSTEWARD_BIN_DIR:-$HOME/.local/bin}"
ORIGIN="${POSTSTEWARD_ORIGIN:-https://app.poststeward.com}"
DISTRIBUTION_ORIGIN="${POSTSTEWARD_DISTRIBUTION_ORIGIN:-${POSTSTEWARD_ORIGIN:-https://poststeward.com}}"
REPOSITORY="${POSTSTEWARD_INSTALL_REPOSITORY:-AyobamiH/poststeward}"
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
  --version <channel|ref|sha> Release channel/ref/SHA (default: stable)
  --beta                    Install the beta ref
  --prefix <absolute-path>  Product data prefix (default: ~/.local/share/poststeward)
  --bin-dir <absolute-path> Command directory (default: ~/.local/bin)
  --origin <https-origin>   Hosted PostSteward origin (default: https://app.poststeward.com)
  --no-onboard              Install and verify without starting machine pairing
  --no-verify               Skip candidate runtime smoke verification
  --dry-run                 Resolve and print the plan without changing files
  -h, --help                Show this help
EOF
}

fail() {
  printf 'PostSteward install blocked: %s\n' "$*" >&2
  exit 2
}

while (($#)); do
  case "$1" in
    --version) (($# >= 2)) || fail "--version requires a value"; VERSION="$2"; shift 2 ;;
    --beta) VERSION="beta"; shift ;;
    --prefix) (($# >= 2)) || fail "--prefix requires a value"; PREFIX="$2"; shift 2 ;;
    --bin-dir) (($# >= 2)) || fail "--bin-dir requires a value"; BIN_DIR="$2"; shift 2 ;;
    --origin) (($# >= 2)) || fail "--origin requires a value"; ORIGIN="$2"; DISTRIBUTION_ORIGIN="$2"; shift 2 ;;
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
case "$VERSION" in
  *[!A-Za-z0-9._-]*) fail "--version accepts a tag/ref name or exact 40-character SHA" ;;
esac

command -v python3 >/dev/null 2>&1 || fail "Python 3.10+ is required. macOS: install Python from python.org or an existing Homebrew (brew install python). Ubuntu/WSL: sudo apt install python3. Then rerun; no installation files changed."
command -v tar >/dev/null 2>&1 || fail "tar is required"
python3 - <<'PY' || fail "Python 3.10 or newer is required"
import sys
raise SystemExit(0 if sys.version_info >= (3, 10) else 1)
PY

if command -v curl >/dev/null 2>&1; then
  fetch_file() { curl -fsSL --proto '=https' --tlsv1.2 "$1" -o "$2"; }
elif command -v wget >/dev/null 2>&1; then
  fetch_file() { wget -qO "$2" "$1"; }
else
  fail "curl or wget is required"
fi

TMP="$(mktemp -d)"
cleanup() { rm -rf "$TMP"; }
trap cleanup EXIT

EXPECTED_RUNTIME_TREE_SHA=""
if [[ "$VERSION" = "stable" || "$VERSION" = "beta" ]]; then
  META="$TMP/release.json"
  fetch_file "$DISTRIBUTION_ORIGIN/releases/$VERSION.json" "$META"
  MANIFEST_VALUES="$(python3 - "$META" "$VERSION" <<'PY'
from datetime import datetime, timezone
import json, re, sys
value=json.load(open(sys.argv[1],encoding="utf-8"))
channel=sys.argv[2]
required={"schema_version":1,"product":"poststeward","channel":channel}
if any(value.get(k) != v for k,v in required.items()):
    raise SystemExit("release manifest identity mismatch")
sha=str(value.get("revision") or "").lower()
tree=str(value.get("runtime_tree_sha256") or "").lower()
if not re.fullmatch(r"[0-9a-f]{40}",sha) or not re.fullmatch(r"[0-9a-f]{64}",tree):
    raise SystemExit("release manifest digest/revision invalid")
try:
    expiry=datetime.fromisoformat(str(value["expires_at"]).replace("Z","+00:00"))
except Exception:
    raise SystemExit("release manifest expiry invalid")
if expiry <= datetime.now(timezone.utc):
    raise SystemExit("release manifest expired")
print(sha)
print(tree)
PY
  )" || fail "release channel metadata is invalid, stale or unavailable"
  RESOLVED_SHA="$(printf '%s\n' "$MANIFEST_VALUES" | sed -n '1p')"
  EXPECTED_RUNTIME_TREE_SHA="$(printf '%s\n' "$MANIFEST_VALUES" | sed -n '2p')"
elif [[ "$VERSION" =~ ^[0-9a-fA-F]{40}$ ]]; then
  RESOLVED_SHA="$(printf '%s' "$VERSION" | tr 'A-F' 'a-f')"
else
  META="$TMP/commit.json"
  fetch_file "https://api.github.com/repos/$REPOSITORY/commits/$VERSION" "$META"
  RESOLVED_SHA="$(python3 - "$META" <<'PY'
import json, re, sys
value=json.load(open(sys.argv[1],encoding="utf-8"))
sha=str(value.get("sha") or "")
if not re.fullmatch(r"[0-9a-f]{40}", sha):
    raise SystemExit(2)
print(sha)
PY
  )" || fail "could not resolve requested PostSteward ref"
fi

RELEASES="$PREFIX/releases"
RELEASE="$RELEASES/$RESOLVED_SHA"
CURRENT="$PREFIX/current"
SHIM="$BIN_DIR/poststeward"
RECEIPT="${XDG_STATE_HOME:-$HOME/.local/state}/poststeward/install.json"
ARCHIVE="$TMP/poststeward.tar.gz"
EXTRACTED="$TMP/extracted"
CANDIDATE="$TMP/candidate"

printf '%s\n' "PostSteward install plan"
printf '  requested: %s\n' "$VERSION"
printf '  revision:  %s\n' "$RESOLVED_SHA"
printf '  repository:%s\n' " $REPOSITORY"
printf '  origin:    %s\n' "$ORIGIN"
printf '  release:   %s\n' "$RELEASE"
printf '  command:   %s\n' "$SHIM"
printf '  config:    %s\n' "${XDG_CONFIG_HOME:-$HOME/.config}/poststeward"
printf '  state:     %s\n' "${XDG_STATE_HOME:-$HOME/.local/state}/poststeward"

if ((DRY_RUN)); then
  printf '%s\n' "Dry run only; no files changed."
  exit 0
fi

umask 077
mkdir -p "$RELEASES" "$BIN_DIR" "$(dirname "$RECEIPT")"

if [[ -e "$CURRENT" && ! -L "$CURRENT" ]]; then
  fail "managed current runtime path exists but is not a symlink: $CURRENT"
fi
if [[ -e "$SHIM" ]]; then
  [[ -f "$SHIM" && ! -L "$SHIM" ]] || fail "existing poststeward command is not a plain managed file"
  grep -Fq '# managed-by: poststeward-installer' "$SHIM" ||
    fail "refusing to overwrite unrelated command: $SHIM"
fi

if [[ -L "$CURRENT" ]]; then
  CURRENT_TARGET="$(python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).resolve())' "$CURRENT")"
  CURRENT_REVISION="$(basename "$CURRENT_TARGET")"
  if [[ "$CURRENT_REVISION" != "$RESOLVED_SHA" ]]; then
    MARKER="${POSTSTEWARD_RUNTIME_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/poststeward/runtime}/automation-authority.json"
    if [[ -e "$MARKER" || -L "$MARKER" ]]; then
      python3 - "$MARKER" <<'PY' || fail "local publishing authority is active or cannot be verified; deactivate/review the runtime before changing releases"
import json,sys
from pathlib import Path
path=Path(sys.argv[1])
if not path.is_file() or path.is_symlink():
    raise SystemExit("automation authority marker must be a plain file")
try:
    value=json.loads(path.read_text(encoding="utf-8"))
except Exception:
    raise SystemExit("automation authority marker is unreadable")
if not isinstance(value,dict) or value.get("status") not in {"active","inactive"}:
    raise SystemExit("automation authority marker is invalid")
if value["status"] == "active":
    raise SystemExit("local publishing authority is active")
PY
    fi
    if command -v launchctl >/dev/null 2>&1; then
      for label in com.poststeward.run-due com.poststeward.portfolio-refill com.poststeward.collection com.poststeward.replies; do
        if launchctl print "gui/$(id -u)/$label" >/dev/null 2>&1; then
          fail "PostSteward launch agent $label is loaded; deactivate before changing releases"
        fi
      done
    fi
    if command -v systemctl >/dev/null 2>&1; then
      for unit in         poststeward-run-due.timer         poststeward-portfolio-refill.timer         poststeward-collection.timer         poststeward-replies.timer
      do
        if systemctl --user is-active --quiet "$unit" 2>/dev/null; then
          fail "PostSteward automation timer $unit is active; close runtime authority before changing releases"
        fi
      done
    fi
  fi
fi

if [[ ! -d "$RELEASE" ]]; then
  fetch_file "https://github.com/$REPOSITORY/archive/$RESOLVED_SHA.tar.gz" "$ARCHIVE"
  mkdir -p "$EXTRACTED"
  tar -xzf "$ARCHIVE" -C "$EXTRACTED"
  ROOT_ENTRY="$(find "$EXTRACTED" -mindepth 1 -maxdepth 1 -type d -print -quit)"
  [[ -n "$ROOT_ENTRY" && -d "$ROOT_ENTRY/runtime" ]] ||
    fail "downloaded revision does not contain the PostSteward runtime"
  if find "$ROOT_ENTRY/runtime" -type l -print -quit | grep -q .; then
    fail "runtime archive contains symbolic links; refusing install"
  fi
  cp -R "$ROOT_ENTRY/runtime" "$CANDIDATE"
  [[ -f "$CANDIDATE/poststeward" ]] || fail "candidate has no canonical poststeward entrypoint"
  [[ -f "$CANDIDATE/src/ocpf_post/product_entry.py" ]] || fail "candidate runtime is incomplete"
  [[ -f "$CANDIDATE/POSTSTEWARD_RUNTIME_PROVENANCE.json" ]] || fail "candidate provenance is missing"

  ACTUAL_RUNTIME_TREE_SHA="$(python3 - "$CANDIDATE" <<'PY'
from hashlib import sha256
from pathlib import Path
import sys
root=Path(sys.argv[1])
digest=sha256()
rows=[]
for path in root.rglob("*"):
    if not path.is_file() or path.is_symlink():
        continue
    rel=path.relative_to(root)
    if any(
        part in {"__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", ".git", ".venv", "venv", "dist", "build", "htmlcov"}
        or part.endswith(".egg-info")
        for part in rel.parts
    ):
        continue
    if path.name.endswith(".pyc") or path.name in {".coverage", "coverage.xml", "unittest-results.log"}:
        continue
    rows.append((rel.as_posix(),path))
for name,path in sorted(rows):
    content=path.read_bytes()
    digest.update(name.encode())
    digest.update(b"\0")
    digest.update(str(len(content)).encode())
    digest.update(b"\0")
    digest.update(sha256(content).hexdigest().encode())
    digest.update(b"\n")
print(digest.hexdigest())
PY
  )"
  if [[ -n "$EXPECTED_RUNTIME_TREE_SHA" ]]; then
    [[ "$ACTUAL_RUNTIME_TREE_SHA" = "$EXPECTED_RUNTIME_TREE_SHA" ]] ||
      fail "downloaded embedded runtime does not match the signed-off channel manifest digest"
  fi

  if ((VERIFY)); then
    VERIFY_ROOT="$TMP/verify"
    mkdir -p "$VERIFY_ROOT/config" "$VERIFY_ROOT/state" "$VERIFY_ROOT/setup" "$VERIFY_ROOT/releases"
    POSTSTEWARD_RUNTIME_ROOT="$CANDIDATE" \
    POSTSTEWARD_RUNTIME_CONFIG_DIR="$VERIFY_ROOT/config" \
    POSTSTEWARD_RUNTIME_STATE_DIR="$VERIFY_ROOT/state" \
    POSTSTEWARD_SETUP_STATE_DIR="$VERIFY_ROOT/setup" \
    POSTSTEWARD_RELEASES_DIR="$VERIFY_ROOT/releases" \
      /bin/sh "$CANDIDATE/poststeward" --version >/dev/null
    POSTSTEWARD_RUNTIME_ROOT="$CANDIDATE" \
    POSTSTEWARD_RUNTIME_CONFIG_DIR="$VERIFY_ROOT/config" \
    POSTSTEWARD_RUNTIME_STATE_DIR="$VERIFY_ROOT/state" \
    POSTSTEWARD_SETUP_STATE_DIR="$VERIFY_ROOT/setup" \
    POSTSTEWARD_RELEASES_DIR="$VERIFY_ROOT/releases" \
      /bin/sh "$CANDIDATE/poststeward" help --json >/dev/null
  fi

  mv "$CANDIDATE" "$RELEASE"
  chmod -R go-rwx "$RELEASE" 2>/dev/null || true
fi

if [[ -z "${ACTUAL_RUNTIME_TREE_SHA:-}" ]]; then
  ACTUAL_RUNTIME_TREE_SHA="$(python3 - "$RELEASE" <<'PY'
from hashlib import sha256
from pathlib import Path
import sys
root=Path(sys.argv[1])
digest=sha256()
rows=[]
for path in root.rglob("*"):
    if not path.is_file() or path.is_symlink():
        continue
    rel=path.relative_to(root)
    if any(
        part in {"__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", ".git", ".venv", "venv", "dist", "build", "htmlcov"}
        or part.endswith(".egg-info")
        for part in rel.parts
    ):
        continue
    if path.name.endswith(".pyc") or path.name in {".coverage", "coverage.xml", "unittest-results.log"}:
        continue
    rows.append((rel.as_posix(),path))
for name,path in sorted(rows):
    content=path.read_bytes()
    digest.update(name.encode()); digest.update(b"\0")
    digest.update(str(len(content)).encode()); digest.update(b"\0")
    digest.update(sha256(content).hexdigest().encode()); digest.update(b"\n")
print(digest.hexdigest())
PY
  )"
fi
if [[ -n "$EXPECTED_RUNTIME_TREE_SHA" && "$ACTUAL_RUNTIME_TREE_SHA" != "$EXPECTED_RUNTIME_TREE_SHA" ]]; then
  fail "existing embedded runtime does not match the channel manifest digest"
fi

TMP_LINK="$PREFIX/.current.$.tmp"
ln -s "$RELEASE" "$TMP_LINK"
python3 - "$TMP_LINK" "$CURRENT" <<'PY'
import os, sys
os.replace(sys.argv[1], sys.argv[2])
PY

TMP_SHIM="$(mktemp "$BIN_DIR/.poststeward.XXXXXX")"
python3 - "$TMP_SHIM" "$ORIGIN" "$CURRENT" "$RESOLVED_SHA" <<'PYSHIM'
from pathlib import Path
import shlex,sys
path,origin,current,revision=sys.argv[1:]
Path(path).write_text("#!/bin/sh\n# managed-by: poststeward-installer\nset -eu\n"
    + "export POSTSTEWARD_ORIGIN=" + shlex.quote(origin) + "\n"
    + "export POSTSTEWARD_RUNTIME_ROOT=" + shlex.quote(current) + "\n"
    + "export POSTSTEWARD_RUNTIME_RELEASE_SHA=" + shlex.quote(revision) + "\n"
    + "exec /bin/sh " + shlex.quote(current + "/poststeward") + ' "$@"\n')
PYSHIM
chmod 0755 "$TMP_SHIM"
mv -f "$TMP_SHIM" "$SHIM"

POSTSTEWARD_INSTALL_PREFIX_VALUE="$PREFIX" \
POSTSTEWARD_BIN_DIR_VALUE="$BIN_DIR" \
POSTSTEWARD_RECEIPT="$RECEIPT" \
POSTSTEWARD_REQUESTED="$VERSION" \
POSTSTEWARD_REVISION="$RESOLVED_SHA" \
POSTSTEWARD_RELEASE="$RELEASE" \
POSTSTEWARD_ORIGIN_VALUE="$ORIGIN" \
POSTSTEWARD_REPOSITORY="$REPOSITORY" \
POSTSTEWARD_RUNTIME_TREE_SHA="$ACTUAL_RUNTIME_TREE_SHA" \
POSTSTEWARD_RUNTIME_PROVENANCE="$RELEASE/POSTSTEWARD_RUNTIME_PROVENANCE.json" \
python3 - <<'PY'
from datetime import datetime, timezone
import json, os
from pathlib import Path
path=Path(os.environ["POSTSTEWARD_RECEIPT"])
previous=json.loads(path.read_text()) if path.is_file() and not path.is_symlink() else {}
changed=previous.get("resolved_revision") != os.environ["POSTSTEWARD_REVISION"]
value={
 "schema_version":1,
 "product":"poststeward",
 "repository":os.environ["POSTSTEWARD_REPOSITORY"],
 "requested_ref":os.environ["POSTSTEWARD_REQUESTED"],
 "resolved_revision":os.environ["POSTSTEWARD_REVISION"],
 "runtime_tree_sha256":os.environ["POSTSTEWARD_RUNTIME_TREE_SHA"],
 "runtime_provenance":json.load(open(os.environ["POSTSTEWARD_RUNTIME_PROVENANCE"],encoding="utf-8")),
 "release_path":os.environ["POSTSTEWARD_RELEASE"],
 "install_prefix":os.environ["POSTSTEWARD_INSTALL_PREFIX_VALUE"],
 "bin_dir":os.environ["POSTSTEWARD_BIN_DIR_VALUE"],
 "previous_revision":previous.get("resolved_revision") if changed else previous.get("previous_revision"),
 "previous_runtime_tree_sha256":previous.get("runtime_tree_sha256") if changed else previous.get("previous_runtime_tree_sha256"),
 "origin":os.environ["POSTSTEWARD_ORIGIN_VALUE"],
 "installed_at":datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00","Z"),
}
tmp=path.with_name(path.name+".tmp")
tmp.write_text(json.dumps(value,sort_keys=True,separators=(",",":"))+"\n",encoding="utf-8")
tmp.chmod(0o600)
tmp.replace(path)
PY

"$SHIM" --version
printf '%s\n' "Installed PostSteward runtime: $RESOLVED_SHA"
printf '%s\n' "Command: $SHIM"
if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
  printf '%s\n' "PATH note: add $BIN_DIR to PATH."
fi

if ((NO_ONBOARD)); then
  printf '%s\n' "Onboarding skipped. Run: poststeward onboard"
  exit 0
fi

if [[ -r /dev/tty && -w /dev/tty ]]; then
  "$SHIM" onboard </dev/tty >/dev/tty 2>&1 || {
    printf '%s\n' "Runtime installed. Onboarding did not finish; resume with: poststeward onboard" >&2
    exit 3
  }
else
  printf '%s\n' "No interactive terminal detected. Run later: poststeward onboard"
fi
