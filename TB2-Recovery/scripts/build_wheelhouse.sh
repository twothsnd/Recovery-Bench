#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "${ROOT}/env" 2>/dev/null || true

WHEELHOUSE="${TB2_WHEELHOUSE:-${ROOT}/wheelhouse}"
INDEX="${PIP_INDEX_URL:-https://mirrors.aliyun.com/pypi/simple/}"
TRUSTED="${PIP_TRUSTED_HOST:-mirrors.aliyun.com}"
PYTHON_TAGS="${TB2_WHEELHOUSE_PY_TAGS:-3.11 3.12 3.13}"
PYTHON_IMAGE_PREFIX="${TB2_WHEELHOUSE_PYTHON_IMAGE_PREFIX:-${TB2_DOCKER_MIRROR_PREFIX:-}}"
COMMON="${ROOT}/packages/python_common.txt"
HEAVY="${ROOT}/packages/python_heavy.txt"
LOG_DIR="${ROOT}/logs/wheelhouse"

mkdir -p "$WHEELHOUSE" "$LOG_DIR"

tmp_list="$(mktemp)"
trap 'rm -f "$tmp_list"' EXIT

grep -v '^[[:space:]]*#' "$COMMON" | grep -v '^[[:space:]]*$' >"$tmp_list"
if [[ "${TB2_WHEELHOUSE_HEAVY:-0}" == "1" ]]; then
  grep -v '^[[:space:]]*#' "$HEAVY" | grep -v '^[[:space:]]*$' >>"$tmp_list"
fi
cp -f "$tmp_list" "$WHEELHOUSE/packages.txt"

download_for_python() {
  local py="$1"
  local tag="cp${py//./}"
  local dest="$WHEELHOUSE/$tag"
  local log="$LOG_DIR/download_${tag}.log"
  local image="python:${py}-slim-bookworm"
  if [[ -n "$PYTHON_IMAGE_PREFIX" ]]; then
    image="${PYTHON_IMAGE_PREFIX%/}/python:${py}-slim-bookworm"
  fi
  mkdir -p "$dest"

  echo "=== wheelhouse python=${py} tag=${tag} image=${image} dest=${dest} ===" | tee "$log"
  docker run --rm \
    -v "${WHEELHOUSE}:/wheelhouse:rw" \
    -v "${tmp_list}:/packages.txt:ro" \
    "$image" \
    bash -lc "
set -uo pipefail
export PIP_INDEX_URL='${INDEX}'
export PIP_TRUSTED_HOST='${TRUSTED}'
export PIP_DISABLE_PIP_VERSION_CHECK=1
apt-get update -qq >/dev/null 2>&1 || true
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq gcc g++ python3-dev >/dev/null 2>&1 || true
python3 -m pip install -q --upgrade pip wheel setuptools
while IFS= read -r pkg || [[ -n \"\$pkg\" ]]; do
  [[ -z \"\$pkg\" || \"\$pkg\" =~ ^# ]] && continue
  echo \"[download:${tag}] \$pkg\"
  python3 -m pip download -d /wheelhouse/${tag} \"\$pkg\" --prefer-binary \
    --extra-index-url '${INDEX}' 2>/tmp/pip-download.err || {
      echo \"[warn:${tag}] failed: \$pkg\" >&2
      sed -n '1,80p' /tmp/pip-download.err >&2 || true
    }
done < /packages.txt
echo \"done ${tag}: \$(find /wheelhouse/${tag} -maxdepth 1 -type f -name '*.whl' | wc -l) wheels\"
" | tee -a "$log"
}

for py in $PYTHON_TAGS; do
  download_for_python "$py"
done

mkdir -p "$WHEELHOUSE/universal"
find "$WHEELHOUSE" -maxdepth 2 -mindepth 2 -name '*py3-none-any.whl' -print0 2>/dev/null \
  | xargs -0 -r -I{} cp -n {} "$WHEELHOUSE/universal/" 2>/dev/null || true

echo "Wheelhouse ready: $WHEELHOUSE"
for d in "$WHEELHOUSE"/cp* "$WHEELHOUSE"/universal; do
  [[ -d "$d" ]] || continue
  echo "  $(basename "$d"): $(find "$d" -maxdepth 1 -type f | wc -l) files"
done
