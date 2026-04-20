# Status Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a live-refresh terminal status dashboard (`control-plane.sh status`) that reads only local files, and a board snapshot writer that watchers write atomically after each `list_issues()` call.

**Architecture:** The watcher cycle gains a `write_board_snapshot()` call (try/except, non-blocking) that atomically writes `logs/local/watch-all/board_snapshot.json`. A standalone `scripts/cp-status.py` reads that file plus existing local files (watcher status JSONs, PID files, usage.json, /proc/meminfo, /tmp/cp-task-*) and renders a clear-screen 2s refresh loop. `control-plane.sh status` is a thin wrapper.

**Tech Stack:** Python 3.12 stdlib only (`json`, `os`, `sys`, `time`, `pathlib`, `datetime`, `argparse`). Existing `issue_status_name`, `issue_label_names`, `issue_task_kind` helpers in `main.py`.

---

## File Map

| File | Change |
|---|---|
| `src/control_plane/entrypoints/worker/main.py` | Add `write_board_snapshot()` + call it in `run_watch_loop` |
| `scripts/cp-status.py` | New — the dashboard script |
| `scripts/control-plane.sh` | Add `status` command |
| `tests/test_worker_entrypoint.py` | Add tests for `write_board_snapshot()` |

---

## Task 1: Add `write_board_snapshot()` to `main.py`

**Files:**
- Modify: `src/control_plane/entrypoints/worker/main.py` (after `write_watch_status` at line ~650)
- Test: `tests/test_worker_entrypoint.py`

- [ ] **Step 1: Write the failing tests**

Find the end of the existing `write_watch_status` tests section in `tests/test_worker_entrypoint.py`. Add:

```python
def test_write_board_snapshot_creates_file() -> None:
    """write_board_snapshot writes a valid JSON snapshot atomically."""
    import tempfile
    status_dir = Path(tempfile.mkdtemp())
    issues = [
        {
            "id": "aaa",
            "name": "Fix login bug",
            "state_detail": {"name": "Running"},
            "labels": ["repo: ControlPlane", "task-kind: goal"],
        },
        {
            "id": "bbb",
            "name": "Update deps",
            "state_detail": {"name": "Ready for AI"},
            "labels": ["repo: ExternalRepo", "task-kind: improve"],
        },
        {
            "id": "ccc",
            "name": "Old task",
            "state_detail": {"name": "Done"},
            "labels": ["repo: ControlPlane"],
        },
    ]
    write_board_snapshot(issues, role="goal", status_dir=status_dir)
    snapshot_path = status_dir / "board_snapshot.json"
    assert snapshot_path.exists()
    data = json.loads(snapshot_path.read_text())
    assert data["written_by"] == "goal"
    assert "updated_at" in data
    assert data["counts"]["ControlPlane"]["Running"] == 1
    assert data["counts"]["ExternalRepo"]["Ready for AI"] == 1
    # Done issues excluded
    active_ids = {i["id"] for i in data["issues"]}
    assert "ccc" not in active_ids
    assert "aaa" in active_ids
    assert "bbb" in active_ids


def test_write_board_snapshot_no_op_when_status_dir_none() -> None:
    """write_board_snapshot is a no-op when status_dir is None."""
    # Should not raise
    write_board_snapshot([], role="goal", status_dir=None)


def test_write_board_snapshot_atomic_replace() -> None:
    """Snapshot file is never left in a torn state (tmp file cleaned up)."""
    import tempfile
    status_dir = Path(tempfile.mkdtemp())
    write_board_snapshot([], role="test", status_dir=status_dir)
    tmp = status_dir / "board_snapshot.json.tmp"
    assert not tmp.exists()
    assert (status_dir / "board_snapshot.json").exists()
```

- [ ] **Step 2: Run to confirm failure**

```bash
.venv/bin/pytest tests/test_worker_entrypoint.py::test_write_board_snapshot_creates_file -v
```

Expected: `FAILED` with `ImportError` or `NameError: write_board_snapshot`.

- [ ] **Step 3: Implement `write_board_snapshot()` in `main.py`**

Add this function immediately after `write_watch_status` (around line 651):

