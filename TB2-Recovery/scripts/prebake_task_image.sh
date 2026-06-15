#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "${ROOT}/env" 2>/dev/null || true

PIP_INDEX_URL="${PIP_INDEX_URL:-https://mirrors.aliyun.com/pypi/simple/}"
PIP_TRUSTED_HOST="${PIP_TRUSTED_HOST:-mirrors.aliyun.com}"
UV_INDEX_URL="${UV_INDEX_URL:-$PIP_INDEX_URL}"
UV_PYTHON_INSTALL_MIRROR="${UV_PYTHON_INSTALL_MIRROR:-https://mirrors.ustc.edu.cn/github-release/astral-sh/python-build-standalone/}"
DEBIAN_MIRROR="${DEBIAN_MIRROR:-http://mirrors.aliyun.com/debian}"
UBUNTU_MIRROR="${UBUNTU_MIRROR:-http://mirrors.aliyun.com/ubuntu}"
MINI_SWE_AGENT_SPEC="${MINI_SWE_AGENT_SPEC:-mini-swe-agent}"
GH_PROXY="${GH_PROXY:-https://gh-proxy.com}"
UV_VERSION="${UV_VERSION:-0.7.13}"
APT_UPDATE_TIMEOUT="${APT_UPDATE_TIMEOUT:-180}"
APT_INSTALL_TIMEOUT="${APT_INSTALL_TIMEOUT:-600}"
TB2_PREBAKE_REWRITE_APT_SOURCES="${TB2_PREBAKE_REWRITE_APT_SOURCES:-0}"
TB2_PREBAKE_APT_MIRROR_FALLBACK="${TB2_PREBAKE_APT_MIRROR_FALLBACK:-1}"
HOST_UV_BIN="${HOST_UV_BIN:-$(command -v uv || true)}"
HOST_UVX_BIN="${HOST_UVX_BIN:-$(command -v uvx || true)}"
TB2_PREBAKE_SYSTEM_DEPS="${TB2_PREBAKE_SYSTEM_DEPS:-0}"
TB2_PREBAKE_MINI_SWE_AGENT="${TB2_PREBAKE_MINI_SWE_AGENT:-1}"
TB2_PREBAKE_ASCIINEMA="${TB2_PREBAKE_ASCIINEMA:-1}"
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
    -e TB2_PREBAKE_MINI_SWE_AGENT="$TB2_PREBAKE_MINI_SWE_AGENT" \
    -e TB2_PREBAKE_ASCIINEMA="$TB2_PREBAKE_ASCIINEMA" \
    "$image" bash -lc '
    set -euo pipefail
    export PATH="/usr/local/bin:$HOME/.local/bin:$PATH"
    command -v uv >/dev/null
    command -v uvx >/dev/null
    command -v pip >/dev/null
    command -v tmux >/dev/null
    test -x /usr/local/bin/uv.real
    test -x /usr/local/bin/curl
    test -L /usr/bin/curl
    test -f /opt/tb2_recovery/.prebaked
    if [[ "${TB2_PREBAKE_MINI_SWE_AGENT:-1}" == "1" ]]; then
      command -v mini-swe-agent >/dev/null
      mini-swe-agent --help >/dev/null 2>&1
    fi
    if [[ "${TB2_PREBAKE_ASCIINEMA:-1}" == "1" ]]; then
      test -x /usr/local/bin/asciinema
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
  trap 'docker rm -f "$tmp" >/dev/null 2>&1 || true' RETURN

  if [[ -x "$HOST_UV_BIN" ]]; then
    docker cp "$HOST_UV_BIN" "${tmp}:/tmp/tb2_host_uv"
  fi
  if [[ -x "$HOST_UVX_BIN" ]]; then
    docker cp "$HOST_UVX_BIN" "${tmp}:/tmp/tb2_host_uvx"
  fi

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

  docker exec -u root \
    -e PIP_INDEX_URL="$PIP_INDEX_URL" \
    -e PIP_TRUSTED_HOST="$PIP_TRUSTED_HOST" \
    -e UV_INDEX_URL="$UV_INDEX_URL" \
    -e UV_PYTHON_INSTALL_MIRROR="$UV_PYTHON_INSTALL_MIRROR" \
    -e DEBIAN_MIRROR="$DEBIAN_MIRROR" \
    -e UBUNTU_MIRROR="$UBUNTU_MIRROR" \
    -e MINI_SWE_AGENT_SPEC="$MINI_SWE_AGENT_SPEC" \
    -e GH_PROXY="$GH_PROXY" \
    -e UV_VERSION="$UV_VERSION" \
    -e APT_UPDATE_TIMEOUT="$APT_UPDATE_TIMEOUT" \
    -e APT_INSTALL_TIMEOUT="$APT_INSTALL_TIMEOUT" \
    -e TB2_PREBAKE_REWRITE_APT_SOURCES="$TB2_PREBAKE_REWRITE_APT_SOURCES" \
    -e TB2_PREBAKE_APT_MIRROR_FALLBACK="$TB2_PREBAKE_APT_MIRROR_FALLBACK" \
    -e TB2_PREBAKE_SYSTEM_DEPS="$TB2_PREBAKE_SYSTEM_DEPS" \
    -e TB2_PREBAKE_MINI_SWE_AGENT="$TB2_PREBAKE_MINI_SWE_AGENT" \
    -e TB2_PREBAKE_ASCIINEMA="$TB2_PREBAKE_ASCIINEMA" \
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

