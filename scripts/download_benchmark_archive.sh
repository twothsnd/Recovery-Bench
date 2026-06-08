#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 2 ] || [ "$#" -gt 3 ]; then
  echo "usage: $0 NAME URL [ARCHIVE_NAME]" >&2
  exit 2
fi

name="$1"
url="$2"
archive_name="${3:-${url##*/}}"
root="external/${name}"
archive_dir="${root}/archives"
src_dir="${root}/src"
archive="${archive_dir}/${archive_name}"
partial="${archive}.part"
wget_timeout="${WGET_TIMEOUT:-30}"
wget_tries="${WGET_TRIES:-3}"
wget_user_agent="${WGET_USER_AGENT:-Mozilla/5.0}"
extract_tmp=""

mkdir -p "${archive_dir}" "${root}"

cleanup() {
  rm -f "${partial}"
  if [ -n "${extract_tmp}" ] && [ -d "${extract_tmp}" ]; then
    rm -rf "${extract_tmp}"
  fi
}

trap cleanup EXIT

archive_format() {
  case "${archive_name}" in
    *.tar.gz|*.tgz)
      echo "tar.gz"
      ;;
    *.tar)
      echo "tar"
      ;;
    *.zip)
      echo "zip"
      ;;
    *)
      return 1
      ;;
  esac
}

is_valid_archive() {
  local path="$1"
  case "$(archive_format)" in
    tar.gz)
      tar -tzf "${path}" >/dev/null 2>&1
      ;;
    tar)
      tar -tf "${path}" >/dev/null 2>&1
      ;;
    zip)
      python3 -m zipfile -t "${path}" >/dev/null 2>&1
      ;;
    *)
      return 1
      ;;
  esac
}

github_archive_to_codeload_url() {
  local input_url="$1"
  if [[ "${input_url}" =~ ^https://github\.com/([^/]+)/([^/]+)/archive/(.+)\.(tar\.gz|tgz|zip)$ ]]; then
    local owner="${BASH_REMATCH[1]}"
    local repo="${BASH_REMATCH[2]}"
    local ref_path="${BASH_REMATCH[3]}"
    local ext="${BASH_REMATCH[4]}"
    local format
    case "${ext}" in
      tar.gz|tgz)
        format="tar.gz"
        ;;
      zip)
        format="zip"
        ;;
      *)
        return 1
        ;;
    esac
    echo "https://codeload.github.com/${owner}/${repo}/${format}/${ref_path}"
    return 0
  fi
  return 1
}

download_with_wget() {
  local input_url="$1"
  local host_header="${2:-}"
  local args=(
    --no-check-certificate
    --timeout="${wget_timeout}"
    --tries="${wget_tries}"
    --user-agent="${wget_user_agent}"
    -O "${partial}"
  )
  if [ -n "${host_header}" ]; then
    args+=(--header="Host: ${host_header}")
  fi
  wget "${args[@]}" "${input_url}"
}

try_download() {
  local input_url="$1"
  local label="${2:-${input_url}}"
  local host_header="${3:-}"

  rm -f "${partial}"
  echo "downloading ${name} from ${label}"
  if download_with_wget "${input_url}" "${host_header}" && is_valid_archive "${partial}"; then
    return 0
  fi

  rm -f "${partial}"
  echo "download failed or archive validation failed: ${label}" >&2
  return 1
}

try_github_codeload_fallbacks() {
  local codeload_url
  if ! codeload_url="$(github_archive_to_codeload_url "${url}")"; then
    return 1
  fi

  if try_download "${codeload_url}" "${codeload_url}"; then
    return 0
  fi

  local codeload_path="${codeload_url#https://codeload.github.com}"
  local default_ips="140.82.112.10 140.82.113.10 140.82.114.10 140.82.121.10"
  local codeload_ips="${GITHUB_CODELOAD_IPS:-${default_ips}}"
  local ip
  for ip in ${codeload_ips}; do
    if try_download "https://${ip}${codeload_path}" "https://codeload.github.com${codeload_path} via ${ip}" "codeload.github.com"; then
      return 0
    fi
  done

  return 1
}

if ! archive_format >/dev/null; then
  echo "unsupported archive type: ${archive_name}" >&2
  exit 3
fi

if [ -f "${archive}" ]; then
  if is_valid_archive "${archive}"; then
    echo "archive already exists: ${archive}"
  else
    echo "archive is invalid, redownloading: ${archive}"
    rm -f "${archive}"
  fi
fi

if [ ! -f "${archive}" ]; then
  rm -f "${partial}"
  if ! try_download "${url}" "${url}" && ! try_github_codeload_fallbacks; then
    echo "could not download a supported valid archive from official source: ${url}" >&2
    exit 4
  fi
  mv "${partial}" "${archive}"
fi

extract_tmp="$(mktemp -d "${root}/src.tmp.XXXXXX")"
case "$(archive_format)" in
  tar.gz)
    tar -xzf "${archive}" -C "${extract_tmp}" --strip-components=1
    ;;
  tar)
    tar -xf "${archive}" -C "${extract_tmp}" --strip-components=1
    ;;
  zip)
    python3 -m zipfile -e "${archive}" "${extract_tmp}"
    ;;
  *)
    echo "unsupported archive type: ${archive_name}" >&2
    exit 3
    ;;
esac
rm -rf "${src_dir}"
mv "${extract_tmp}" "${src_dir}"
extract_tmp=""

echo "downloaded and extracted ${name} to ${src_dir}"
