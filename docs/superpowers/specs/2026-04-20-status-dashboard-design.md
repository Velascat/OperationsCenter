# Status Dashboard — Design Spec

**Date:** 2026-04-20  
**Status:** Approved

---

## Overview

A live-refresh terminal status dashboard for ControlPlane operators. Reads only local files — no Plane API calls at display time. Supports optional repo filtering.

---

## Components

### 1. Board Snapshot Writer

**What it does:** After each `list_issues()` call in the watcher main loop, serialize a compact board summary to disk atomically.

**Location:** `src/control_plane/entrypoints/worker/main.py` — new function `write_board_snapshot()`, called from the main watcher cycle wherever `list_issues()` results are available.

**Output file:** `logs/local/board_snapshot.json` (transient runtime data, alongside other watcher files).

**Atomic write:** Write to a `.tmp` sibling file, then `os.replace()` into place — guarantees readers never see a torn file regardless of which watcher writes last.

**Schema:**
```json
{
  "updated_at": "2026-04-20T02:14:33Z",
  "written_by": "goal",
  "counts": {
    "ControlPlane": {"Running": 1, "Ready for AI": 2, "Blocked": 0, "Review": 0},
    "ExternalRepo":  {"Running": 0, "Ready for AI": 1, "Blocked": 0, "Review": 0}
  },
  "issues": [
    {
      "id": "034e283e-...",
      "name": "Extract proposal subsystem",
      "state": "Running",
      "repo": "ControlPlane",
      "kind": "goal"
    }
  ]
}
```

`repo` is extracted from the `repo: <key>` label. `kind` from the `task-kind: <kind>` label. Only non-Done/non-Cancelled issues are included.

---

### 2. Status Script

**Location:** `scripts/cp-status.py`

**Invocation:** `python3 scripts/cp-status.py [--repo REPO[,REPO...]]`

**No new dependencies** — stdlib only (`json`, `os`, `sys`, `time`, `pathlib`, `datetime`, `argparse`).

**Data sources (all local reads):**

| Data | Source |
|---|---|
| Watcher state | `logs/local/watch-all/{role}.status.json` |
| Watcher alive | `logs/local/watch-all/{role}.pid` (kill -0 check) |
| Watchdog alive | `logs/local/watch-all/watchdog.pid` |
| Board state | `logs/local/board_snapshot.json` |
| Circuit breaker | `tools/report/control_plane/execution/usage.json` |
| Active kodo task | `/tmp/cp-task-*/goal.md` (first line of Goal section) |
| Memory | `/proc/meminfo` (MemAvailable + SwapFree) |

**Refresh:** 2-second loop. Each iteration: `\033[2J\033[H` (clear screen + home cursor), render, flush. `KeyboardInterrupt` exits cleanly with a final newline.

---

### 3. Shell Wrapper

**Location:** `scripts/control-plane.sh` — new `status` command.

```bash
control-plane.sh status [--repo ControlPlane,ExternalRepo]
```

Passes all args through to `cp-status.py`. Example:

```bash
python3 "${ROOT_DIR}/scripts/cp-status.py" "$@"
```

---

## Display Layout

```
────────── ControlPlane Status  [2026-04-20 02:14:33 UTC]  (Ctrl+C to exit) ──

WATCHERS
  goal     active   cycle=12  task_claimed        034e283e  3m 24s ago
  test     idle     cycle=8   no_task_available   -         12s ago
  improve  idle     cycle=5   no_task_available   -         8s ago
  propose  idle     cycle=2   idle_board          -         41s ago
  review   idle     cycle=6   -                   -         1m 2s ago
  spec     idle     cycle=1   -                   -         2m 1s ago
  watchdog running

ACTIVE KODO
  Extract proposal subsystem — Move all proposal-related…
  repo: ControlPlane   branch: plane/034e283e-…   elapsed: 3m 24s

BOARD  (snapshot 18s old)
  ControlPlane
    Running: 1   Ready for AI: 2   Blocked: 0   Review: 0
  ExternalRepo
    Running: 0   Ready for AI: 1   Blocked: 0   Review: 0

CIRCUIT BREAKER  closed  (1/5 fresh outcomes failed, threshold 80%)
MEMORY           15.2 GB available (RAM + swap)   threshold: 6.0 GB

──────────────────────────────────────────────────────────────────────────────
  Refreshing every 2s
```

---

## Repo Filter (`--repo`)

**Syntax:** `--repo ControlPlane` or `--repo ControlPlane,ExternalRepo`

**Effect per section:**

| Section | Filtered behaviour |
|---|---|
| Watchers | Always show all rows. If the watcher's current `task_id` belongs to a different repo, append `[other repo]` in dim text. |
| Active Kodo | Only show kodo workspaces whose repo label matches the filter. |
| Board | Only show matching repo blocks. |
| CB / Memory | Always shown (global, not repo-scoped). |

---

## Circuit Breaker Display

Read `tools/report/control_plane/execution/usage.json`, filter to `kind == "execution_outcome"` events within the last `_CB_STALENESS_HOURS` (4h default), take last 5. Compute failure rate. Show `open` or `closed` with fraction and threshold.

If fewer than 3 fresh outcomes: show `insufficient data`.

---

## Error Handling

- Missing files: show `unavailable` for that section rather than crashing.
- Stale snapshot (>5 min old): show age in yellow to signal the watcher may be stuck.
- No `/tmp/cp-task-*/`: Active Kodo section shows `none running`.
- `/proc/meminfo` unreadable: Memory line shows `unavailable`.

---

## Files Changed

| File | Change |
|---|---|
| `src/control_plane/entrypoints/worker/main.py` | Add `write_board_snapshot()`, call it from watcher cycle |
| `scripts/cp-status.py` | New file — the dashboard script |
| `scripts/control-plane.sh` | Add `status` command |
| `.gitignore` | Add `logs/local/board_snapshot.json` if not already covered |
