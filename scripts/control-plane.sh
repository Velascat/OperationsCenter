#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"
CONFIG_PATH="${CONTROL_PLANE_CONFIG:-${ROOT_DIR}/config/control_plane.local.yaml}"
ENV_PATH="${CONTROL_PLANE_ENV_FILE:-${ROOT_DIR}/.env.control-plane.local}"
BOOTSTRAP_STAMP="${VENV_DIR}/.control-plane-bootstrap"
LOG_DIR="${ROOT_DIR}/logs/local"
WATCH_DIR="${LOG_DIR}/watch-all"
REPORT_DIR="${ROOT_DIR}/tools/report/kodo_plane"
PLANE_MANAGER="${ROOT_DIR}/deployment/plane/manage.sh"
JANITOR_MAX_AGE_DAYS="${CONTROL_PLANE_RETENTION_DAYS:-1}"

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

run_janitor() {
  local max_age_minutes=$((JANITOR_MAX_AGE_DAYS * 24 * 60))
  [[ "${max_age_minutes}" -lt 0 ]] && max_age_minutes=1440

  mkdir -p "${LOG_DIR}" "${WATCH_DIR}" "${REPORT_DIR}"

  while IFS= read -r path; do
    rm -f "${path}"
  done < <(find "${LOG_DIR}" -type f ! -name "*.pid" -mmin +"${max_age_minutes}" -print)

  while IFS= read -r path; do
    rm -f "${path}"
  done < <(find "${WATCH_DIR}" -type f -name "*.pid" -mmin +"${max_age_minutes}" -print)

  while IFS= read -r path; do
    rm -rf "${path}"
  done < <(find "${REPORT_DIR}" -mindepth 1 -maxdepth 1 -type d -mmin +"${max_age_minutes}" -print)

  find "${LOG_DIR}" -depth -type d -empty -delete >/dev/null 2>&1 || true

  # Clean up stale task branches left in local repo clones by kodo.
  # Branches matching "task/*" or "cp/*" whose worktrees no longer exist are removed.
  _cleanup_stale_task_branches
}

