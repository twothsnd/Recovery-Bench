#!/usr/bin/env bash
set -euo pipefail

python_bin="${PYTHON:-.venv/bin/python}"
appworld_root="${APPWORLD_ROOT:-${PWD}}"
data_version="${APPWORLD_DATA_VERSION:-0.2.0}"
archive_dir="${APPWORLD_ARCHIVE_DIR:-external/appworld/archives}"
bundle="${archive_dir}/data-${data_version}.bundle"
partial="${bundle}.part"
partial_next="${partial}.next"
default_host="appworld.dev.s3.amazonaws.com"
default_url="https://${default_host}/data-${data_version}.bundle"
s3_ip="${APPWORLD_S3_IP:-}"
if [ -n "${s3_ip}" ]; then
  url="${APPWORLD_DATA_URL:-https://${s3_ip}/data-${data_version}.bundle}"
  host_header="${APPWORLD_HOST_HEADER:-${default_host}}"
else
  url="${APPWORLD_DATA_URL:-${default_url}}"
  host_header="${APPWORLD_HOST_HEADER:-}"
fi
download_mode="${APPWORLD_DOWNLOAD_MODE:-resume}"
downloader="${APPWORLD_DOWNLOADER:-wget}"
download_jobs="${APPWORLD_DOWNLOAD_JOBS:-16}"
wget_timeout="${WGET_TIMEOUT:-30}"
wget_tries="${WGET_TRIES:-3}"
wget_user_agent="${WGET_USER_AGENT:-Mozilla/5.0}"
parts_dir="${bundle}.parts"

mkdir -p "${archive_dir}"

cleanup() {
  if [ -d "${parts_dir}" ]; then
    rm -rf "${parts_dir}"
  fi
}

trap cleanup EXIT

wget_common_args=(
  --no-check-certificate
  --timeout="${wget_timeout}"
  --tries="${wget_tries}"
  --user-agent="${wget_user_agent}"
)
if [ -n "${host_header}" ]; then
  wget_common_args+=(--header="Host: ${host_header}")
fi

curl_common_args=(
  -k
  -L
  --retry "${WGET_TRIES:-3}"
  --user-agent "${wget_user_agent}"
)
if [ -n "${s3_ip}" ]; then
  curl_common_args+=(--resolve "${default_host}:443:${s3_ip}")
fi

content_length() {
  wget "${wget_common_args[@]}" --spider --server-response "${url}" 2>&1 \
    | awk 'BEGIN {IGNORECASE=1} $1 == "Content-Length:" {value=$2} END {gsub("\r", "", value); print value}'
}

download_single() {
  if [ "${downloader}" = "curl" ]; then
    curl "${curl_common_args[@]}" --continue-at - -o "${partial}" "${default_url}"
  else
    wget "${wget_common_args[@]}" --continue -O "${partial}" "${url}"
  fi
}

download_parallel() {
  local total_size="$1"
  local jobs="$2"
  local chunk_size=$(( (total_size + jobs - 1) / jobs ))
  local index=0
  local status=0
  local pids=()

  rm -rf "${parts_dir}"
  mkdir -p "${parts_dir}"
  rm -f "${partial_next}"

  while [ "${index}" -lt "${jobs}" ]; do
    local start=$(( index * chunk_size ))
    if [ "${start}" -ge "${total_size}" ]; then
      break
    fi
    local end=$(( start + chunk_size - 1 ))
    if [ "${end}" -ge "${total_size}" ]; then
      end=$(( total_size - 1 ))
    fi
    local part="${parts_dir}/part-$(printf '%04d' "${index}")"
    echo "downloading AppWorld data bytes ${start}-${end}"
    if [ "${downloader}" = "curl" ]; then
      curl "${curl_common_args[@]}" \
        --silent \
        --show-error \
        --range "${start}-${end}" \
        -o "${part}" \
        "${default_url}" &
    else
      wget "${wget_common_args[@]}" \
        --quiet \
        --header="Range: bytes=${start}-${end}" \
        -O "${part}" \
        "${url}" &
    fi
    pids+=("$!")
    index=$(( index + 1 ))
  done

  for pid in "${pids[@]}"; do
    if ! wait "${pid}"; then
      status=1
    fi
  done
  if [ "${status}" -ne 0 ]; then
    return "${status}"
  fi

  : > "${partial_next}"
  index=0
  while [ "${index}" -lt "${jobs}" ]; do
    local part="${parts_dir}/part-$(printf '%04d' "${index}")"
    if [ -f "${part}" ]; then
      cat "${part}" >> "${partial_next}"
    fi
    index=$(( index + 1 ))
  done

  local actual_size
  actual_size="$(wc -c < "${partial_next}")"
  if [ "${actual_size}" != "${total_size}" ]; then
    echo "assembled bundle has ${actual_size} bytes, expected ${total_size}" >&2
    return 1
  fi
  mv "${partial_next}" "${partial}"
}

if [ ! -f "${bundle}" ]; then
  if [ "${download_mode}" = "parallel" ] && [ "${download_jobs}" -gt 1 ]; then
    total_size="$(content_length || true)"
    if [ -n "${total_size}" ] && [ "${total_size}" -gt 0 ]; then
      echo "downloading AppWorld data from official S3 with ${download_jobs} parallel byte ranges"
      download_parallel "${total_size}" "${download_jobs}"
    else
      echo "could not determine content length; falling back to single wget" >&2
      download_single
    fi
  else
    echo "downloading AppWorld data from official S3 with resumable wget"
    download_single
  fi
  mv "${partial}" "${bundle}"
else
  echo "bundle already exists: ${bundle}"
fi

APPWORLD_ROOT="${appworld_root}" "${python_bin}" -c \
  "from appworld.common.constants import PASSWORD, SALT; from appworld.common.crypto import unpack_bundle; unpack_bundle('${bundle}', '${appworld_root}', PASSWORD, SALT)"

echo "downloaded and unpacked AppWorld data to ${appworld_root}/data"
