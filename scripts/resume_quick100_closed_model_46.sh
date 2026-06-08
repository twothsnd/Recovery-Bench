#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

RUN_ID="${1:-quick100-closed-model-46-fixed-20260525}"
OUTPUT_ROOT="runs/${RUN_ID}"
LOG_DIR="runs/logs"
LOG_FILE="${LOG_DIR}/${RUN_ID}.log"
SUITE="${RECOVERY_BENCH_SUITE:-official}"
SAMPLE_SEED="${RECOVERY_BENCH_SAMPLE_SEED:-recovery-bench-quick100-v1}"
TOKEN_SHA="${RECOVERY_BENCH_ANTHROPIC_TOKEN_SHA:-}"

if [[ -z "$TOKEN_SHA" ]]; then
  echo "RECOVERY_BENCH_ANTHROPIC_TOKEN_SHA must be set to the sha256 prefix of the token to use." >&2
  exit 2
fi

mkdir -p "$LOG_DIR"

TOKEN="$(
  TOKEN_SHA="$TOKEN_SHA" .venv/bin/python - <<'PY'
import hashlib
import os
from pathlib import Path

target = os.environ["TOKEN_SHA"]
names = ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY")

for name in names:
    value = os.environ.get(name)
    if value and hashlib.sha256(value.encode()).hexdigest()[:8] == target:
        print(value)
        raise SystemExit(0)

for path in (Path.home() / ".bash_history", Path.home() / ".bashrc", Path.home() / ".profile"):
    if not path.exists():
        continue
    try:
        lines = path.read_text(errors="ignore").splitlines()
    except Exception:
        continue
    for line in reversed(lines):
        for name in names:
            marker = f"{name}="
            if marker not in line:
                continue
            value = line.split(marker, 1)[1].strip()
            if value.startswith(("\"", "'")):
                value = value[1:]
            value = value.split()[0].rstrip(";").strip("\"'")
            if hashlib.sha256(value.encode()).hexdigest()[:8] == target:
                print(value)
                raise SystemExit(0)

raise SystemExit(f"Anthropic token with sha8={target} was not found")
PY
)"

export ANTHROPIC_AUTH_TOKEN="$TOKEN"
export ANTHROPIC_API_KEY="$TOKEN"
export ANTHROPIC_BASE_URL="${ANTHROPIC_BASE_URL:-https://hk-speed-01.code-next.akclau.de}"
export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=true
export PYTHONUNBUFFERED=1
export PYTHONPATH="${PYTHONPATH:-src}"

{
  date "+%F %T %Z"
  echo "resume_quick100_closed_model_46 run_id=${RUN_ID} output_root=${OUTPUT_ROOT} suite=${SUITE} sample_seed=${SAMPLE_SEED} token_sha=${TOKEN_SHA}"
  .venv/bin/python -u scripts/run_closed_model_batch.py \
    --model sonnet46 \
    --model opus46 \
    --benchmark appworld \
    --benchmark tau-bench \
    --benchmark enterpriseops-gym \
    --suite "$SUITE" \
    --benchmark-task-count 100 \
    --sample-seed "$SAMPLE_SEED" \
    --skip-existing \
    --output-root "$OUTPUT_ROOT"
} >> "$LOG_FILE" 2>&1