_cleanup_stale_task_branches() {
  local branch deleted=0
  # Only act on repos that are locally cloned (have a .git directory).
  for repo_dir in "${ROOT_DIR}"/workspace/*/; do
    [[ -d "${repo_dir}/.git" ]] || continue
    while IFS= read -r branch; do
      branch="${branch#  }"  # strip leading whitespace
      # Skip the current branch and remote-tracking refs.
      [[ "${branch}" == "* "* ]] && continue
      [[ "${branch}" == remotes/* ]] && continue
      # Only prune branches that look like task branches.
      if [[ "${branch}" =~ ^(task/|cp/|kodo/|plane/) ]]; then
        git -C "${repo_dir}" branch -D "${branch}" >/dev/null 2>&1 && ((deleted++)) || true
      fi
    done < <(git -C "${repo_dir}" branch 2>/dev/null | grep -E '^\s*(task/|cp/|kodo/|plane/)' || true)
  done
  [[ "${deleted}" -gt 0 ]] && echo "Janitor: removed ${deleted} stale task branch(es)" || true
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
  scripts/control-plane.sh dev-status
  scripts/control-plane.sh watch --role goal
  scripts/control-plane.sh watch --role review
  scripts/control-plane.sh watch-stop --role goal
  scripts/control-plane.sh run --task-id TASK-123
  scripts/control-plane.sh plane-doctor [--task-id TASK-123]
  scripts/control-plane.sh dependency-check [--create-plane-tasks]
  scripts/control-plane.sh janitor
  scripts/control-plane.sh plane-up
  scripts/control-plane.sh plane-down
  scripts/control-plane.sh plane-status
  scripts/control-plane.sh dev-up
  scripts/control-plane.sh dev-down
  scripts/control-plane.sh dev-restart
  scripts/control-plane.sh providers-status
  scripts/control-plane.sh doctor
  scripts/control-plane.sh test
  scripts/control-plane.sh worker --task-id TASK-123
  scripts/control-plane.sh smoke --task-id TASK-123 --comment-only
  scripts/control-plane.sh observe-repo [--repo /abs/path]
  scripts/control-plane.sh generate-insights [--repo /abs/path]
  scripts/control-plane.sh decide-proposals [--repo /abs/path]
  scripts/control-plane.sh propose-from-candidates [--repo /abs/path] [--dry-run]
  scripts/control-plane.sh autonomy-cycle --config FILE [--repo PATH] [--execute] [--all-families]
  scripts/control-plane.sh analyze-artifacts [--repo NAME] [--limit N] [--json]
  scripts/control-plane.sh tune-autonomy [--window N] [--apply]
  scripts/control-plane.sh promote-backlog [--family FAMILY] [--execute]

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

watch_status_file() {
  local role="$1"
  echo "${WATCH_DIR}/${role}.status.json"
}

start_watch_role() {
  local role="$1"
  local poll_interval=20
  case "${role}" in
    goal) poll_interval="${CONTROL_PLANE_WATCH_INTERVAL_GOAL_SECONDS:-${CONTROL_PLANE_GOAL_POLL_SECONDS:-30}}" ;;
    test) poll_interval="${CONTROL_PLANE_WATCH_INTERVAL_TEST_SECONDS:-${CONTROL_PLANE_TEST_POLL_SECONDS:-60}}" ;;
    improve) poll_interval="${CONTROL_PLANE_WATCH_INTERVAL_IMPROVE_SECONDS:-${CONTROL_PLANE_IMPROVE_POLL_SECONDS:-60}}" ;;
    propose) poll_interval="${CONTROL_PLANE_WATCH_INTERVAL_PROPOSE_SECONDS:-${CONTROL_PLANE_PROPOSE_POLL_SECONDS:-120}}" ;;
    review) poll_interval="${CONTROL_PLANE_WATCH_INTERVAL_REVIEW_SECONDS:-60}" ;;
    spec) poll_interval="${CONTROL_PLANE_WATCH_INTERVAL_SPEC_SECONDS:-120}" ;;
  esac
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
  # The outer bash wrapper restarts the watcher automatically on non-zero exit.
  # It traps SIGTERM so that stop_watch_role kills both the wrapper and any
  # running python child.  A clean exit (exit code 0) breaks the loop — this
  # covers deliberate stops such as credential failures at startup.
  if [[ "${role}" == "review" ]]; then
    setsid /bin/bash -lc "
      cd '${ROOT_DIR}'
      set -a
      source '${ENV_PATH}'
      set +a
      _child_pid=''
      trap 'kill \$_child_pid 2>/dev/null; exit 0' TERM INT
      while true; do
        '${VENV_DIR}/bin/python' -m control_plane.entrypoints.reviewer.main \
          --config '${CONFIG_PATH}' \
          --watch \
          --poll-interval-seconds '${poll_interval}' \
          --status-dir '${WATCH_DIR}' &
        _child_pid=\$!
        wait \$_child_pid
        _exit=\$?
        [[ ! -f '${pid_file}' ]] && exit 0
        [[ \$_exit -eq 0 ]] && exit 0
        echo \"{\\\"event\\\":\\\"watcher_restart\\\",\\\"role\\\":\\\"${role}\\\",\\\"exit_code\\\":\$_exit}\"
        sleep 5
      done
    " >>"${log_file}" 2>&1 < /dev/null &
  elif [[ "${role}" == "spec" ]]; then
    setsid /bin/bash -lc "
      cd '${ROOT_DIR}'
      set -a
      source '${ENV_PATH}'
      set +a
      _child_pid=''
      trap 'kill \$_child_pid 2>/dev/null; exit 0' TERM INT
      while true; do
        '${VENV_DIR}/bin/python' -m control_plane.entrypoints.spec_director.main \
          --config '${CONFIG_PATH}' &
        _child_pid=\$!
        wait \$_child_pid
        _exit=\$?
        [[ ! -f '${pid_file}' ]] && exit 0
        [[ \$_exit -eq 0 ]] && exit 0
        echo \"{\\\"event\\\":\\\"watcher_restart\\\",\\\"role\\\":\\\"${role}\\\",\\\"exit_code\\\":\$_exit}\"
        sleep 5
      done
    " >>"${log_file}" 2>&1 < /dev/null &
  else
    setsid /bin/bash -lc "
      cd '${ROOT_DIR}'
      set -a
      source '${ENV_PATH}'
      set +a
      _child_pid=''
      trap 'kill \$_child_pid 2>/dev/null; exit 0' TERM INT
      while true; do
        '${VENV_DIR}/bin/python' -m control_plane.entrypoints.worker.main \
          --config '${CONFIG_PATH}' \
          --watch \
          --role '${role}' \
          --poll-interval-seconds '${poll_interval}' \
          --status-dir '${WATCH_DIR}' &
        _child_pid=\$!
        wait \$_child_pid
        _exit=\$?
        [[ ! -f '${pid_file}' ]] && exit 0
        [[ \$_exit -eq 0 ]] && exit 0
        echo \"{\\\"event\\\":\\\"watcher_restart\\\",\\\"role\\\":\\\"${role}\\\",\\\"exit_code\\\":\$_exit}\"
        sleep 5
      done
    " >>"${log_file}" 2>&1 < /dev/null &
  fi
  local pid=$!
  echo "${pid}" > "${pid_file}"
  echo "watch-${role} started (auto-restart enabled): pid=${pid} poll_interval=${poll_interval}s log=${log_file}"
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
  local status_file
  pid_file="$(watch_pid_file "${role}")"
  status_file="$(watch_status_file "${role}")"
  if [[ -f "${pid_file}" ]] && kill -0 "$(cat "${pid_file}")" >/dev/null 2>&1; then
    if [[ -f "${status_file}" ]]; then
      python3 - "${role}" "${pid_file}" "${status_file}" <<'PY'
import json, sys
role, pid_file, status_file = sys.argv[1:]
pid = open(pid_file).read().strip()
data = json.load(open(status_file))
counters = data.get("counters", {})
print(
    f"watch-{role}: running (pid {pid}) | "
    f"cycle={data.get('cycle')} state={data.get('state')} last_action={data.get('last_action')} "
    f"task_id={data.get('task_id') or '-'} task_kind={data.get('task_kind') or '-'} "
    f"followups={len(data.get('follow_up_task_ids') or [])} triaged={counters.get('blocked_tasks_triaged', 0)} "
    f"created={counters.get('follow_up_tasks_created', 0)} updated_at={data.get('updated_at')}"
)
PY
    else
      echo "watch-${role}: running (pid $(cat "${pid_file}"))"
    fi
  else
    if [[ -f "${status_file}" ]]; then
      python3 - "${role}" "${status_file}" <<'PY'
import json, sys
role, status_file = sys.argv[1:]
data = json.load(open(status_file))
print(
    f"watch-{role}: stopped | "
    f"last_cycle={data.get('cycle')} state={data.get('state')} last_action={data.get('last_action')} "
    f"task_id={data.get('task_id') or '-'} updated_at={data.get('updated_at')}"
)
PY
    else
      echo "watch-${role}: stopped"
    fi
  fi
}

cmd="${1:-}"
if [[ -z "${cmd}" ]]; then
  usage
  exit 1
fi
shift || true

cd "${ROOT_DIR}"
# Skip janitor for read-only / stop commands — they're fast and don't need it.
case "${cmd}" in
  watch-all-status|dev-status|watch-all-stop|watch-stop|plane-status|providers-status|doctor) ;;
  *) run_janitor ;;
esac

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
    start_watch_role goal
    start_watch_role test
    start_watch_role improve
    start_watch_role propose
    start_watch_role review
    start_watch_role spec
    run_with_log plane-status "${PLANE_MANAGER}" status
    ;;
  dev-down)
    load_env_file
    stop_watch_role goal
    stop_watch_role test
    stop_watch_role improve
    stop_watch_role propose
    stop_watch_role review
    stop_watch_role spec
    run_with_log plane-down "${PLANE_MANAGER}" down
    ;;
  dev-restart)
    load_env_file
    stop_watch_role goal
    stop_watch_role test
    stop_watch_role improve
    stop_watch_role propose
    stop_watch_role review
    stop_watch_role spec
    run_with_log plane-down "${PLANE_MANAGER}" down
    ensure_venv
    run_with_log plane-up "${PLANE_MANAGER}" up
    maybe_open_browser
    start_watch_role goal
    start_watch_role test
    start_watch_role improve
    start_watch_role propose
    start_watch_role review
    start_watch_role spec
    run_with_log plane-status "${PLANE_MANAGER}" status
    ;;
  dev-status)
    load_env_file
    run_with_log plane-status "${PLANE_MANAGER}" status || true
    status_watch_role goal
    status_watch_role test
    status_watch_role improve
    status_watch_role propose
    status_watch_role review
    status_watch_role spec
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
    # Parse --role from args so we can dispatch through start_watch_role,
    # which handles the reviewer entrypoint, pid files, and auto-restart.
    _watch_role=""
    for _arg in "$@"; do
      if [[ "${_watch_role}" == "__next__" ]]; then
        _watch_role="${_arg}"
        break
      fi
      [[ "${_arg}" == "--role" ]] && _watch_role="__next__"
    done
    if [[ -n "${_watch_role}" && "${_watch_role}" != "__next__" ]]; then
      start_watch_role "${_watch_role}"
    else
      # No --role given: run worker inline (foreground, for debugging).
      run_with_log worker "${VENV_DIR}/bin/python" -m control_plane.entrypoints.worker.main --config "${CONFIG_PATH}" --watch "$@"
    fi
    ;;
  watch-stop)
    # Stop a single watcher role: scripts/control-plane.sh watch-stop --role goal
    _stop_role=""
    for _arg in "$@"; do
      if [[ "${_stop_role}" == "__next__" ]]; then
        _stop_role="${_arg}"
        break
      fi
      [[ "${_arg}" == "--role" ]] && _stop_role="__next__"
    done
    if [[ -n "${_stop_role}" && "${_stop_role}" != "__next__" ]]; then
      stop_watch_role "${_stop_role}"
    else
      echo "Usage: watch-stop --role <role>" >&2
      exit 1
    fi
    ;;
  watch-all)
    ensure_venv
    load_env_file
    start_watch_role goal
    start_watch_role test
    start_watch_role improve
    start_watch_role propose
    start_watch_role review
    start_watch_role spec
    ;;
  watch-all-stop)
    stop_watch_role goal
    stop_watch_role test
    stop_watch_role improve
    stop_watch_role propose
    stop_watch_role review
    stop_watch_role spec
    ;;
  watch-all-status)
    status_watch_role goal
    status_watch_role test
    status_watch_role improve
    status_watch_role propose
    status_watch_role review
    status_watch_role spec
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
  observe-repo)
    ensure_venv
    load_env_file
    run_with_log observe-repo "${VENV_DIR}/bin/python" -m control_plane.entrypoints.observer.main --config "${CONFIG_PATH}" "$@"
    ;;
  backfill-pr-reviews)
    ensure_venv
    load_env_file
    run_with_log backfill-pr-reviews "${VENV_DIR}/bin/python" -m control_plane.entrypoints.reviewer.main --config "${CONFIG_PATH}" --backfill "$@"
    ;;
  generate-insights)
    ensure_venv
    load_env_file
    run_with_log generate-insights "${VENV_DIR}/bin/python" -m control_plane.entrypoints.insights.main "$@"
    ;;
  decide-proposals)
    ensure_venv
    load_env_file
    run_with_log decide-proposals "${VENV_DIR}/bin/python" -m control_plane.entrypoints.decision.main "$@"
    ;;
  propose-from-candidates)
    ensure_venv
    load_env_file
    run_with_log propose-from-candidates "${VENV_DIR}/bin/python" -m control_plane.entrypoints.proposer.main --config "${CONFIG_PATH}" "$@"
    ;;
  autonomy-cycle)
    ensure_venv
    load_env_file
    run_with_log autonomy-cycle "${VENV_DIR}/bin/python" -m control_plane.entrypoints.autonomy_cycle.main --config "${CONFIG_PATH}" "$@"
    ;;
  analyze-artifacts)
    ensure_venv
    load_env_file
    run_with_log analyze-artifacts "${VENV_DIR}/bin/python" -m control_plane.entrypoints.analyze.main "$@"
    ;;
  tune-autonomy)
    ensure_venv
    load_env_file
    run_with_log tune-autonomy "${VENV_DIR}/bin/python" -m control_plane.entrypoints.tuning.main --config "${CONFIG_PATH}" "$@"
    ;;
  promote-backlog)
    ensure_venv
    load_env_file
    run_with_log promote-backlog "${VENV_DIR}/bin/python" -m control_plane.entrypoints.promote_backlog.main --config "${CONFIG_PATH}" "$@"
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
  janitor)
    echo "Janitor complete. Retention window: ${JANITOR_MAX_AGE_DAYS} day(s)"
    ;;
  *)
    usage
    exit 1
    ;;
esac
