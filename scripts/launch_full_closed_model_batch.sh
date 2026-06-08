#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

RUN_ID="${1:-official-closed-model-20260524}"
OUTPUT_ROOT="runs/${RUN_ID}"
LOG_DIR="runs/logs"
LOG_FILE="${LOG_DIR}/${RUN_ID}.log"
SUITE="${RECOVERY_BENCH_SUITE:-official}"

mkdir -p "$LOG_DIR"
export PYTHONUNBUFFERED=1
export PYTHONPATH="${PYTHONPATH:-src}"

{
  date "+%F %T %Z"
  echo "launch_full_closed_model_batch run_id=${RUN_ID} output_root=${OUTPUT_ROOT} suite=${SUITE}"
  .venv/bin/python -u scripts/run_closed_model_batch.py \
    --model sonnet \
    --model opus \
    --benchmark appworld \
    --benchmark tau-bench \
    --benchmark enterpriseops-gym \
    --suite "$SUITE" \
    --skip-existing \
    --output-root "$OUTPUT_ROOT"
} >> "$LOG_FILE" 2>&1
