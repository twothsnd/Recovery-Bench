#!/usr/bin/env bash
set -euo pipefail

ROOT="${ROOT:-/data/xiewei/Recovery-Bench}"
MODEL_PATH="${MODEL_PATH:-/data/xiewei/models/Qwen3.5-9B}"
VENV_PYTHON="${VENV_PYTHON:-${ROOT}/.venv-vllm019-py310/bin/python}"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-18080}"
GPUS="${GPUS:-4,5,6,7}"
TP_SIZE="${TP_SIZE:-4}"
MAX_MODEL_LEN="${MAX_MODEL_LEN:-131072}"
GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-0.95}"

export CUDA_VISIBLE_DEVICES="${GPUS}"
export HF_HOME="${HF_HOME:-${ROOT}/TB2-Recovery/.cache/hf}"
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-${ROOT}/TB2-Recovery/.cache}"
export VLLM_CACHE_ROOT="${VLLM_CACHE_ROOT:-${ROOT}/TB2-Recovery/.cache/vllm}"
export VLLM_WORKER_MULTIPROC_METHOD="${VLLM_WORKER_MULTIPROC_METHOD:-spawn}"

exec "${VENV_PYTHON}" -m vllm.entrypoints.openai.api_server \
  --model "${MODEL_PATH}" \
  --served-model-name Qwen3.5-9B \
  --host "${HOST}" \
  --port "${PORT}" \
  --dtype bfloat16 \
  --max-model-len "${MAX_MODEL_LEN}" \
  --tensor-parallel-size "${TP_SIZE}" \
  --gpu-memory-utilization "${GPU_MEMORY_UTILIZATION}" \
  --max-num-seqs 1 \
  --enforce-eager \
  --disable-custom-all-reduce \
  --language-model-only \
  --reasoning-parser qwen3 \
  --no-enable-log-requests
