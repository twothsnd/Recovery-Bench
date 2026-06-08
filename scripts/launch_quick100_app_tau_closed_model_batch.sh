#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

RUN_ID="${1:-quick100-appworld-taubench-20260525}"
OUTPUT_ROOT="runs/${RUN_ID}"
LOG_DIR="runs/logs"
LOG_FILE="${LOG_DIR}/${RUN_ID}.log"
SUITE="${RECOVERY_BENCH_SUITE:-official}"
SAMPLE_SEED="${RECOVERY_BENCH_SAMPLE_SEED:-recovery-bench-quick100-v1}"

mkdir -p "$LOG_DIR"
export PYTHONUNBUFFERED=1
export PYTHONPATH="${PYTHONPATH:-src}"

{
  date "+%F %T %Z"
  echo "launch_quick100_app_tau_closed_model_batch run_id=${RUN_ID} output_root=${OUTPUT_ROOT} suite=${SUITE} sample_seed=${SAMPLE_SEED}"
  .venv/bin/python -u scripts/run_closed_model_batch.py \
    --model sonnet46 \
    --model opus46 \
    --benchmark appworld \
    --benchmark tau-bench \
    --suite "$SUITE" \
    --benchmark-task-count 100 \
    --sample-seed "$SAMPLE_SEED" \
    --skip-existing \
    --output-root "$OUTPUT_ROOT"
} >> "$LOG_FILE" 2>&1
