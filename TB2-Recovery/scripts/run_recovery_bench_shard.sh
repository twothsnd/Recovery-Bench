#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 4 ]]; then
  echo "Usage: $0 SHARD_INDEX N_SHARDS CONFIG OUTPUT_DIR" >&2
  exit 2
fi

SHARD_INDEX="$1"
N_SHARDS="$2"
CONFIG="$3"
OUTPUT_DIR="$4"

ROOT="/data/xiewei/Recovery-Bench"
DATASET="${TB2_DATASET_PATH:-${ROOT}/external/terminal-bench-2}"
HARBOR_SITE="${HARBOR_PYTHONPATH:-/home/xiewei/.local/share/uv/tools/harbor/lib/python3.13/site-packages}"

export PYTHONPATH="${ROOT}/src:${ROOT}/TB2-Recovery:${HARBOR_SITE}:${PYTHONPATH:-}"
export OPENAI_API_KEY="${OPENAI_API_KEY:-EMPTY}"

mapfile -t tasks < <(
  find "${DATASET}" -maxdepth 2 -type f -name task.toml -printf '%h\n' \
    | xargs -n1 basename \
    | sort
)

args=()
selected=0
for i in "${!tasks[@]}"; do
  if (( i % N_SHARDS == SHARD_INDEX )); then
    args+=(--task-id "${tasks[$i]}")
    selected=$((selected + 1))
  fi
done

echo "shard_index=${SHARD_INDEX}"
echo "n_shards=${N_SHARDS}"
echo "total_tasks=${#tasks[@]}"
echo "selected_tasks=${selected}"
echo "config=${CONFIG}"
echo "output_dir=${OUTPUT_DIR}"
printf 'tasks='
printf '%s ' "${args[@]}"
printf '\n'

exec "${ROOT}/.venv/bin/recovery-bench" suite \
  --config "${CONFIG}" \
  --output-dir "${OUTPUT_DIR}" \
  "${args[@]}"
