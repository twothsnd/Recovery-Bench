#!/usr/bin/env bash
set -euo pipefail

python_bin="${PYTHON:-python3}"
appworld_src="${APPWORLD_SRC:-external/appworld/src}"
tau_bench_src="${TAU_BENCH_SRC:-external/tau-bench/src}"
install_appworld="${INSTALL_APPWORLD:-1}"
install_tau_bench="${INSTALL_TAU_BENCH:-1}"

read -r -a pip_extra_args <<< "${PIP_EXTRA_ARGS:-}"

if [ "${install_appworld}" = "1" ]; then
  if [ ! -f "${appworld_src}/pyproject.toml" ]; then
    echo "missing AppWorld source: ${appworld_src}" >&2
    echo "run scripts/download_first_benchmarks.sh first" >&2
    exit 2
  fi
  "${python_bin}" -m pip install "${pip_extra_args[@]}" -e "${appworld_src}"
fi

if [ "${install_tau_bench}" = "1" ]; then
  if [ ! -f "${tau_bench_src}/pyproject.toml" ]; then
    echo "missing tau-bench source: ${tau_bench_src}" >&2
    echo "run scripts/download_first_benchmarks.sh first" >&2
    exit 2
  fi
  "${python_bin}" -m pip install "${pip_extra_args[@]}" -e "${tau_bench_src}[gym]"
fi

PYTHONPATH=src "${python_bin}" -m recovery_bench.cli list-benchmarks
