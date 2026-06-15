#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "${ROOT}/env" 2>/dev/null || true

DEBIAN_MIRROR="${DEBIAN_MIRROR:-http://mirrors.aliyun.com/debian}"
UBUNTU_MIRROR="${UBUNTU_MIRROR:-http://mirrors.aliyun.com/ubuntu}"
APT_UPDATE_TIMEOUT="${APT_UPDATE_TIMEOUT:-180}"
APT_INSTALL_TIMEOUT="${APT_INSTALL_TIMEOUT:-600}"
TB2_PREBAKE_ASCIINEMA="${TB2_PREBAKE_ASCIINEMA:-1}"
TB2_PREBAKE_ASCIINEMA_VERSION="${TB2_PREBAKE_ASCIINEMA_VERSION:-2.4.0}"
TB2_PIP_DOWNLOAD_INDEX_URL="${TB2_PIP_DOWNLOAD_INDEX_URL:-${PIP_INDEX_URL:-}}"
DEBIAN_BOOKWORM_LIBEVENT_DEB_URL="${DEBIAN_BOOKWORM_LIBEVENT_DEB_URL:-${DEBIAN_MIRROR%/}/pool/main/libe/libevent/libevent-core-2.1-7_2.1.12-stable-8_amd64.deb}"
DEBIAN_BOOKWORM_LIBUTEMPTER_DEB_URL="${DEBIAN_BOOKWORM_LIBUTEMPTER_DEB_URL:-${DEBIAN_MIRROR%/}/pool/main/libu/libutempter/libutempter0_1.2.1-3_amd64.deb}"
DEBIAN_BOOKWORM_TMUX_DEB_URL="${DEBIAN_BOOKWORM_TMUX_DEB_URL:-${DEBIAN_MIRROR%/}/pool/main/t/tmux/tmux_3.3a-3_amd64.deb}"

if [[ "$#" -lt 1 ]]; then
  echo "Usage: $0 IMAGE [IMAGE...]" >&2
  exit 2
fi

is_ready() {
  local image="$1"
  docker run --rm \
    -e TB2_PREBAKE_ASCIINEMA="$TB2_PREBAKE_ASCIINEMA" \
    "$image" bash -lc '
    set -euo pipefail
    export PATH="/usr/local/bin:$HOME/.local/bin:$PATH"
    command -v tmux >/dev/null
    test -f /opt/tb2_recovery/.prebaked
    if [[ "${TB2_PREBAKE_ASCIINEMA:-1}" == "1" ]]; then
      command -v asciinema >/dev/null
      asciinema --version >/dev/null 2>&1
    fi
  ' >/dev/null 2>&1
}

download_host_deb() {
  local url="$1"
  local output="$2"
  if [[ -s "$output" ]]; then
    return 0
  fi
  mkdir -p "$(dirname "$output")"
  local partial="${output}.tmp"
  rm -f "$partial"
  curl -LfsS --retry 3 --connect-timeout 20 --max-time 180 "$url" -o "$partial"
  mv "$partial" "$output"
}

download_ubuntu_noble_prebake_debs() {
  local mirror="${UBUNTU_MIRROR%/}"
  local output_dir="$1"
  mkdir -p "$output_dir"
  local spec url file
  while read -r spec; do
    [[ -n "$spec" ]] || continue
    url="${mirror}/${spec}"
    file="${output_dir}/$(basename "$spec")"
    download_host_deb "$url" "$file"
  done <<'EOF'
pool/main/libe/libevent/libevent-core-2.1-7t64_2.1.12-stable-9ubuntu2_amd64.deb
pool/main/libu/libutempter/libutempter0_1.2.1-3build1_amd64.deb
pool/main/t/tmux/tmux_3.4-1build1_amd64.deb
pool/main/p/python3.12/libpython3.12-minimal_3.12.3-1ubuntu0.13_amd64.deb
pool/main/e/expat/libexpat1_2.6.1-2ubuntu0.4_amd64.deb
pool/main/p/python3.12/python3.12-minimal_3.12.3-1ubuntu0.13_amd64.deb
pool/main/p/python3-defaults/python3-minimal_3.12.3-0ubuntu2.1_amd64.deb
pool/main/m/media-types/media-types_10.1.0_all.deb
pool/main/n/netbase/netbase_6.4_all.deb
pool/main/t/tzdata/tzdata_2026a-0ubuntu0.24.04.1_all.deb
pool/main/r/readline/readline-common_8.2-4build1_all.deb
pool/main/r/readline/libreadline8t64_8.2-4build1_amd64.deb
pool/main/s/sqlite3/libsqlite3-0_3.45.1-1ubuntu2.5_amd64.deb
pool/main/p/python3.12/libpython3.12-stdlib_3.12.3-1ubuntu0.13_amd64.deb
pool/main/p/python3.12/python3.12_3.12.3-1ubuntu0.13_amd64.deb
pool/main/p/python3-defaults/libpython3-stdlib_3.12.3-0ubuntu2.1_amd64.deb
pool/main/p/python3-defaults/python3_3.12.3-0ubuntu2.1_amd64.deb
pool/main/s/setuptools/python3-pkg-resources_68.1.2-2ubuntu1.2_all.deb
pool/universe/a/asciinema/asciinema_2.4.0-1_all.deb
EOF
}