```python
_BOARD_SNAPSHOT_ACTIVE_STATES = {"Running", "Ready for AI", "Blocked", "Review"}


def write_board_snapshot(
    issues: list[dict[str, Any]],
    *,
    role: str,
    status_dir: Path | None,
) -> None:
    if status_dir is None:
        return
    snapshot_path = status_dir / "board_snapshot.json"
    tmp_path = status_dir / "board_snapshot.json.tmp"
    counts: dict[str, dict[str, int]] = {}
    active_issues: list[dict[str, Any]] = []
    for issue in issues:
        state = issue_status_name(issue)
        if state not in _BOARD_SNAPSHOT_ACTIVE_STATES:
            continue
        repo = "unknown"
        kind = ""
        for lbl in issue_label_names(issue):
            if lbl.lower().startswith("repo:"):
                repo = lbl.split(":", 1)[1].strip()
            elif lbl.lower().startswith("task-kind:"):
                kind = lbl.split(":", 1)[1].strip()
        if repo not in counts:
            counts[repo] = {"Running": 0, "Ready for AI": 0, "Blocked": 0, "Review": 0}
        if state in counts[repo]:
            counts[repo][state] += 1
        active_issues.append({
            "id": str(issue.get("id", "")),
            "name": str(issue.get("name", "")),
            "state": state,
            "repo": repo,
            "kind": kind,
        })
    payload = {
        "updated_at": datetime.now(UTC).isoformat(),
        "written_by": role,
        "counts": counts,
        "issues": active_issues,
    }
    tmp_path.write_text(json.dumps(payload, indent=2))
    os.replace(tmp_path, snapshot_path)
```

- [ ] **Step 4: Run the three tests**

```bash
.venv/bin/pytest tests/test_worker_entrypoint.py::test_write_board_snapshot_creates_file tests/test_worker_entrypoint.py::test_write_board_snapshot_no_op_when_status_dir_none tests/test_worker_entrypoint.py::test_write_board_snapshot_atomic_replace -v
```

Expected: all 3 `PASSED`.

- [ ] **Step 5: Call `write_board_snapshot()` in `run_watch_loop`**

In `run_watch_loop`, find the heartbeat write block (around line 7158):

```python
        if status_dir is not None and _is_primary_slot:
            write_heartbeat(status_dir, role, now=datetime.now(UTC))
```

Immediately after it, add:

```python
        if _is_primary_slot:
            try:
                _snap_issues = client.list_issues()
                write_board_snapshot(_snap_issues, role=role, status_dir=status_dir)
            except Exception:
                pass
```

- [ ] **Step 6: Run the full test suite to confirm no regressions**

```bash
.venv/bin/pytest -q
```

Expected: all existing tests pass plus the 3 new ones.

- [ ] **Step 7: Commit**

```bash
git add src/control_plane/entrypoints/worker/main.py tests/test_worker_entrypoint.py
git commit -m "feat(status): write board snapshot atomically after each watcher cycle

Adds write_board_snapshot() which serializes active Plane issues to
logs/local/watch-all/board_snapshot.json via atomic os.replace(). Called
from run_watch_loop on the primary slot each cycle; errors are swallowed
so snapshot failures never affect the watcher main path."
```

---

## Task 2: Create `scripts/cp-status.py`

**Files:**
- Create: `scripts/cp-status.py`

- [ ] **Step 1: Create the file with all helpers**

```python
#!/usr/bin/env python3
"""ControlPlane live status dashboard. Reads local files only — no Plane API calls."""
from __future__ import annotations

import argparse
import json
import os
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

        # Repo filter: dim rows where current task belongs to another repo
        dim = ""
        other_repo_tag = ""
        if repo_filter and task_id and task_id != "-":
            # Check snapshot for which repo this task belongs to
            snapshot = _read_json(SNAPSHOT_PATH) or {}
            task_repos = {i["id"]: i.get("repo", "") for i in snapshot.get("issues", [])}
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
        # Repo from branch name or snapshot
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
        # Read threshold from env (mirrors main.py default of 400MB, local config sets 6144)
        threshold_mb = int(os.environ.get("CONTROL_PLANE_MIN_KODO_AVAILABLE_MB", "6144"))
        threshold_gb = threshold_mb / 1024
        color = _RED if total_mb < threshold_mb else ""
        return f"  {color}{total_gb:.1f} GB available (RAM + swap){_RESET}   threshold: {threshold_gb:.1f} GB"
    except Exception:
        return "  unavailable"


def _render(repo_filter: list[str] | None) -> str:
    now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    width = 78
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
            output = _render(repo_filter)
            sys.stdout.write("\033[2J\033[H")
            sys.stdout.write(output + "\n")
            sys.stdout.flush()
            time.sleep(2)
    except KeyboardInterrupt:
        sys.stdout.write("\nStopped.\n")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Make it executable**

```bash
chmod +x scripts/cp-status.py
```

- [ ] **Step 3: Smoke-test it manually**

```bash
python3 scripts/cp-status.py
```

Expected: dashboard renders, refreshes every 2s. Ctrl+C exits cleanly with "Stopped."

- [ ] **Step 4: Test with repo filter**

```bash
python3 scripts/cp-status.py --repo ControlPlane
```

Expected: Board shows only ControlPlane block; watcher rows for tasks in other repos show `[other repo]` dimmed.

- [ ] **Step 5: Commit**

```bash
git add scripts/cp-status.py
git commit -m "feat(status): add cp-status.py live dashboard script

