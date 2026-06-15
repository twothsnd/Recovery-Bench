#!/usr/bin/env bash
set -euo pipefail

SNAPSHOT_ID=""
SESSION_ID=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --snapshot-id) SNAPSHOT_ID="$2"; shift 2 ;;
    --session-id) SESSION_ID="$2"; shift 2 ;;
    -h|--help) echo "Usage: $0 --snapshot-id ID --session-id ID"; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done

[[ -n "$SNAPSHOT_ID" && -n "$SESSION_ID" ]] || {
  echo "Usage: $0 --snapshot-id ID --session-id ID" >&2
  exit 2
}

if [[ -z "${TB2_VM_DISCARD_COMMAND:-}" ]]; then
  exit 0
fi

export TB2_SNAPSHOT_ID="$SNAPSHOT_ID"
export TB2_SESSION_ID="$SESSION_ID"

bash -lc "$TB2_VM_DISCARD_COMMAND"
