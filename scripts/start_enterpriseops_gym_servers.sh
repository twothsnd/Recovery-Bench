#!/usr/bin/env bash
set -euo pipefail

read -r -a domains <<< "${ENTERPRISEOPS_DOMAINS:-teams}"
image_prefix="${ENTERPRISEOPS_IMAGE_PREFIX:-shivakrishnareddyma225/enterpriseops-gym-mcp}"
docker_mirror_prefix="${ENTERPRISEOPS_DOCKER_MIRROR_PREFIX:-}"
docker_mirror_prefixes="${ENTERPRISEOPS_DOCKER_MIRROR_PREFIXES:-}"
force_pull="${ENTERPRISEOPS_FORCE_PULL:-0}"
replace="${ENTERPRISEOPS_REPLACE_CONTAINERS:-0}"

host_port_for() {
  local override_name="ENTERPRISEOPS_${1^^}_HOST_PORT"
  local override_value="${!override_name:-}"
  if [ -n "${override_value}" ]; then
    echo "${override_value}"
    return 0
  fi
  case "$1" in
    csm) echo 8001 ;;
    teams) echo 8002 ;;
    calendar) echo 8003 ;;
    email) echo 8004 ;;
    itsm) echo 8006 ;;
    hr) echo 8008 ;;
    drive) echo 8009 ;;
    *) return 1 ;;
  esac
}

container_port_for() {
  case "$1" in
    calendar) echo 8003 ;;
    csm|teams|email|itsm|hr|drive) echo 8005 ;;
    *) return 1 ;;
  esac
}

for domain in "${domains[@]}"; do
  host_port="$(host_port_for "${domain}")"
  container_port="$(container_port_for "${domain}")"
  image="${image_prefix}-${domain}:latest"
  name="enterpriseops-gym-${domain}"

  if [ "${force_pull}" = "1" ] || ! docker image inspect "${image}" >/dev/null 2>&1; then
    echo "pulling official EnterpriseOps-Gym MCP image ${image}"
    if ! docker pull "${image}"; then
      mirror_candidates="${docker_mirror_prefixes}"
      if [ -n "${docker_mirror_prefix}" ]; then
        mirror_candidates="${mirror_candidates} ${docker_mirror_prefix}"
      fi
      if [ -z "${mirror_candidates// }" ]; then
        echo "failed to pull ${image}; set ENTERPRISEOPS_DOCKER_MIRROR_PREFIXES or ENTERPRISEOPS_DOCKER_MIRROR_PREFIX to use Docker Hub mirror transports" >&2
        exit 3
      fi
      pulled=0
      for mirror_prefix in ${mirror_candidates}; do
        mirror_image="${mirror_prefix%/}/${image}"
        echo "official pull failed; trying ${mirror_image}"
        if docker pull "${mirror_image}"; then
          docker tag "${mirror_image}" "${image}"
          pulled=1
          break
        fi
      done
      if [ "${pulled}" != "1" ]; then
        echo "failed to pull ${image} through any configured Docker Hub mirror transport" >&2
        exit 3
      fi
    fi
  else
    echo "using local EnterpriseOps-Gym MCP image ${image}"
  fi

  if docker ps -a --format '{{.Names}}' | grep -Fxq "${name}"; then
    if [ "${replace}" = "1" ]; then
      docker rm -f "${name}"
    elif docker ps --format '{{.Names}}' | grep -Fxq "${name}"; then
      echo "container already running: ${name}"
      continue
    else
      echo "starting existing container: ${name}"
      docker start "${name}"
      continue
    fi
  fi

  echo "starting ${name} on localhost:${host_port}"
  docker run -d \
    --name "${name}" \
    -p "${host_port}:${container_port}" \
    "${image}"
done