Reads watcher status files, board snapshot, CB outcomes, /proc/meminfo,
and /tmp/cp-task-* workspaces. 2s clear-screen refresh. Supports
--repo filter: board and active kodo filtered, watchers dimmed."
```

---

## Task 3: Wire `control-plane.sh status`

**Files:**
- Modify: `scripts/control-plane.sh`

- [ ] **Step 1: Add `status` to the help text**

Find the `usage()` function in `control-plane.sh` (around line 100). Add a `status` entry alongside other commands:

```bash
  status [--repo KEY,KEY]   Live status dashboard (Ctrl+C to exit)
```

- [ ] **Step 2: Add the `status` case**

Find the main `case "$1"` dispatch block. Add a `status)` case before the final `*)` fallthrough. A good location is after `watch-all-status)`:

```bash
  status)
    ensure_venv
    load_env_file
    python3 "${ROOT_DIR}/scripts/cp-status.py" "${@:2}"
    ;;
```

- [ ] **Step 3: Smoke-test the wrapper**

```bash
bash scripts/control-plane.sh status
```

Expected: same dashboard as direct `python3 scripts/cp-status.py`.

```bash
bash scripts/control-plane.sh status --repo ControlPlane
```

Expected: filtered view.

- [ ] **Step 4: Commit**

```bash
git add scripts/control-plane.sh
git commit -m "feat(status): add control-plane.sh status command

Thin wrapper around cp-status.py. Passes --repo filter args through.
Usage: control-plane.sh status [--repo ControlPlane,ExternalRepo]"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Covered by |
|---|---|
| Board snapshot writer, atomic os.replace | Task 1 Step 3 |
| Snapshot in `logs/local/watch-all/board_snapshot.json` | Task 1 Step 3 (`status_dir / "board_snapshot.json"`) |
| Written by any watcher, whichever runs last wins | Task 1 Step 5 (all watchers call it) |
| `scripts/cp-status.py`, stdlib only | Task 2 Step 1 |
| Watcher section: role, state, cycle, last_action, task_id, elapsed | Task 2 `_watcher_rows()` |
| Active Kodo: title, repo, branch, elapsed | Task 2 `_kodo_rows()` |
| Board section: per-repo state counts, snapshot age | Task 2 `_board_rows()` |
| CB section: open/closed, failure rate, threshold | Task 2 `_cb_row()` |
| Memory section: RAM+swap vs threshold | Task 2 `_memory_row()` |
| Stale snapshot warning (>5 min) | Task 2 `_board_rows()` — yellow when age_secs > 300 |
| 2s refresh, Ctrl+C exits | Task 2 `main()` |
| `--repo` filter: Board + Kodo filtered, watchers dimmed | Task 2 all three section functions |
| `control-plane.sh status [--repo]` | Task 3 |
| Tests for `write_board_snapshot` | Task 1 Steps 1-4 |

**Placeholder scan:** None found.

**Type consistency:** `write_board_snapshot(issues: list[dict], role: str, status_dir: Path | None)` used consistently in Task 1 Steps 1, 3, and 5.

**Min kodo threshold:** `cp-status.py` reads `CONTROL_PLANE_MIN_KODO_AVAILABLE_MB` env var (default 6144, matching the local config). The local config sets `min_kodo_available_mb: 6144` but this is read by the Python settings loader, not directly by the script — the env var approach is consistent with how other thresholds (CB window, threshold) are read.
