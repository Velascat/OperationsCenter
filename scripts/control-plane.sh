#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"
CONFIG_PATH="${CONTROL_PLANE_CONFIG:-${ROOT_DIR}/config/control_plane.local.yaml}"
ENV_PATH="${CONTROL_PLANE_ENV_FILE:-${ROOT_DIR}/.env.control-plane.local}"
BOOTSTRAP_STAMP="${VENV_DIR}/.control-plane-bootstrap"

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

usage() {
  cat <<EOF
Usage:
  scripts/control-plane.sh setup
  scripts/control-plane.sh test
  scripts/control-plane.sh api
  scripts/control-plane.sh worker --task-id TASK-123
  scripts/control-plane.sh smoke --task-id TASK-123 --comment-only

Environment:
  CONTROL_PLANE_CONFIG   Override config path (default: ${CONFIG_PATH})
  CONTROL_PLANE_ENV_FILE Override env file path (default: ${ENV_PATH})
EOF
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
    exec "${VENV_DIR}/bin/python" -m control_plane.entrypoints.setup.main init "$@"
    ;;
  test)
    ensure_venv
    exec "${VENV_DIR}/bin/pytest" -q "$@"
    ;;
  api)
    ensure_venv
    load_env_file
    exec "${VENV_DIR}/bin/python" -m uvicorn control_plane.entrypoints.api.main:app --reload "$@"
    ;;
  worker)
    ensure_venv
    load_env_file
    exec "${VENV_DIR}/bin/python" -m control_plane.entrypoints.worker.main --config "${CONFIG_PATH}" "$@"
    ;;
  smoke)
    ensure_venv
    load_env_file
    exec "${VENV_DIR}/bin/python" -m control_plane.entrypoints.smoke.plane --config "${CONFIG_PATH}" "$@"
    ;;
  *)
    usage
    exit 1
    ;;
esac
