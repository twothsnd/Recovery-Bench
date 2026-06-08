#!/usr/bin/env bash
set -euo pipefail

APPWORLD_REF="${APPWORLD_REF:-main}"
TAU2_BENCH_REF="${TAU2_BENCH_REF:-main}"
CLAWSBENCH_REF="${CLAWSBENCH_REF:-main}"
ENTERPRISEOPS_GYM_REF="${ENTERPRISEOPS_GYM_REF:-main}"

scripts/download_benchmark_archive.sh \
  appworld \
  "https://github.com/StonyBrookNLP/appworld/archive/refs/heads/${APPWORLD_REF}.tar.gz" \
  "appworld-${APPWORLD_REF}.tar.gz"

scripts/download_benchmark_archive.sh \
  tau-bench \
  "https://github.com/sierra-research/tau2-bench/archive/refs/heads/${TAU2_BENCH_REF}.tar.gz" \
  "tau2-bench-${TAU2_BENCH_REF}.tar.gz"

scripts/download_benchmark_archive.sh \
  clawsbench \
  "https://github.com/benchflow-ai/ClawsBench/archive/refs/heads/${CLAWSBENCH_REF}.tar.gz" \
  "ClawsBench-${CLAWSBENCH_REF}.tar.gz"

scripts/download_benchmark_archive.sh \
  enterpriseops-gym \
  "https://github.com/ServiceNow/EnterpriseOps-Gym/archive/refs/heads/${ENTERPRISEOPS_GYM_REF}.tar.gz" \
  "EnterpriseOps-Gym-${ENTERPRISEOPS_GYM_REF}.tar.gz"
