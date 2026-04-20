#!/usr/bin/env python3
"""ControlPlane live status dashboard. Reads local files only — no Plane API calls."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

UTC = timezone.utc
ROOT_DIR = Path(__file__).resolve().parent.parent
WATCH_DIR = ROOT_DIR / "logs" / "local" / "watch-all"
USAGE_JSON = ROOT_DIR / "tools" / "report" / "control_plane" / "execution" / "usage.json"
SNAPSHOT_PATH = WATCH_DIR / "board_snapshot.json"
ROLES = ["goal", "test", "improve", "propose", "review", "spec"]

# CB defaults — mirror usage_store.py constants
_CB_WINDOW = int(os.environ.get("CONTROL_PLANE_CIRCUIT_BREAKER_WINDOW", "5"))
_CB_THRESHOLD = float(os.environ.get("CONTROL_PLANE_CIRCUIT_BREAKER_THRESHOLD", "0.8"))
_CB_STALENESS_HOURS = float(os.environ.get("CONTROL_PLANE_CIRCUIT_BREAKER_STALENESS_HOURS", "4"))

# ANSI
_DIM = "\033[2m"
_RESET = "\033[0m"
_YELLOW = "\033[33m"
_RED = "\033[31m"
_GREEN = "\033[32m"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _elapsed(ts_iso: str | None) -> str:
    if not ts_iso:
        return "?"
    try:
        dt = datetime.fromisoformat(ts_iso)
        secs = int((datetime.now(UTC) - dt).total_seconds())
        if secs < 0:
            secs = 0
        if secs < 60:
            return f"{secs}s ago"
        if secs < 3600:
            return f"{secs // 60}m {secs % 60}s ago"
        return f"{secs // 3600}h {(secs % 3600) // 60}m ago"
    except Exception:
        return "?"


def _elapsed_seconds(ts_iso: str | None) -> float:
    if not ts_iso:
        return 0.0
    try:
        dt = datetime.fromisoformat(ts_iso)
        return max(0.0, (datetime.now(UTC) - dt).total_seconds())
    except Exception:
        return 0.0


def _read_pid(path: Path) -> int | None:
    try:
        return int(path.read_text().strip())
    except Exception:
        return None


def _watcher_rows(repo_filter: list[str] | None) -> list[str]:
    lines = []
    task_repos: dict[str, str] = {}
    if repo_filter:
        snapshot = _read_json(SNAPSHOT_PATH) or {}
        task_repos = {i["id"]: i.get("repo", "") for i in snapshot.get("issues", [])}
    for role in ROLES:
        pid_path = WATCH_DIR / f"{role}.pid"
        status_path = WATCH_DIR / f"{role}.status.json"
        pid = _read_pid(pid_path)
        alive = pid is not None and _pid_alive(pid)
        status = _read_json(status_path) or {}
        state = status.get("state", "-")
        cycle = status.get("cycle", "-")
        last_action = status.get("last_action") or "-"
        task_id = status.get("task_id") or "-"
        updated_at = status.get("updated_at")
        task_short = task_id[:8] if task_id and task_id != "-" else "-"
        age = _elapsed(updated_at)
        alive_marker = "" if alive else f" {_RED}[dead]{_RESET}"

        dim = ""
        other_repo_tag = ""
        if repo_filter and task_id and task_id != "-":
            task_repo = task_repos.get(task_id, "")
            if task_repo and task_repo not in repo_filter:
                dim = _DIM
                other_repo_tag = f" {_DIM}[other repo]{_RESET}"

        lines.append(
            f"  {dim}{role:<8} {state:<8} cycle={cycle:<4} {last_action:<28} {task_short}  {age}{other_repo_tag}{alive_marker}{_RESET}"
        )

    # Watchdog
    wd_pid_path = WATCH_DIR / "watchdog.pid"
    wd_pid = _read_pid(wd_pid_path)
    wd_alive = wd_pid is not None and _pid_alive(wd_pid)
    wd_status = "running" if wd_alive else f"{_RED}stopped{_RESET}"
    lines.append(f"  watchdog {wd_status}")
    return lines


def _kodo_rows(repo_filter: list[str] | None) -> list[str]:
    lines = []
    try:
        workspaces = list(Path("/tmp").glob("cp-task-*"))
    except Exception:
        return ["  unavailable"]
    if not workspaces:
        return ["  none running"]
    for ws in sorted(workspaces):
        goal_file = ws / "goal.md"
        repo_link = ws / "repo"
        # Parse goal title from goal.md
        title = "-"
        try:
            text = goal_file.read_text()
            for line in text.splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    title = line[:72] + ("…" if len(line) > 72 else "")
                    break
        except Exception:
            pass
        # Branch from repo/.git/HEAD
        branch = "-"
        try:
            head = (repo_link / ".git" / "HEAD").read_text().strip()
            if head.startswith("ref: refs/heads/"):
                branch = head[len("ref: refs/heads/"):][:48]
        except Exception:
            pass
        # Repo from snapshot (first Running issue)
        repo = "-"
        try:
            snapshot = _read_json(SNAPSHOT_PATH) or {}
            for issue in snapshot.get("issues", []):
                if issue.get("state") == "Running":
                    repo = issue.get("repo", "-")
                    break
        except Exception:
            pass
        # Elapsed from workspace mtime
        try:
            mtime = os.path.getmtime(goal_file)
            secs = int(time.time() - mtime)
            elapsed = f"{secs // 60}m {secs % 60}s" if secs >= 60 else f"{secs}s"
        except Exception:
            elapsed = "?"

        # Apply filter
        if repo_filter and repo != "-" and repo not in repo_filter:
            continue

        lines.append(f"  {title}")
        lines.append(f"  repo: {repo}   branch: {branch}   elapsed: {elapsed}")
    if not lines:
        return ["  none running (filtered)"]
    return lines


def _board_rows(repo_filter: list[str] | None) -> list[str]:
    snapshot = _read_json(SNAPSHOT_PATH)
    if snapshot is None:
        return ["  unavailable"]
    updated_at = snapshot.get("updated_at")
    age_secs = int(_elapsed_seconds(updated_at))
    age_str = f"{age_secs}s old"
    if age_secs > 300:
        age_str = f"{_YELLOW}{age_str}{_RESET}"
    header = f"  (snapshot {age_str})"
    counts = snapshot.get("counts", {})
    repos = sorted(counts.keys())
    if repo_filter:
        repos = [r for r in repos if r in repo_filter]
    if not repos:
        return [header, "  no data"]
    lines = [header]
    for repo in repos:
        c = counts[repo]
        lines.append(f"  {repo}")
        lines.append(
            f"    Running: {c.get('Running', 0)}   "
            f"Ready for AI: {c.get('Ready for AI', 0)}   "
            f"Blocked: {c.get('Blocked', 0)}   "
            f"Review: {c.get('Review', 0)}"
        )
    return lines


def _cb_row() -> str:
    data = _read_json(USAGE_JSON)
    if data is None:
        return "  unavailable"
    events = data.get("events", [])
    now = datetime.now(UTC)
    stale_cutoff = now - timedelta(hours=_CB_STALENESS_HOURS)
    try:
        outcomes = [
            e for e in reversed(events)
            if e.get("kind") == "execution_outcome"
            and datetime.fromisoformat(e["timestamp"]) > stale_cutoff
        ][:_CB_WINDOW]
    except Exception:
        return "  unavailable"
    if len(outcomes) < 3:
        return f"  insufficient data  ({len(outcomes)} fresh outcomes, need 3)"
    failures = sum(1 for e in outcomes if not e.get("succeeded"))
    rate = failures / len(outcomes)
    is_open = rate >= _CB_THRESHOLD
    status = f"{_RED}OPEN{_RESET}" if is_open else f"{_GREEN}closed{_RESET}"
    return f"  {status}  ({failures}/{len(outcomes)} fresh failed, threshold {int(_CB_THRESHOLD * 100)}%)"


def _memory_row() -> str:
    try:
        avail_kb = 0
        swap_kb = 0
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                avail_kb = int(line.split()[1])
            elif line.startswith("SwapFree:"):
                swap_kb = int(line.split()[1])
        total_mb = (avail_kb + swap_kb) // 1024
        total_gb = total_mb / 1024
        threshold_mb = int(os.environ.get("CONTROL_PLANE_MIN_KODO_AVAILABLE_MB", "6144"))
        threshold_gb = threshold_mb / 1024
        color = _RED if total_mb < threshold_mb else ""
        reset = _RESET if color else ""
        return f"  {color}{total_gb:.1f} GB available (RAM + swap){reset}   threshold: {threshold_gb:.1f} GB"
    except Exception:
        return "  unavailable"


def _render(repo_filter: list[str] | None, width: int = 78) -> str:
    now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    header = f"────────── ControlPlane Status  [{now_str}]  (Ctrl+C to exit) "
    header = header.ljust(width - 1, "─")
    divider = "─" * (width - 1)

    sections = [header, ""]

    sections.append("WATCHERS")
    sections.extend(_watcher_rows(repo_filter))

    sections.append("")
    sections.append("ACTIVE KODO")
    sections.extend(_kodo_rows(repo_filter))

    sections.append("")
    sections.append("BOARD")
    sections.extend(_board_rows(repo_filter))

    sections.append("")
    sections.append("CIRCUIT BREAKER  " + _cb_row().lstrip())
    sections.append("MEMORY           " + _memory_row().lstrip())

    sections.append("")
    sections.append(divider)
    sections.append("  Refreshing every 2s")

    return "\n".join(sections)


def main() -> None:
    parser = argparse.ArgumentParser(description="ControlPlane live status dashboard")
    parser.add_argument(
        "--repo",
        default="",
        help="Comma-separated repo keys to filter (e.g. ControlPlane,ExternalRepo). Empty = show all.",
    )
    args = parser.parse_args()
    repo_filter: list[str] | None = None
    if args.repo.strip():
        repo_filter = [r.strip() for r in args.repo.split(",") if r.strip()]

    try:
        while True:
            cols, rows = shutil.get_terminal_size(fallback=(78, 24))
            output = _render(repo_filter, width=cols)
            lines = output.split("\n")
            # Truncate to terminal height so content never overflows into scrollback
            if len(lines) > rows - 1:
                lines = lines[:rows - 1]
            # Overwrite in place using direct row addressing — never writes \n so
            # the terminal cannot scroll, avoiding scrollback growth.
            # \033[?7l disables auto-wrap so long lines clip rather than wrap.
            sys.stdout.write("\033[?7l")
            for i, line in enumerate(lines, 1):
                sys.stdout.write(f"\033[{i};1H{line}\033[K")
            sys.stdout.write(f"\033[{len(lines) + 1};1H\033[J\033[?7h")
            sys.stdout.flush()
            time.sleep(2)
    except KeyboardInterrupt:
        sys.stdout.write("\033[J\033[?7h\nStopped.\n")


if __name__ == "__main__":
    main()
