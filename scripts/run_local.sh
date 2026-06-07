#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$ROOT_DIR/.venv"
CONFIG_FILE="$ROOT_DIR/config.yaml"
LOG_DIR="$ROOT_DIR/logs"
TIMESTAMP="$(date +%Y-%m-%d-%H%M%S)"
RUN_LOG="$LOG_DIR/$TIMESTAMP-run.log"
LATEST_LOG="$LOG_DIR/latest.log"

if [ ! -f "$CONFIG_FILE" ]; then
  echo "Missing config.yaml. Copy config.example.yaml to config.yaml and configure it first." >&2
  exit 1
fi

if [ ! -d "$VENV_DIR" ]; then
  echo "Missing .venv. Run scripts/install_local.sh first." >&2
  exit 1
fi

mkdir -p "$ROOT_DIR/data" "$LOG_DIR"

RUN_LABEL="LIVE RUN"
for arg in "$@"; do
  if [ "$arg" = "--dry-run" ]; then
    RUN_LABEL="DRY RUN"
    break
  fi
done

if [ -f "$ROOT_DIR/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.env"
  set +a
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

echo "[$RUN_LABEL] Starting local campaign run at $TIMESTAMP" | tee "$RUN_LOG"
set +e
python "$ROOT_DIR/run_campaign.py" --config "$CONFIG_FILE" --mode full "$@" 2>&1 | tee -a "$RUN_LOG"
EXIT_CODE=${PIPESTATUS[0]}
set -e
cp "$RUN_LOG" "$LATEST_LOG"
if [ "$EXIT_CODE" -ne 0 ]; then
  echo "Run failed. See $RUN_LOG" >&2
  exit "$EXIT_CODE"
fi
echo "Run complete. Log: $RUN_LOG"
