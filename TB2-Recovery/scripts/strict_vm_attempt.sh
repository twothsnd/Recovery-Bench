#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage: strict_vm_attempt.sh --task-id ID --task-path PATH --attempt-dir DIR \
  --result-path PATH --memory-path PATH --model-name NAME --api-base URL \
  --attempt-index N --protocol MODE --session-id ID
USAGE
}

TASK_ID=""
TASK_PATH=""
ATTEMPT_DIR=""
RESULT_PATH=""
MEMORY_PATH=""
MODEL_NAME=""
API_BASE=""
ATTEMPT_INDEX=""
PROTOCOL=""
SESSION_ID=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --task-id) TASK_ID="$2"; shift 2 ;;
    --task-path) TASK_PATH="$2"; shift 2 ;;
    --attempt-dir) ATTEMPT_DIR="$2"; shift 2 ;;
    --result-path) RESULT_PATH="$2"; shift 2 ;;
    --memory-path) MEMORY_PATH="$2"; shift 2 ;;
    --model-name) MODEL_NAME="$2"; shift 2 ;;
    --api-base) API_BASE="$2"; shift 2 ;;
    --attempt-index) ATTEMPT_INDEX="$2"; shift 2 ;;
    --protocol) PROTOCOL="$2"; shift 2 ;;
    --session-id) SESSION_ID="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage; exit 2 ;;
  esac
done

[[ -n "$TASK_ID" && -n "$TASK_PATH" && -n "$ATTEMPT_DIR" && -n "$RESULT_PATH" ]] || { usage; exit 2; }
[[ -n "${TB2_VM_ATTEMPT_COMMAND:-}" ]] || {
  echo "TB2_VM_ATTEMPT_COMMAND is required for strict VM attempts." >&2
  exit 2
}

mkdir -p "$ATTEMPT_DIR"

export TB2_TASK_ID="$TASK_ID"
export TB2_TASK_PATH="$TASK_PATH"
export TB2_ATTEMPT_DIR="$ATTEMPT_DIR"
export TB2_RESULT_PATH="$RESULT_PATH"
export TB2_MEMORY_PATH="$MEMORY_PATH"
export TB2_MODEL_NAME="$MODEL_NAME"
export TB2_API_BASE="$API_BASE"
export TB2_ATTEMPT_INDEX="$ATTEMPT_INDEX"
export TB2_PROTOCOL="$PROTOCOL"
export TB2_SESSION_ID="$SESSION_ID"

bash -lc "$TB2_VM_ATTEMPT_COMMAND"

if [[ ! -s "$RESULT_PATH" ]]; then
  echo "Strict VM attempt command must write JSON to TB2_RESULT_PATH=$RESULT_PATH" >&2
  exit 1
fi

python3 - "$RESULT_PATH" <<'PY'
import json
import sys
path = sys.argv[1]
with open(path, "r", encoding="utf-8") as fh:
    data = json.load(fh)
if not isinstance(data, dict):
    raise SystemExit("strict VM result must be a JSON object")
if not (data.get("snapshot_id") or data.get("pre_score_snapshot_id")):
    raise SystemExit("strict VM result must contain snapshot_id or pre_score_snapshot_id")
if "verifier_result" not in data and "score" not in data and "reward" not in data and "success" not in data:
    raise SystemExit("strict VM result must contain official verifier_result, score, reward, or success")
print(json.dumps(data, ensure_ascii=False))
PY