configure_apt_sources() {
  if [[ "${TB2_PREBAKE_REWRITE_APT_SOURCES:-0}" != "1" ]]; then
    return 0
  fi
  configure_mirror_apt_sources
}

disable_existing_apt_sources() {
  mkdir -p /etc/apt/sources.list.d/tb2-recovery-disabled
  if [[ -f /etc/apt/sources.list ]]; then
    mv /etc/apt/sources.list /etc/apt/sources.list.d/tb2-recovery-disabled/sources.list.bak
  fi
  shopt -s nullglob
  for source_file in /etc/apt/sources.list.d/*.list /etc/apt/sources.list.d/*.sources; do
    case "$source_file" in
      */tb2-recovery-*.list|*/tb2-recovery-*.sources) continue ;;
      */tb2-recovery-disabled/*) continue ;;
    esac
    mv "$source_file" "/etc/apt/sources.list.d/tb2-recovery-disabled/$(basename "$source_file").bak"
  done
  shopt -u nullglob
}

configure_mirror_apt_sources() {
  if [[ ! -f /etc/os-release ]]; then
    return 0
  fi
  . /etc/os-release
  codename="${VERSION_CODENAME:-bookworm}"
  disable_existing_apt_sources
  if [[ "${ID:-}" == "ubuntu" ]]; then
    signed_by=""
    if [[ -f /usr/share/keyrings/ubuntu-archive-keyring.gpg ]]; then
      signed_by="Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg"
    fi
    cat >/etc/apt/sources.list.d/tb2-recovery-ubuntu.sources <<EOF
Types: deb
URIs: ${UBUNTU_MIRROR%/}/
Suites: ${codename} ${codename}-updates ${codename}-backports
Components: main universe restricted multiverse
${signed_by}

Types: deb
URIs: ${UBUNTU_MIRROR%/}/
Suites: ${codename}-security
Components: main universe restricted multiverse
${signed_by}
EOF
  else
    cat >/etc/apt/sources.list <<EOF
deb ${DEBIAN_MIRROR%/} ${codename} main contrib non-free
deb ${DEBIAN_MIRROR%/} ${codename}-updates main contrib non-free
EOF
  fi
}

apt_update_with_fallback() {
  if apt_update; then
    return 0
  fi
  if [[ "${TB2_PREBAKE_APT_MIRROR_FALLBACK:-1}" != "1" ]]; then
    return 1
  fi
  echo "apt update failed with existing sources; retrying with configured mirror sources" >&2
  configure_mirror_apt_sources
  apt_update
}

install_tmux_deb_fallback() {
  if [[ ! -f /etc/os-release ]]; then
    return 1
  fi
  . /etc/os-release
  if [[ "${ID:-}" != "debian" || "${VERSION_CODENAME:-}" != "bookworm" ]]; then
    return 1
  fi
  local tmp_debs mirror
  tmp_debs="$(mktemp -d)"
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

need_system_deps=0
[[ "${TB2_PREBAKE_SYSTEM_DEPS}" == "1" ]] && need_system_deps=1
command -v python3 >/dev/null 2>&1 || need_system_deps=1
command -v pip >/dev/null 2>&1 || command -v pip3 >/dev/null 2>&1 || need_system_deps=1
command -v curl >/dev/null 2>&1 || need_system_deps=1
command -v wget >/dev/null 2>&1 || need_system_deps=1
command -v git >/dev/null 2>&1 || need_system_deps=1
if [[ "$need_system_deps" == "1" ]]; then
if command -v apt-get >/dev/null 2>&1; then
  configure_apt_sources
  apt_update_with_fallback
  apt_install bash ca-certificates curl wget git build-essential python3 python3-venv python3-pip tar gzip tmux
elif command -v apk >/dev/null 2>&1; then
  apk add --no-cache bash ca-certificates curl wget git build-base python3 py3-pip tmux >/dev/null
elif command -v dnf >/dev/null 2>&1; then
  dnf install -y bash ca-certificates curl wget git gcc gcc-c++ make python3 python3-pip tmux >/dev/null
elif command -v yum >/dev/null 2>&1; then
  yum install -y bash ca-certificates curl wget git gcc gcc-c++ make python3 python3-pip tmux >/dev/null
fi
fi

if ! command -v tmux >/dev/null 2>&1; then
if command -v apt-get >/dev/null 2>&1; then
  configure_apt_sources
  apt_update_with_fallback
  apt_install tmux || install_tmux_deb_fallback
elif command -v apk >/dev/null 2>&1; then
  apk add --no-cache tmux >/dev/null
elif command -v dnf >/dev/null 2>&1; then
  dnf install -y tmux >/dev/null
elif command -v yum >/dev/null 2>&1; then
  yum install -y tmux >/dev/null
fi
fi

mkdir -p /usr/local/bin /opt/tb2_recovery/shims /root/.local/bin

install_uv_binary() {
  if [[ -x /usr/local/bin/uv.real ]]; then
    ln -sf /usr/local/bin/uv.real /usr/local/bin/uv
    [[ -x /usr/local/bin/uvx.real ]] || ln -sf /usr/local/bin/uv.real /usr/local/bin/uvx.real
    ln -sf /usr/local/bin/uvx.real /usr/local/bin/uvx
    return 0
  fi

  local existing_uv
  existing_uv="$(command -v uv || true)"
  if [[ -n "$existing_uv" && "$existing_uv" != "/usr/local/bin/uv.real" ]]; then
    cp -f "$existing_uv" /usr/local/bin/uv.real
    chmod +x /usr/local/bin/uv.real
  elif [[ -x /tmp/tb2_host_uv ]]; then
    install -m 755 /tmp/tb2_host_uv /usr/local/bin/uv.real
    if [[ -x /tmp/tb2_host_uvx ]]; then
      install -m 755 /tmp/tb2_host_uvx /usr/local/bin/uvx.real
    fi
  else
    local tmp_uv url
    tmp_uv="$(mktemp -d)"
    for url in \
      "${GH_PROXY}/https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-x86_64-unknown-linux-gnu.tar.gz" \
      "https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-x86_64-unknown-linux-gnu.tar.gz"
    do
      if curl -LfsS --retry 5 --connect-timeout 20 --max-time 180 "$url" -o "$tmp_uv/uv.tar.gz"; then
        tar xzf "$tmp_uv/uv.tar.gz" -C "$tmp_uv"
        install -m 755 "$tmp_uv"/uv-x86_64-unknown-linux-gnu/uv /usr/local/bin/uv.real
        if [[ -x "$tmp_uv"/uv-x86_64-unknown-linux-gnu/uvx ]]; then
          install -m 755 "$tmp_uv"/uv-x86_64-unknown-linux-gnu/uvx /usr/local/bin/uvx.real
        fi
        rm -rf "$tmp_uv"
        break
      fi
    done
    [[ -x /usr/local/bin/uv.real ]] || { echo "failed to install uv" >&2; return 1; }
  fi

  [[ -x /usr/local/bin/uvx.real ]] || ln -sf /usr/local/bin/uv.real /usr/local/bin/uvx.real
  ln -sf /usr/local/bin/uv.real /usr/local/bin/uv
  ln -sf /usr/local/bin/uvx.real /usr/local/bin/uvx
}

install_uv_binary
export PATH="/usr/local/bin:/root/.local/bin:$PATH"
export UV_INDEX_URL="${UV_INDEX_URL}"
export PIP_INDEX_URL="${PIP_INDEX_URL}"
export UV_PYTHON_INSTALL_MIRROR="${UV_PYTHON_INSTALL_MIRROR}"

if [[ "${TB2_PREBAKE_MINI_SWE_AGENT}" == "1" ]] && ! command -v mini-swe-agent >/dev/null 2>&1; then
  python_bin="$(command -v python3)"
  uv tool install --python "$python_bin" "${MINI_SWE_AGENT_SPEC}"
fi

if [[ "${TB2_PREBAKE_ASCIINEMA}" == "1" ]] && ! command -v asciinema >/dev/null 2>&1; then
  python_bin="$(command -v python3)"
  uv tool install --python "$python_bin" asciinema
fi

real_asciinema="$(command -v asciinema || true)"
if [[ -n "$real_asciinema" && "$real_asciinema" != "/usr/local/bin/asciinema" ]]; then
  ln -sf "$real_asciinema" /usr/local/bin/asciinema
fi

real_mswe="$(command -v mini-swe-agent || true)"
if [[ -n "$real_mswe" && "$real_mswe" != "/usr/local/bin/mini-swe-agent" ]]; then
  ln -sf "$real_mswe" /usr/local/bin/mini-swe-agent
fi

if [[ -x /usr/local/bin/pip && ! -e /usr/local/bin/pip.real ]]; then
  cp -f /usr/local/bin/pip /usr/local/bin/pip.real || true
fi
if [[ -x /usr/local/bin/pip3 && ! -e /usr/local/bin/pip3.real ]]; then
  cp -f /usr/local/bin/pip3 /usr/local/bin/pip3.real || true
fi
if [[ -x /usr/local/bin/curl && ! -e /usr/local/bin/curl.real ]]; then
  cp -f /usr/local/bin/curl /usr/local/bin/curl.real || true
fi
if [[ -x /usr/bin/curl && ! -e /usr/bin/curl.real ]]; then
  cp -f /usr/bin/curl /usr/bin/curl.real || true
fi
if [[ -x /usr/local/bin/wget && ! -e /usr/local/bin/wget.real ]]; then
  cp -f /usr/local/bin/wget /usr/local/bin/wget.real || true
fi
if [[ -x /usr/bin/wget && ! -e /usr/bin/wget.real ]]; then
  cp -f /usr/bin/wget /usr/bin/wget.real || true
fi

cat >/etc/pip.conf <<EOF
[global]
index-url = ${PIP_INDEX_URL}
trusted-host = ${PIP_TRUSTED_HOST}
disable-pip-version-check = true
progress-bar = off
EOF

cat >/etc/profile.d/tb2_recovery.sh <<EOF
export PATH="/usr/local/bin:\$HOME/.local/bin:\$PATH"
export PIP_INDEX_URL="${PIP_INDEX_URL}"
export PIP_TRUSTED_HOST="${PIP_TRUSTED_HOST}"
export UV_INDEX_URL="${UV_INDEX_URL}"
export UV_PYTHON_INSTALL_MIRROR="${UV_PYTHON_INSTALL_MIRROR}"
EOF
'

  docker cp "${ROOT}/package_shims/pip" "${tmp}:/opt/tb2_recovery/shims/pip"
  docker cp "${ROOT}/package_shims/uv" "${tmp}:/opt/tb2_recovery/shims/uv"
  docker cp "${ROOT}/package_shims/curl" "${tmp}:/opt/tb2_recovery/shims/curl"
  docker cp "${ROOT}/package_shims/wget" "${tmp}:/opt/tb2_recovery/shims/wget"

  docker exec -u root \
    -e TB2_PREBAKE_MINI_SWE_AGENT="$TB2_PREBAKE_MINI_SWE_AGENT" \
    -e TB2_PREBAKE_ASCIINEMA="$TB2_PREBAKE_ASCIINEMA" \
    "$tmp" bash -lc '
set -euo pipefail
chmod +x /opt/tb2_recovery/shims/*
ln -sf /opt/tb2_recovery/shims/pip /usr/local/bin/pip
ln -sf /opt/tb2_recovery/shims/pip /usr/local/bin/pip3
ln -sf /opt/tb2_recovery/shims/uv /usr/local/bin/uv
ln -sf /opt/tb2_recovery/shims/curl /usr/local/bin/curl
ln -sf /opt/tb2_recovery/shims/curl /usr/bin/curl
ln -sf /opt/tb2_recovery/shims/wget /usr/local/bin/wget
ln -sf /opt/tb2_recovery/shims/wget /usr/bin/wget
touch /opt/tb2_recovery/.prebaked
export PATH="/usr/local/bin:$HOME/.local/bin:$PATH"
if [[ "${TB2_PREBAKE_MINI_SWE_AGENT:-1}" == "1" ]]; then
  mini-swe-agent --help >/dev/null
fi
if [[ "${TB2_PREBAKE_ASCIINEMA:-1}" == "1" ]]; then
  test -x /usr/local/bin/asciinema
  asciinema --version >/dev/null
fi
uv --version >/dev/null
uvx --version >/dev/null
tmux -V >/dev/null
'

  docker commit "$tmp" "$image" >/dev/null
  docker rm -f "$tmp" >/dev/null
  trap - RETURN
  echo "OK prebaked: $image"
}

for image in "$@"; do
  install_one "$image"
done
