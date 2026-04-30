# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""S7-1: Process supervisor for OperationsCenter watchers.

Spawns and monitors the worker/reviewer/autonomy_cycle processes.  When a
managed process exits unexpectedly (crash or OOM) or its heartbeat goes stale,
the supervisor restarts it after a brief back-off.

Usage
-----
    python -m operations_center.entrypoints.supervisor.main \\
        --config /path/to/operations_center.yaml \\
        --log-dir logs/local \\
        --manifest /path/to/supervisor_manifest.yaml

Manifest format (YAML)
---------------------
    processes:
      - role: goal
        command: ["python", "-m", "operations_center.entrypoints.worker.main",
                  "--config", "/path/to/config.yaml",
                  "--watch", "--role", "goal", "--status-dir", "logs/local"]
      - role: improve
        command: [...]
      - role: reviewer
        command: [...]

Each entry requires:
  - ``role``: unique name used for heartbeat lookup and log tagging
  - ``command``: the full argv list to launch the process

Optional per-entry keys:
  - ``restart_max``: maximum restart attempts (default: unlimited / -1)
  - ``restart_backoff_seconds``: seconds to wait before restarting (default: 10)
"""
from __future__ import annotations

import argparse
import json
import logging
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

_logger = logging.getLogger(__name__)

# Seconds between supervisor health-check iterations.
_CHECK_INTERVAL_SECONDS = 30

# Maximum age of a heartbeat before the process is considered stale (seconds).
# Must be larger than the watcher's own write-heartbeat interval (currently 1
# cycle × poll_interval, typically 15–30 s).
_HEARTBEAT_MAX_AGE_SECONDS = 300  # 5 minutes

_RESTART_BACKOFF_DEFAULT = 10
_RESTART_MAX_DEFAULT = -1  # unlimited


@dataclass
class ManagedProcess:
    role: str
    command: list[str]
    restart_max: int = _RESTART_MAX_DEFAULT
    restart_backoff_seconds: int = _RESTART_BACKOFF_DEFAULT
    restart_count: int = 0
    proc: subprocess.Popen | None = field(default=None, repr=False)
    last_restart_at: datetime | None = None


def _load_manifest(path: Path) -> list[ManagedProcess]:
    raw = yaml.safe_load(path.read_text())
    processes: list[ManagedProcess] = []
    for entry in raw.get("processes", []):
        processes.append(
            ManagedProcess(
                role=str(entry["role"]),
                command=[str(c) for c in entry["command"]],
                restart_max=int(entry.get("restart_max", _RESTART_MAX_DEFAULT)),
                restart_backoff_seconds=int(
                    entry.get("restart_backoff_seconds", _RESTART_BACKOFF_DEFAULT)
                ),
            )
        )
    return processes


def _heartbeat_age_seconds(log_dir: Path, role: str, now: datetime) -> float | None:
    """Return seconds since the heartbeat file for *role* was written, or None if absent."""
    from datetime import timezone as _tz

    hb_file = log_dir / f"heartbeat_{role}.json"
    if not hb_file.exists():
        return None
    try:
        payload = json.loads(hb_file.read_text())
        ts = datetime.fromisoformat(str(payload["ts"]))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=_tz.utc)
        return (now - ts).total_seconds()
    except Exception:
        return None


def _is_alive(mp: ManagedProcess) -> bool:
    if mp.proc is None:
        return False
    return mp.proc.poll() is None


def _spawn(mp: ManagedProcess) -> None:
    """Start the managed process, storing the Popen handle."""
    _logger.info(json.dumps({
        "event": "supervisor_spawn",
        "role": mp.role,
        "command": mp.command,
        "restart_count": mp.restart_count,
    }))
    mp.proc = subprocess.Popen(mp.command)
    mp.last_restart_at = datetime.now(UTC)


def _terminate(mp: ManagedProcess) -> None:
    """Send SIGTERM then SIGKILL to the managed process."""
    if mp.proc is None:
        return
    try:
        mp.proc.terminate()
        try:
            mp.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            mp.proc.kill()
            mp.proc.wait(timeout=5)
    except Exception:
        pass
    mp.proc = None


def _maybe_restart(mp: ManagedProcess, *, reason: str) -> bool:
    """Restart *mp* if within restart_max.  Returns True if restarted."""
    if mp.restart_max >= 0 and mp.restart_count >= mp.restart_max:
        _logger.error(json.dumps({
            "event": "supervisor_restart_limit_reached",
            "role": mp.role,
            "restart_count": mp.restart_count,
            "restart_max": mp.restart_max,
            "reason": reason,
        }))
        return False
    _logger.warning(json.dumps({
        "event": "supervisor_restarting",
        "role": mp.role,
        "reason": reason,
        "restart_count": mp.restart_count,
        "backoff_seconds": mp.restart_backoff_seconds,
    }))
    _terminate(mp)
    time.sleep(mp.restart_backoff_seconds)
    mp.restart_count += 1
    _spawn(mp)
    return True


def _write_supervisor_status(log_dir: Path, processes: list[ManagedProcess]) -> None:
    """Write a structured status file so external tooling can observe the supervisor."""
    status_path = log_dir / "supervisor.status.json"
    try:
        status_path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {
            "updated_at": datetime.now(UTC).isoformat(),
            "processes": [
                {
                    "role": mp.role,
                    "alive": _is_alive(mp),
                    "pid": mp.proc.pid if mp.proc else None,
                    "restart_count": mp.restart_count,
                    "last_restart_at": mp.last_restart_at.isoformat() if mp.last_restart_at else None,
                }
                for mp in processes
            ],
        }
        status_path.write_text(json.dumps(payload, indent=2))
    except Exception:
        pass


def run_supervisor(
    processes: list[ManagedProcess],
    *,
    log_dir: Path,
    check_interval_seconds: int = _CHECK_INTERVAL_SECONDS,
    max_iterations: int | None = None,
) -> None:
    """Main supervisor loop.

    1. Spawns all processes on first iteration.
    2. Every *check_interval_seconds* checks each process for:
       - Process exit (poll() is not None) → restart.
       - Stale heartbeat (> _HEARTBEAT_MAX_AGE_SECONDS) → kill + restart.
    3. Writes supervisor.status.json on every iteration.
    """
    _logger.info(json.dumps({
        "event": "supervisor_start",
        "roles": [mp.role for mp in processes],
        "check_interval_seconds": check_interval_seconds,
    }))

    # Initial spawn
    for mp in processes:
        _spawn(mp)

    def _handle_sigterm(signum: int, frame: Any) -> None:  # noqa: ANN001
        _logger.info(json.dumps({"event": "supervisor_shutdown", "signal": signum}))
        for mp in processes:
            _terminate(mp)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _handle_sigterm)
    signal.signal(signal.SIGINT, _handle_sigterm)

    iteration = 0
    while True:
        iteration += 1
        if max_iterations is not None and iteration > max_iterations:
            break
        time.sleep(check_interval_seconds)
        now = datetime.now(UTC)
        for mp in processes:
            if not _is_alive(mp):
                exit_code = mp.proc.returncode if mp.proc else None
                _logger.warning(json.dumps({
                    "event": "supervisor_process_exited",
                    "role": mp.role,
                    "exit_code": exit_code,
                }))
                _maybe_restart(mp, reason=f"process_exited_code_{exit_code}")
                continue
            # Heartbeat staleness check
            age = _heartbeat_age_seconds(log_dir, mp.role, now)
            if age is not None and age > _HEARTBEAT_MAX_AGE_SECONDS:
                _logger.warning(json.dumps({
                    "event": "supervisor_heartbeat_stale",
                    "role": mp.role,
                    "heartbeat_age_seconds": round(age, 1),
                }))
                _maybe_restart(mp, reason=f"heartbeat_stale_{round(age)}s")
        _write_supervisor_status(log_dir, processes)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="OperationsCenter process supervisor — spawns and auto-restarts watchers"
    )
    parser.add_argument(
        "--manifest",
        required=True,
        help="Path to YAML manifest listing processes to supervise",
    )
    parser.add_argument(
        "--log-dir",
        default="logs/local",
        help="Directory to read heartbeat files from and write supervisor.status.json to",
    )
    parser.add_argument(
        "--check-interval",
        type=int,
        default=_CHECK_INTERVAL_SECONDS,
        help=f"Seconds between health checks (default: {_CHECK_INTERVAL_SECONDS})",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    manifest_path = Path(args.manifest)
    if not manifest_path.exists():
        _logger.error("Manifest not found: %s", manifest_path)
        sys.exit(1)

    processes = _load_manifest(manifest_path)
    if not processes:
        _logger.error("Manifest has no processes defined")
        sys.exit(1)

    log_dir = Path(args.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    run_supervisor(
        processes,
        log_dir=log_dir,
        check_interval_seconds=args.check_interval,
    )


if __name__ == "__main__":
    main()
