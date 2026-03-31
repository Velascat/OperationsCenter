#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"
CONFIG_PATH="${CONTROL_PLANE_CONFIG:-${ROOT_DIR}/config/control_plane.local.yaml}"
ENV_PATH="${CONTROL_PLANE_ENV_FILE:-${ROOT_DIR}/.env.control-plane.local}"
BOOTSTRAP_STAMP="${VENV_DIR}/.control-plane-bootstrap"
LOG_DIR="${ROOT_DIR}/logs/local"
WATCH_DIR="${LOG_DIR}/watch-all"
PLANE_MANAGER="${ROOT_DIR}/deployment/plane/manage.sh"

ensure_venv() {
  if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
    python3 -m venv "${VENV_DIR}"
  fi
  if [[ ! -f "${BOOTSTRAP_STAMP}" || "${ROOT_DIR}/pyproject.toml" -nt "${BOOTSTRAP_STAMP}" ]]; then
    "${VENV_DIR}/bin/python" -m pip install --upgrade pip
    "${VENV_DIR}/bin/python" -m pip install -e '.[dev]'
    touch "${BOOTSTRAP_STAMP}"
  fi
}

load_env_file() {
  if [[ -f "${ENV_PATH}" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "${ENV_PATH}"
    set +a
  fi
}

maybe_open_browser() {
  if [[ "${CONTROL_PLANE_PLANE_OPEN_BROWSER:-}" != "1" ]]; then
    return 0
  fi
  if [[ -z "${CONTROL_PLANE_PLANE_URL:-}" ]]; then
    return 0
  fi
  if command -v xdg-open >/dev/null 2>&1; then
    xdg-open "${CONTROL_PLANE_PLANE_URL}" >/dev/null 2>&1 || true
  fi
}

timestamp() {
  date +"%Y%m%dT%H%M%S"
}

run_with_log() {
  local name="$1"
  shift
  mkdir -p "${LOG_DIR}"
  local log_path="${LOG_DIR}/$(timestamp)_${name}.log"
  echo "Writing log: ${log_path}"
  "$@" 2>&1 | tee "${log_path}"
}

usage() {
  cat <<EOF
Usage:
  scripts/control-plane.sh setup
  scripts/control-plane.sh start
  scripts/control-plane.sh stop
  scripts/control-plane.sh run-next
  scripts/control-plane.sh watch-all
  scripts/control-plane.sh watch-all-stop
  scripts/control-plane.sh watch-all-status
  scripts/control-plane.sh watch --role goal
  scripts/control-plane.sh run --task-id TASK-123
  scripts/control-plane.sh plane-doctor [--task-id TASK-123]
  scripts/control-plane.sh dependency-check [--create-plane-tasks]
  scripts/control-plane.sh plane-up
  scripts/control-plane.sh plane-down
  scripts/control-plane.sh plane-status
  scripts/control-plane.sh dev-up
  scripts/control-plane.sh dev-down
  scripts/control-plane.sh providers-status
  scripts/control-plane.sh doctor
  scripts/control-plane.sh test
  scripts/control-plane.sh api
  scripts/control-plane.sh worker --task-id TASK-123
  scripts/control-plane.sh smoke --task-id TASK-123 --comment-only

Environment:
  CONTROL_PLANE_CONFIG   Override config path (default: ${CONFIG_PATH})
  CONTROL_PLANE_ENV_FILE Override env file path (default: ${ENV_PATH})
EOF
}

watch_pid_file() {
  local role="$1"
  echo "${WATCH_DIR}/${role}.pid"
}

watch_log_file() {
  local role="$1"
  echo "${WATCH_DIR}/$(timestamp)_${role}.log"
}

start_watch_role() {
  local role="$1"
  local pid_file
  pid_file="$(watch_pid_file "${role}")"
  if [[ -f "${pid_file}" ]] && kill -0 "$(cat "${pid_file}")" >/dev/null 2>&1; then
    echo "watch-${role} already running with PID $(cat "${pid_file}")"
    return 0
  fi
  rm -f "${pid_file}"
  mkdir -p "${WATCH_DIR}"
  local log_file
  log_file="$(watch_log_file "${role}")"
  setsid /bin/bash -lc "
    set -a
    source '${ENV_PATH}'
    set +a
    exec '${VENV_DIR}/bin/python' -m control_plane.entrypoints.worker.main \
      --config '${CONFIG_PATH}' \
      --watch \
      --role '${role}'
  " >>"${log_file}" 2>&1 < /dev/null &
  local pid=$!
  echo "${pid}" > "${pid_file}"
  echo "watch-${role} started: pid=${pid} log=${log_file}"
}

stop_watch_role() {
  local role="$1"
  local pid_file
  pid_file="$(watch_pid_file "${role}")"
  if [[ ! -f "${pid_file}" ]]; then
    echo "watch-${role} is not running"
    return 0
  fi
  local pid
  pid="$(cat "${pid_file}")"
  if kill -0 "${pid}" >/dev/null 2>&1; then
    kill "${pid}" >/dev/null 2>&1 || true
    echo "watch-${role} stopped: pid=${pid}"
  else
    echo "watch-${role} was not running"
  fi
  rm -f "${pid_file}"
}

status_watch_role() {
  local role="$1"
  local pid_file
  pid_file="$(watch_pid_file "${role}")"
  if [[ -f "${pid_file}" ]] && kill -0 "$(cat "${pid_file}")" >/dev/null 2>&1; then
    echo "watch-${role}: running (pid $(cat "${pid_file}"))"
  else
    echo "watch-${role}: stopped"
  fi
}

cmd="${1:-}"
if [[ -z "${cmd}" ]]; then
  usage
  exit 1
fi
shift || true

cd "${ROOT_DIR}"

case "${cmd}" in
  setup)
    ensure_venv
    run_with_log setup "${VENV_DIR}/bin/python" -m control_plane.entrypoints.setup.main "$@"
    ;;
  start|plane-up)
    load_env_file
    run_with_log plane-up "${PLANE_MANAGER}" up
    maybe_open_browser
    ;;
  stop|plane-down)
    load_env_file
    run_with_log plane-down "${PLANE_MANAGER}" down
    ;;
  plane-status)
    load_env_file
    run_with_log plane-status "${PLANE_MANAGER}" status
    ;;
  dev-up)
    ensure_venv
    load_env_file
    run_with_log plane-up "${PLANE_MANAGER}" up
    maybe_open_browser
    run_with_log plane-status "${PLANE_MANAGER}" status
    ;;
  dev-down)
    load_env_file
    run_with_log plane-down "${PLANE_MANAGER}" down
    ;;
  providers-status|doctor)
    ensure_venv
    load_env_file
    run_with_log providers-status "${VENV_DIR}/bin/python" -m control_plane.entrypoints.setup.doctor "$@"
    ;;
  test)
    ensure_venv
    run_with_log test "${VENV_DIR}/bin/pytest" -q "$@"
    ;;
  api)
    ensure_venv
    load_env_file
    run_with_log api "${VENV_DIR}/bin/python" -m uvicorn control_plane.entrypoints.api.main:app --reload "$@"
    ;;
  run)
    ensure_venv
    load_env_file
    run_with_log worker "${VENV_DIR}/bin/python" -m control_plane.entrypoints.worker.main --config "${CONFIG_PATH}" "$@"
    ;;
  run-next)
    ensure_venv
    load_env_file
    run_with_log worker "${VENV_DIR}/bin/python" -m control_plane.entrypoints.worker.main --config "${CONFIG_PATH}" --first-ready "$@"
    ;;
  watch)
    ensure_venv
    load_env_file
    run_with_log worker "${VENV_DIR}/bin/python" -m control_plane.entrypoints.worker.main --config "${CONFIG_PATH}" --watch "$@"
    ;;
  watch-all)
    ensure_venv
    load_env_file
    start_watch_role goal
    start_watch_role test
    start_watch_role improve
    ;;
  watch-all-stop)
    stop_watch_role goal
    stop_watch_role test
    stop_watch_role improve
    ;;
  watch-all-status)
    status_watch_role goal
    status_watch_role test
    status_watch_role improve
    ;;
  worker)
    ensure_venv
    load_env_file
    run_with_log worker "${VENV_DIR}/bin/python" -m control_plane.entrypoints.worker.main --config "${CONFIG_PATH}" "$@"
    ;;
  smoke)
    ensure_venv
    load_env_file
    run_with_log smoke "${VENV_DIR}/bin/python" -m control_plane.entrypoints.smoke.plane --config "${CONFIG_PATH}" "$@"
    ;;
  plane-doctor)
    ensure_venv
    load_env_file
    run_with_log plane-doctor "${VENV_DIR}/bin/python" -m control_plane.entrypoints.smoke.plane_doctor --config "${CONFIG_PATH}" "$@"
    ;;
  dependency-check)
    ensure_venv
    load_env_file
    run_with_log dependency-check "${VENV_DIR}/bin/python" -m control_plane.entrypoints.maintenance.dependency_check --config "${CONFIG_PATH}" "$@"
    ;;
  *)
    usage
    exit 1
    ;;
esac
