#!/usr/bin/env bash
set -euo pipefail

hf_dataset="${ENTERPRISEOPS_HF_DATASET:-ServiceNow-AI/EnterpriseOps-Gym}"
revision="${ENTERPRISEOPS_REVISION:-main}"
raw_dir="${ENTERPRISEOPS_RAW_DIR:-external/enterpriseops-gym/archives/hf_dataset}"
tasks_dir="${ENTERPRISEOPS_TASKS_DIR:-external/enterpriseops-gym/tasks}"
downloader="${ENTERPRISEOPS_DOWNLOADER:-wget}"
materialize="${ENTERPRISEOPS_MATERIALIZE:-1}"
python_bin="${PYTHON:-.venv/bin/python}"
hf_bin="${HF_CLI:-}"
wget_timeout="${WGET_TIMEOUT:-45}"
wget_tries="${WGET_TRIES:-3}"
curl_retries="${CURL_RETRIES:-3}"
base_url="${ENTERPRISEOPS_HF_BASE:-https://huggingface.co/datasets}"
allow_mirror="${ENTERPRISEOPS_ALLOW_MIRROR:-0}"

read -r -a modes <<< "${ENTERPRISEOPS_MODES:-oracle plus_5_tools plus_10_tools plus_15_tools}"
read -r -a domains <<< "${ENTERPRISEOPS_DOMAINS:-calendar csm drive email hr itsm teams hybrid}"

case "${base_url}" in
  https://huggingface.co/datasets|https://hf.co/datasets)
    ;;
  *)
    if [ "${allow_mirror}" != "1" ]; then
      echo "refusing non-official Hugging Face base URL: ${base_url}" >&2
      echo "set ENTERPRISEOPS_ALLOW_MIRROR=1 to explicitly opt in to a mirror transport" >&2
      exit 2
    fi
    case "${base_url}" in
      https://*/datasets)
        ;;
      *)
        echo "mirror base URL must be an https URL ending in /datasets: ${base_url}" >&2
        exit 2
        ;;
    esac
    ;;
esac

if [ ! -x "${python_bin}" ]; then
  python_bin="python3"
fi
if [ -z "${hf_bin}" ]; then
  if [ -x ".venv/bin/hf" ]; then
    hf_bin=".venv/bin/hf"
  else
    hf_bin="hf"
  fi
fi

mkdir -p "${raw_dir}" "${tasks_dir}"
manifest="${raw_dir}/download_manifest.tsv"
if [ ! -f "${manifest}" ]; then
  printf 'mode\tdomain\turl\tpath\tbytes\tsha256\n' > "${manifest}"
fi

is_valid_parquet() {
  local path="$1"
  "${python_bin}" - "$path" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
if not path.exists() or path.stat().st_size < 8:
    raise SystemExit(1)
data = path.read_bytes()
raise SystemExit(0 if data[:4] == b"PAR1" and data[-4:] == b"PAR1" else 1)
PY
}

download_wget() {
  local url="$1"
  local output="$2"
  wget \
    --timeout="${wget_timeout}" \
    --tries="${wget_tries}" \
    --user-agent="${WGET_USER_AGENT:-Mozilla/5.0}" \
    -O "${output}" \
    "${url}"
}

download_curl() {
  local url="$1"
  local output="$2"
  curl \
    -L \
    --fail \
    --retry "${curl_retries}" \
    --connect-timeout "${CURL_CONNECT_TIMEOUT:-30}" \
    --output "${output}" \
    "${url}"
}

download_hf_cli() {
  local mode="$1"
  local file_name="$2"
  "${hf_bin}" download "${hf_dataset}" "${mode}/${file_name}" --repo-type dataset --revision "${revision}" --local-dir "${raw_dir}" --max-workers 1
}

download_one() {
  local mode="$1"
  local domain="$2"
  local file_name="${domain}-00000-of-00001.parquet"
  local output_dir="${raw_dir}/${mode}"
  local output="${output_dir}/${file_name}"
  local partial="${output}.part"
  local url="${base_url}/${hf_dataset}/resolve/${revision}/${mode}/${file_name}?download=true"

  mkdir -p "${output_dir}"
  if is_valid_parquet "${output}"; then
    echo "already have ${output}"
    return 0
  fi

  rm -f "${partial}"
  echo "downloading official EnterpriseOps-Gym split ${mode}/${domain}"
  case "${downloader}" in
    wget)
      download_wget "${url}" "${partial}"
      ;;
    curl)
      download_curl "${url}" "${partial}"
      ;;
    hf)
      download_hf_cli "${mode}" "${file_name}"
      ;;
    auto)
      download_wget "${url}" "${partial}" || download_curl "${url}" "${partial}" || download_hf_cli "${mode}" "${file_name}"
      ;;
    *)
      echo "unsupported ENTERPRISEOPS_DOWNLOADER=${downloader}; use wget, curl, hf, datasets, or auto" >&2
      exit 2
      ;;
  esac

  if [ -f "${partial}" ]; then
    if ! is_valid_parquet "${partial}"; then
      echo "downloaded file is not a valid parquet file: ${partial}" >&2
      exit 4
    fi
    mv "${partial}" "${output}"
  fi

  if ! is_valid_parquet "${output}"; then
    echo "missing or invalid parquet after download: ${output}" >&2
    exit 4
  fi

  local bytes sha
  bytes="$(wc -c < "${output}")"
  sha="$(sha256sum "${output}" | awk '{print $1}')"
  awk -v mode="${mode}" -v domain="${domain}" 'BEGIN{FS=OFS="\t"} NR==1 || !($1==mode && $2==domain)' "${manifest}" > "${manifest}.tmp"
  mv "${manifest}.tmp" "${manifest}"
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' "${mode}" "${domain}" "${url}" "${output}" "${bytes}" "${sha}" >> "${manifest}"
}

if [ "${downloader}" != "datasets" ]; then
  for mode in "${modes[@]}"; do
    for domain in "${domains[@]}"; do
      download_one "${mode}" "${domain}"
    done
  done
fi

if [ "${materialize}" = "1" ]; then
  source_type="parquet"
  if [ "${downloader}" = "datasets" ]; then
    source_type="datasets"
  fi
  "${python_bin}" scripts/materialize_enterpriseops_tasks.py \
    --input-dir "${raw_dir}" \
    --output-dir "${tasks_dir}" \
    --hf-dataset "${hf_dataset}" \
    --modes "${modes[@]}" \
    --domains "${domains[@]}" \
    --source "${source_type}" \
    --overwrite
fi
