#!/usr/bin/env bash
set -euo pipefail

appworld_src="${APPWORLD_SRC:-external/appworld/src}"
appworld_runtime="${APPWORLD_RUNTIME_SRC:-external/appworld/runtime}"
ref="${APPWORLD_REF:-main}"
base_url="${APPWORLD_BUNDLE_BASE_URL:-https://media.githubusercontent.com/media/StonyBrookNLP/appworld/${ref}}"
wget_timeout="${WGET_TIMEOUT:-30}"
wget_tries="${WGET_TRIES:-3}"
wget_user_agent="${WGET_USER_AGENT:-Mozilla/5.0}"
python_bin="${PYTHON:-${PWD}/.venv/bin/python}"

download_bundle() {
  local relative_path="$1"
  local target="${appworld_runtime}/${relative_path}"
  local pointer="${target}.pointer"
  local partial="${target}.part"
  local expected_sha expected_size actual_sha actual_size

  if [ ! -f "${target}" ]; then
    echo "missing AppWorld bundle pointer: ${target}" >&2
    exit 2
  fi

  if ! grep -q "https://git-lfs.github.com/spec/v1" "${target}"; then
    echo "bundle already materialized: ${target}"
    return
  fi

  expected_sha="$(awk -F: '/^oid sha256:/ {print $2}' "${target}")"
  expected_size="$(awk '/^size / {print $2}' "${target}")"
  if [ -z "${expected_sha}" ] || [ -z "${expected_size}" ]; then
    echo "invalid Git LFS pointer: ${target}" >&2
    exit 3
  fi

  cp "${target}" "${pointer}"
  echo "downloading AppWorld source bundle ${relative_path}"
  wget \
    --no-check-certificate \
    --timeout="${wget_timeout}" \
    --tries="${wget_tries}" \
    --user-agent="${wget_user_agent}" \
    -O "${partial}" \
    "${base_url}/${relative_path}"

  actual_sha="$(sha256sum "${partial}" | awk '{print $1}')"
  actual_size="$(wc -c < "${partial}")"
  if [ "${actual_sha}" != "${expected_sha}" ]; then
    echo "sha256 mismatch for ${relative_path}: got ${actual_sha}, expected ${expected_sha}" >&2
    exit 4
  fi
  if [ "${actual_size}" != "${expected_size}" ]; then
    echo "size mismatch for ${relative_path}: got ${actual_size}, expected ${expected_size}" >&2
    exit 5
  fi
  mv "${partial}" "${target}"
}

if [ ! -d "${appworld_src}" ]; then
  echo "missing AppWorld official source checkout: ${appworld_src}" >&2
  exit 2
fi

rm -rf "${appworld_runtime}"
mkdir -p "$(dirname "${appworld_runtime}")"
cp -a "${appworld_src}" "${appworld_runtime}"

download_bundle "src/appworld/.source/apps.bundle"
download_bundle "src/appworld/.source/tests.bundle"
download_bundle "generate/.source/tasks.bundle"
download_bundle "generate/.source/data.bundle"

(
  cd "${appworld_runtime}"
  PYTHONPATH="${PWD}/src:${PYTHONPATH:-}" "${python_bin}" -m appworld.cli install --repo
)

echo "downloaded and unpacked official AppWorld source bundles to ${appworld_runtime}"
