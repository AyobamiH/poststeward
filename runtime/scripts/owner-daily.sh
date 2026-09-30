#!/usr/bin/env bash
# Report only: no Git, timer, admission, refill or publishing operations.
set -euo pipefail
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec python3 -B "$SCRIPT_DIR/owner-daily.py" --save "$@"
