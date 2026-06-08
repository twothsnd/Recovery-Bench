#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

RUN_ID="${1:-quick100-closed-model-46-fixed-20260525}"
RUN_SESSION="${RECOVERY_BENCH_RUN_SESSION:-recovery_quick100_resume}"
WATCH_SESSION="${RECOVERY_BENCH_WATCH_SESSION:-recovery_quick100_watchdog}"
TOKEN_SHA="${RECOVERY_BENCH_ANTHROPIC_TOKEN_SHA:-}"
RETRY_SECONDS="${RECOVERY_BENCH_WATCHDOG_RETRY_SECONDS:-1800}"
POLL_SECONDS="${RECOVERY_BENCH_WATCHDOG_POLL_SECONDS:-60}"
LOG_DIR="runs/logs"
RUN_LOG="${LOG_DIR}/${RUN_ID}.log"
WATCH_LOG="${LOG_DIR}/${RUN_ID}.watchdog.log"
STATE_FILE="${LOG_DIR}/${RUN_ID}.watchdog.state"

mkdir -p "$LOG_DIR"
touch "$RUN_LOG"

if [[ -z "$TOKEN_SHA" ]]; then
  echo "RECOVERY_BENCH_ANTHROPIC_TOKEN_SHA must be set to the sha256 prefix of the token to use." >&2
  exit 2
fi

log() {
  printf '%s %s\n' "$(date '+%F %T %Z')" "$*"
}

runner_running() {
  tmux has-session -t "$RUN_SESSION" 2>/dev/null && return 0
  pgrep -f "scripts/run_closed_model_batch.py .*--output-root runs/${RUN_ID}" >/dev/null 2>&1
}

start_runner() {
  if runner_running; then
    log "runner already running session=${RUN_SESSION}"
    return 0
  fi
  log "starting runner session=${RUN_SESSION} run_id=${RUN_ID} token_sha=${TOKEN_SHA}"
  tmux new-session -d -s "$RUN_SESSION" \
    "cd '$ROOT_DIR' && RECOVERY_BENCH_ANTHROPIC_TOKEN_SHA='$TOKEN_SHA' bash scripts/resume_quick100_closed_model_46.sh '$RUN_ID'"
}

latest_log_chunk() {
  local size offset
  size="$(stat -c '%s' "$RUN_LOG" 2>/dev/null || printf 0)"
  offset=0
  if [[ -f "$STATE_FILE" ]]; then
    offset="$(cat "$STATE_FILE" 2>/dev/null || printf 0)"
  fi
  if [[ "$offset" -gt "$size" ]]; then
    offset=0
  fi
  tail -c +"$((offset + 1))" "$RUN_LOG" 2>/dev/null || true
  printf '%s' "$size" > "$STATE_FILE"
}

has_latest_successful_batch_done() {
  local last_terminal
  last_terminal="$(
    rg -n 'batch_done|fatal_exit|combo_fail' "$RUN_LOG" 2>/dev/null | tail -n 1 || true
  )"
  [[ "$last_terminal" == *"batch_done failures=[]"* ]]
}

main() {
  log "watchdog_start run_id=${RUN_ID} run_session=${RUN_SESSION} watch_session=${WATCH_SESSION} retry_seconds=${RETRY_SECONDS} poll_seconds=${POLL_SECONDS} token_sha=${TOKEN_SHA}"
  stat -c '%s' "$RUN_LOG" > "$STATE_FILE"

  local access_denied_seen=0
  local seen_runner=0
  while true; do
    chunk="$(latest_log_chunk)"
    if [[ "$chunk" == *"Access denied. Insufficient permissions."* ]]; then
      access_denied_seen=1
      log "detected_access_denied"
    fi

    if runner_running; then
      seen_runner=1
      sleep "$POLL_SECONDS"
      continue
    fi

    if has_latest_successful_batch_done; then
      log "runner_complete; watchdog remains idle"
      sleep "$POLL_SECONDS"
      continue
    fi

    if [[ "$access_denied_seen" -eq 1 ]]; then
      log "runner_stopped_after_access_denied; retrying_after_seconds=${RETRY_SECONDS}"
      sleep "$RETRY_SECONDS"
      access_denied_seen=0
      start_runner
      sleep "$POLL_SECONDS"
      continue
    fi

    if [[ "$seen_runner" -eq 0 ]]; then
      log "runner_not_running_at_watchdog_start; starting_runner"
      start_runner
      sleep "$POLL_SECONDS"
      continue
    fi

    log "runner_stopped_without_new_access_denied; not_restarting"
    sleep "$POLL_SECONDS"
  done
}

main >> "$WATCH_LOG" 2>&1
