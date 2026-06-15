#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 --task-id ID --task-path PATH --image IMAGE --session-id ID" >&2
}

TASK_ID=""
TASK_PATH=""
IMAGE=""
SESSION_ID=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --task-id) TASK_ID="$2"; shift 2 ;;
    --task-path) TASK_PATH="$2"; shift 2 ;;
    --image) IMAGE="$2"; shift 2 ;;
    --session-id) SESSION_ID="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage; exit 2 ;;
  esac
done

[[ -n "$TASK_ID" && -n "$TASK_PATH" && -n "$SESSION_ID" ]] || { usage; exit 2; }
[[ -n "${TB2_VM_RESET_COMMAND:-}" ]] || {
  echo "TB2_VM_RESET_COMMAND is required for strict VM reset." >&2
  exit 2
}

export TB2_TASK_ID="$TASK_ID"
export TB2_TASK_PATH="$TASK_PATH"
export TB2_IMAGE="$IMAGE"
export TB2_SESSION_ID="$SESSION_ID"

bash -lc "$TB2_VM_RESET_COMMAND"
