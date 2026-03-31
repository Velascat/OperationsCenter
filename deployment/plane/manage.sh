#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PLANE_DIR="${ROOT_DIR}/deployment/plane"
RUNTIME_DIR="${PLANE_DIR}/runtime"
RUNTIME_LOG_DIR="${ROOT_DIR}/logs/local/plane-runtime"
SETUP_SH="${RUNTIME_DIR}/setup.sh"
PLANE_APP_DIR="${RUNTIME_DIR}/plane-app"
PLANE_ENV="${PLANE_APP_DIR}/plane.env"
PLANE_URL="${CONTROL_PLANE_PLANE_URL:-http://localhost:8080}"
PLANE_VERSION="${CONTROL_PLANE_PLANE_VERSION:-}"
PLANE_SETUP_URL="${CONTROL_PLANE_PLANE_SETUP_URL:-}"
LAST_RUNTIME_LOG=""

timestamp() {
  date +"%Y%m%dT%H%M%S"
}

download_setup() {
  mkdir -p "${RUNTIME_DIR}"
  if [[ ! -x "${SETUP_SH}" ]]; then
    local setup_url="${PLANE_SETUP_URL}"
    if [[ -z "${setup_url}" ]]; then
      if [[ -n "${PLANE_VERSION}" ]]; then
        setup_url="https://github.com/makeplane/plane/releases/download/${PLANE_VERSION}/setup.sh"
      else
        setup_url="https://github.com/makeplane/plane/releases/latest/download/setup.sh"
      fi
    fi
    echo "Using Plane setup source: ${setup_url}"
    curl -fsSL -o "${SETUP_SH}" "${setup_url}"
    chmod +x "${SETUP_SH}"
  fi
}

run_setup_menu() {
  local action="$1"
  mkdir -p "${RUNTIME_LOG_DIR}"
  local log_path="${RUNTIME_LOG_DIR}/$(timestamp)_plane_${action}.log"
  LAST_RUNTIME_LOG="${log_path}"
  if ! (
    cd "${RUNTIME_DIR}"
    printf '%s\n8\n' "${action}" | ./setup.sh
  ) >"${log_path}" 2>&1; then
    echo "Plane command failed. Runtime log: ${log_path}"
    tail -n 40 "${log_path}" || true
    return 1
  fi
  echo "Plane runtime log: ${log_path}"
}

set_env_value() {
  local key="$1"
  local value="$2"
  if [[ ! -f "${PLANE_ENV}" ]]; then
    return 1
  fi
  if grep -q "^${key}=" "${PLANE_ENV}"; then
    sed -i "s#^${key}=.*#${key}=${value}#" "${PLANE_ENV}"
  else
    printf '%s=%s\n' "${key}" "${value}" >> "${PLANE_ENV}"
  fi
}

configure_plane_env() {
  if [[ ! -f "${PLANE_ENV}" ]]; then
    return 1
  fi

  local host_port="8080"
  if [[ "${PLANE_URL}" =~ :([0-9]+)$ ]]; then
    host_port="${BASH_REMATCH[1]}"
  fi

  set_env_value "LISTEN_HTTP_PORT" "${host_port}"
  set_env_value "WEB_URL" "${PLANE_URL}"
  set_env_value "CORS_ALLOWED_ORIGINS" "${PLANE_URL}"
}

ensure_installed() {
  download_setup
  if [[ ! -d "${PLANE_APP_DIR}" ]]; then
    run_setup_menu 1
  fi
  configure_plane_env
}

wait_until_ready() {
  local attempts=30
  local delay_seconds=5

  for ((i=1; i<=attempts; i++)); do
    if curl -fsS --max-time 5 "${PLANE_URL}" >/dev/null 2>&1; then
      echo "Plane is reachable at ${PLANE_URL}"
      return 0
    fi
    sleep "${delay_seconds}"
  done

  echo "Plane did not become reachable at ${PLANE_URL}"
  echo "Check logs with: (cd ${RUNTIME_DIR} && ./setup.sh and choose 'View Logs')"
  return 1
}

cmd="${1:-}"
if [[ -z "${cmd}" ]]; then
  echo "Usage: deployment/plane/manage.sh {up|down|status}"
  exit 1
fi

case "${cmd}" in
  up)
    echo "Preparing local Plane runtime..."
    ensure_installed
    echo "Starting Plane containers..."
    run_setup_menu 2
    echo "Checking Plane readiness..."
    wait_until_ready
    ;;
  down)
    if [[ -x "${SETUP_SH}" ]]; then
      echo "Stopping Plane containers..."
      run_setup_menu 3
      echo "Plane containers stopped."
    else
      echo "Plane runtime is not installed yet."
    fi
    ;;
  status)
    if curl -fsS --max-time 5 "${PLANE_URL}" >/dev/null 2>&1; then
      echo "Plane is reachable at ${PLANE_URL}"
      exit 0
    fi
    echo "Plane is not reachable at ${PLANE_URL}"
    exit 1
    ;;
  *)
    echo "Usage: deployment/plane/manage.sh {up|down|status}"
    exit 1
    ;;
esac