download_python_wheels() {
  local output_dir="$1"
  mkdir -p "$output_dir"
  if compgen -G "${output_dir}/asciinema-${TB2_PREBAKE_ASCIINEMA_VERSION}-*.whl" >/dev/null; then
    return 0
  fi
  local argv=(python -m pip download --only-binary=:all: --dest "$output_dir" "asciinema==${TB2_PREBAKE_ASCIINEMA_VERSION}")
  if [[ -n "${TB2_PIP_DOWNLOAD_INDEX_URL}" ]]; then
    argv+=(--index-url "$TB2_PIP_DOWNLOAD_INDEX_URL")
  fi
  "${argv[@]}"
}

install_one() {
  local image="$1"
  if is_ready "$image"; then
    echo "SKIP prebaked: $image"
    return 0
  fi

  local tmp="tb2_recovery_prebake_${image//[^a-zA-Z0-9]/_}_$$"
  docker rm -f "$tmp" >/dev/null 2>&1 || true
  docker create --name "$tmp" "$image" sleep infinity >/dev/null
  docker start "$tmp" >/dev/null
  cleanup_tmp() {
    docker rm -f "$tmp" >/dev/null 2>&1 || true
  }
  trap cleanup_tmp RETURN
  trap cleanup_tmp EXIT

  local host_deb_dir="${ROOT}/cache/debs/debian-bookworm"
  local host_libevent_deb="${host_deb_dir}/libevent-core-2.1-7_2.1.12-stable-8_amd64.deb"
  local host_libutempter_deb="${host_deb_dir}/libutempter0_1.2.1-3_amd64.deb"
  local host_tmux_deb="${host_deb_dir}/tmux_3.3a-3_amd64.deb"
  if download_host_deb "$DEBIAN_BOOKWORM_LIBEVENT_DEB_URL" "$host_libevent_deb" \
    && download_host_deb "$DEBIAN_BOOKWORM_LIBUTEMPTER_DEB_URL" "$host_libutempter_deb" \
    && download_host_deb "$DEBIAN_BOOKWORM_TMUX_DEB_URL" "$host_tmux_deb"; then
    docker cp "$host_libevent_deb" "${tmp}:/tmp/tb2_libevent_core.deb"
    docker cp "$host_libutempter_deb" "${tmp}:/tmp/tb2_libutempter0.deb"
    docker cp "$host_tmux_deb" "${tmp}:/tmp/tb2_tmux.deb"
  fi
  local host_ubuntu_noble_deb_dir="${ROOT}/cache/debs/ubuntu-noble"
  if download_ubuntu_noble_prebake_debs "$host_ubuntu_noble_deb_dir"; then
    docker exec -u root "$tmp" mkdir -p /tmp/tb2_ubuntu_noble_debs
    for deb in "$host_ubuntu_noble_deb_dir"/*.deb; do
      docker cp "$deb" "${tmp}:/tmp/tb2_ubuntu_noble_debs/$(basename "$deb")"
    done
  fi
  local host_python_wheel_dir="${ROOT}/cache/python-wheels"
  if [[ "${TB2_PREBAKE_ASCIINEMA}" == "1" ]] && download_python_wheels "$host_python_wheel_dir"; then
    docker exec -u root "$tmp" mkdir -p /tmp/tb2_python_wheels
    for wheel in "$host_python_wheel_dir"/*.whl; do
      docker cp "$wheel" "${tmp}:/tmp/tb2_python_wheels/$(basename "$wheel")"
    done
  fi

  docker exec -u root \
    -e DEBIAN_MIRROR="$DEBIAN_MIRROR" \
    -e UBUNTU_MIRROR="$UBUNTU_MIRROR" \
    -e APT_UPDATE_TIMEOUT="$APT_UPDATE_TIMEOUT" \
    -e APT_INSTALL_TIMEOUT="$APT_INSTALL_TIMEOUT" \
    -e TB2_PREBAKE_ASCIINEMA="$TB2_PREBAKE_ASCIINEMA" \
    -e TB2_PREBAKE_ASCIINEMA_VERSION="$TB2_PREBAKE_ASCIINEMA_VERSION" \
    "$tmp" bash -lc '
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

apt_update() {
  timeout "${APT_UPDATE_TIMEOUT:-180}" apt-get \
    -o Acquire::Retries=2 \
    -o Acquire::http::Timeout=30 \
    -o Acquire::https::Timeout=30 \
    -o Acquire::ForceIPv4=true \
    update -qq
}

apt_install() {
  timeout "${APT_INSTALL_TIMEOUT:-600}" apt-get \
    -o Acquire::Retries=2 \
    -o Acquire::http::Timeout=30 \
    -o Acquire::https::Timeout=30 \
    -o Acquire::ForceIPv4=true \
    install -y -qq --no-install-recommends "$@" >/dev/null
}

install_tmux_deb_fallback() {
  if [[ ! -f /etc/os-release ]]; then
    return 1
  fi
  . /etc/os-release
  local tmp_debs mirror
  tmp_debs="$(mktemp -d)"
  if [[ "${ID:-}" == "ubuntu" && "${VERSION_CODENAME:-}" == "noble" ]]; then
    if compgen -G "/tmp/tb2_ubuntu_noble_debs/*.deb" >/dev/null; then
      cp /tmp/tb2_ubuntu_noble_debs/*.deb "$tmp_debs"/
    else
      rm -rf "${tmp_debs}"
      return 1
    fi
    dpkg -i \
      "${tmp_debs}/libevent-core-2.1-7t64_2.1.12-stable-9ubuntu2_amd64.deb" \
      "${tmp_debs}/libutempter0_1.2.1-3build1_amd64.deb" \
      "${tmp_debs}/tmux_3.4-1build1_amd64.deb" >/dev/null
    rm -rf "${tmp_debs}"
    return 0
  fi
  if [[ "${ID:-}" != "debian" || "${VERSION_CODENAME:-}" != "bookworm" ]]; then
    rm -rf "${tmp_debs}"
    return 1
  fi
  if [[ -s /tmp/tb2_libevent_core.deb && -s /tmp/tb2_libutempter0.deb && -s /tmp/tb2_tmux.deb ]]; then
    cp /tmp/tb2_libevent_core.deb "${tmp_debs}/libevent-core.deb"
    cp /tmp/tb2_libutempter0.deb "${tmp_debs}/libutempter0.deb"
    cp /tmp/tb2_tmux.deb "${tmp_debs}/tmux.deb"
  else
    mirror="${DEBIAN_MIRROR%/}"
    real_curl=""
    for candidate in /usr/local/bin/curl.real /usr/bin/curl.real /bin/curl.real; do
      if [[ -x "$candidate" ]]; then
        real_curl="$candidate"
        break
      fi
    done
    [[ -n "$real_curl" ]] || return 1
    "$real_curl" -LfsS --retry 3 --connect-timeout 20 --max-time 120 \
      "${mirror}/pool/main/libe/libevent/libevent-core-2.1-7_2.1.12-stable-8_amd64.deb" \
      -o "${tmp_debs}/libevent-core.deb"
    "$real_curl" -LfsS --retry 3 --connect-timeout 20 --max-time 120 \
      "${mirror}/pool/main/libu/libutempter/libutempter0_1.2.1-3_amd64.deb" \
      -o "${tmp_debs}/libutempter0.deb"
    "$real_curl" -LfsS --retry 3 --connect-timeout 20 --max-time 120 \
      "${mirror}/pool/main/t/tmux/tmux_3.3a-3_amd64.deb" \
      -o "${tmp_debs}/tmux.deb"
  fi
  dpkg -i "${tmp_debs}/libevent-core.deb" "${tmp_debs}/libutempter0.deb" "${tmp_debs}/tmux.deb" >/dev/null
  rm -rf "${tmp_debs}"
}

install_asciinema_deb_fallback() {
  if [[ ! -f /etc/os-release ]]; then
    return 1
  fi
  . /etc/os-release
  if [[ "${ID:-}" != "ubuntu" || "${VERSION_CODENAME:-}" != "noble" ]]; then
    return 1
  fi
  local tmp_debs
  tmp_debs="$(mktemp -d)"
  if compgen -G "/tmp/tb2_ubuntu_noble_debs/*.deb" >/dev/null; then
    cp /tmp/tb2_ubuntu_noble_debs/*.deb "$tmp_debs"/
  else
    rm -rf "${tmp_debs}"
    return 1
  fi
  dpkg -i \
    "${tmp_debs}/libpython3.12-minimal_3.12.3-1ubuntu0.13_amd64.deb" \
    "${tmp_debs}/libexpat1_2.6.1-2ubuntu0.4_amd64.deb" \
    "${tmp_debs}/python3.12-minimal_3.12.3-1ubuntu0.13_amd64.deb" >/dev/null
  dpkg -i \
    "${tmp_debs}/python3-minimal_3.12.3-0ubuntu2.1_amd64.deb" >/dev/null
  dpkg -i \
    "${tmp_debs}/media-types_10.1.0_all.deb" \
    "${tmp_debs}/netbase_6.4_all.deb" \
    "${tmp_debs}/tzdata_2026a-0ubuntu0.24.04.1_all.deb" \
    "${tmp_debs}/readline-common_8.2-4build1_all.deb" \
    "${tmp_debs}/libreadline8t64_8.2-4build1_amd64.deb" \
    "${tmp_debs}/libsqlite3-0_3.45.1-1ubuntu2.5_amd64.deb" >/dev/null
  dpkg -i \
    "${tmp_debs}/libpython3.12-stdlib_3.12.3-1ubuntu0.13_amd64.deb" \
    "${tmp_debs}/python3.12_3.12.3-1ubuntu0.13_amd64.deb" >/dev/null
  dpkg -i \
    "${tmp_debs}/libpython3-stdlib_3.12.3-0ubuntu2.1_amd64.deb" \
    "${tmp_debs}/python3_3.12.3-0ubuntu2.1_amd64.deb" >/dev/null
  dpkg -i \
    "${tmp_debs}/python3-pkg-resources_68.1.2-2ubuntu1.2_all.deb" \
    "${tmp_debs}/asciinema_2.4.0-1_all.deb" >/dev/null
  rm -rf "${tmp_debs}"
}

install_asciinema_pip_fallback() {
  if ! command -v python3 >/dev/null 2>&1; then
    return 1
  fi
  if ! python3 -m pip --version >/dev/null 2>&1; then
    return 1
  fi
  if ! compgen -G "/tmp/tb2_python_wheels/*.whl" >/dev/null; then
    return 1
  fi
  python3 -m pip install \
    --no-index \
    --find-links /tmp/tb2_python_wheels \
    --no-cache-dir \
    --disable-pip-version-check \
    "asciinema==${TB2_PREBAKE_ASCIINEMA_VERSION:-2.4.0}" >/dev/null
}

if ! command -v tmux >/dev/null 2>&1; then
if command -v apt-get >/dev/null 2>&1; then
  if install_tmux_deb_fallback; then
    :
  else
    apt_update
    apt_install tmux
  fi
elif command -v apk >/dev/null 2>&1; then
  apk add --no-cache tmux >/dev/null
elif command -v dnf >/dev/null 2>&1; then
  dnf install -y tmux >/dev/null
elif command -v yum >/dev/null 2>&1; then
  yum install -y tmux >/dev/null
fi
fi

if [[ "${TB2_PREBAKE_ASCIINEMA}" == "1" ]] && ! command -v asciinema >/dev/null 2>&1; then
if install_asciinema_pip_fallback; then
  :
elif command -v apt-get >/dev/null 2>&1; then
  if install_asciinema_deb_fallback; then
    :
  else
    apt_update
    apt_install asciinema
  fi
elif command -v apk >/dev/null 2>&1; then
  apk add --no-cache asciinema >/dev/null
elif command -v dnf >/dev/null 2>&1; then
  dnf install -y asciinema >/dev/null
elif command -v yum >/dev/null 2>&1; then
  yum install -y asciinema >/dev/null
fi
fi

mkdir -p /opt/tb2_recovery
rm -rf \
  /tmp/tb2_ubuntu_noble_debs \
  /tmp/tb2_python_wheels \
  /tmp/tb2_libevent_core.deb \
  /tmp/tb2_libutempter0.deb \
  /tmp/tb2_tmux.deb

'

  docker exec -u root \
    -e TB2_PREBAKE_ASCIINEMA="$TB2_PREBAKE_ASCIINEMA" \
    "$tmp" bash -lc '
set -euo pipefail
touch /opt/tb2_recovery/.prebaked
export PATH="/usr/local/bin:$HOME/.local/bin:$PATH"
if [[ "${TB2_PREBAKE_ASCIINEMA:-1}" == "1" ]]; then
  command -v asciinema >/dev/null
  asciinema --version >/dev/null
fi
tmux -V >/dev/null
'

  docker commit "$tmp" "$image" >/dev/null
  docker rm -f "$tmp" >/dev/null
  trap - RETURN
  trap - EXIT
  echo "OK prebaked: $image"
}

for image in "$@"; do
  install_one "$image"
done
