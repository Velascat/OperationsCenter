# OperationsCenter Platform Watchdog Loop Runbook

This runbook describes the **OC/Platform watchdog loop** — a self-paced hourly
audit-and-fix cycle driven by Claude Code's `/loop` skill and `ScheduleWakeup`.
It is **not** the internal `watchdog` role that revives watcher processes; that
is `scripts/operations-center.sh watchdog`. This loop is a higher-level operator
loop that runs audits, triages findings, dispatches fixes, and enforces invariants
across the whole OC/Platform stack.

The loop is session-bound: it runs as long as the Claude Code session is open.
It uses `ScheduleWakeup`, not cron/systemd/daemon behavior. Do not replace it
with a system scheduler.

---

## Prerequisites

Before starting the loop, confirm:

1. **Plane up** — `plane-app-*` containers healthy at `http://localhost:8080`
2. **WorkStation SwitchBoard up**:
   ```bash
   cd /home/dev/Documents/GitHub/WorkStation
   docker compose -f compose/docker-compose.yml -f compose/profiles/core.yml up -d
   docker ps --filter name=workstation-switchboard --format '{{.Status}}'
   # expect: Up ... (healthy)
   ```
3. **OC watchers running** (or being intentionally started):
   ```bash
   scripts/operations-center.sh watch-all
   scripts/operations-center.sh watch-all-status   # all 8 must show running
   ```
4. **Runtime model is low-cost** (sonnet/haiku, not opus):
   - `config/operations_center.local.yaml` → `kodo.orchestrator: claude-code:sonnet`
   - `config/runtime_binding_policy.yaml` → refactor + feature rules → `model: sonnet`
5. **kodo max_concurrent = 1** — verify in `config/operations_center.local.yaml`

---

## Preflight checklist

Copy-paste this block at the start of each session before invoking `/loop`:

```bash
# 1. Repo root
[[ "$(pwd)" == "/home/dev/Documents/GitHub/OperationsCenter" ]] \
  && echo "✓ repo root" || echo "✗ wrong dir: $(pwd)"

# 2. Env file
source .env.operations-center.local && echo "✓ env sourced"

# 3. CLIs present
ls .venv/bin/operations-center-custodian-sweep >/dev/null 2>&1 \
  && echo "✓ CLIs present" || echo "✗ .venv missing — run: scripts/operations-center.sh setup"

# 4. Plane reachable
curl -sf http://localhost:8080/api/health/ >/dev/null \
  && echo "✓ Plane up" || echo "✗ Plane unreachable"

# 5. WorkStation / SwitchBoard up
curl -sf http://localhost:20401/health | python3 -c \
  "import sys,json; d=json.load(sys.stdin); print('✓ SwitchBoard', d['status'])" \
  2>/dev/null || echo "✗ SwitchBoard unreachable"

# 6. Watchers running
scripts/operations-center.sh watch-all-status 2>/dev/null | grep -c "running" | \
  xargs -I{} bash -c '[[ {} -eq 8 ]] && echo "✓ 8 watchers running" || echo "✗ only {} watchers running"'

# 7. No live loop lock from another session
scripts/operations-center.sh watchdog-loop-status

# 8. Working tree state
git status --short | head -10

# 9. Runtime model
grep "orchestrator:" config/operations_center.local.yaml
grep "model:" config/runtime_binding_policy.yaml | head -5

# 10. kodo concurrency
python3 -c "import yaml; d=yaml.safe_load(open('config/operations_center.local.yaml')); \
  mc=d.get('backend_caps',{}).get('kodo',{}).get('max_concurrent'); \
  print('✓ kodo max_concurrent=1' if mc==1 else f'✗ kodo max_concurrent={mc}')"
```

---

## Loop ownership lock

To prevent two Claude Code sessions from running the platform loop simultaneously,
acquire the lock before the first cycle and release it on clean stop.

```bash
# Acquire (fails fast if another live process holds the lock)
scripts/operations-center.sh watchdog-loop-acquire

# Check status
scripts/operations-center.sh watchdog-loop-status

# Release on clean exit / session end
scripts/operations-center.sh watchdog-loop-release
```

Lock file: `logs/local/watchdog_loop.lock`
Payload: `{ pid, timestamp, hostname, repo_root, purpose }`

**Semantics:**
- If the lock exists and the recorded PID is alive → abort (another session owns it)
- If the lock exists and the PID is dead → reclaim as stale, continue
- If no lock → acquire and continue

All mutating actions (autonomy-cycle, watcher restarts, `.console/*` updates)
must only run when this loop owns the lock.

---

## Starting the loop

Invoke in the Claude Code session:

```
/loop Every hour: run the OC/Platform audit-and-fix cycle from /home/dev/Documents/GitHub/OperationsCenter. Source .env.operations-center.local first. Use .venv/bin/ for all CLIs. This loop is session-bound and uses ScheduleWakeup, not cron/systemd.

STEP 0 — OWNERSHIP + PREFLIGHT:
Acquire/verify logs/local/watchdog_loop.lock via:
  scripts/operations-center.sh watchdog-loop-acquire
If another live owner exists, abort. If stale, reclaim.
Then confirm: Plane at http://localhost:8080, WorkStation/SwitchBoard at http://localhost:20401/health, all 8 OC watchers running, .venv CLIs present, runtime low-cost policy (sonnet/haiku), kodo max_concurrent=1 in config, working tree state via git status.

STEP 1 — INVESTIGATE (run in parallel where safe):
  .venv/bin/operations-center-custodian-sweep --config config/operations_center.local.yaml --emit
  .venv/bin/operations-center-ghost-audit     --config config/operations_center.local.yaml --since 1h
  .venv/bin/operations-center-flow-audit      --config config/operations_center.local.yaml
  .venv/bin/operations-center-graph-doctor
  .venv/bin/operations-center-reaudit-check   --json
  .venv/bin/operations-center-check-regressions --config config/operations_center.local.yaml --lookback-hours 1 --dry-run
Collect exit codes and finding counts. Determine affected repos only from tool output — not from vibes or unrelated logs. If an affected repo cannot be determined confidently, create a Plane task and skip direct execution for that finding.

STEP 2 — TRIAGE:
  .venv/bin/operations-center-triage-scan --config config/operations_center.local.yaml --apply

STEP 3 — EXECUTION GATE:
For each finding, decide: Plane task only vs direct fix.
Direct fix is allowed ONLY when ALL of these hold:
  (a) finding reproduced in the current cycle
  (b) scoped to a specific repo determined from tool output
  (c) implementation-level work (not ADR / policy / design-only)
  (d) not blocked on credentials or operator infrastructure
  (e) not requiring destructive cleanup (no git reset --hard, no volume wipes)
  (f) not requiring runtime-policy widening (no max_concurrent increase, no model upgrade)
If any condition fails → create/update Plane task, skip direct fix.

STEP 4 — DIRECT FIXES (only if loop owns the lock):
For each affected repo that passes the execution gate, run one at a time:
  scripts/operations-center.sh autonomy-cycle --config config/operations_center.local.yaml --execute --repo <path>
Respect kodo max_concurrent=1 — do not dispatch two repos simultaneously.

STEP 5 — INVARIANT ENFORCEMENT:
  .venv/bin/pytest tests/unit/er000_phase0_golden/ -q --tb=short
Also run targeted tests for any repo touched in STEP 4.
If any test fails and cannot be fixed safely in this cycle → create/update Plane task.

STEP 6 — WATCHER HEALTH + RESTART INVESTIGATION:
  scripts/operations-center.sh watch-all-status
  grep -h "watcher_restart\|exit_code\|ERROR\|Traceback" logs/local/watch-all/*.log | tail -50
Classify restarts:
  - exit 143 (SIGTERM): benign deliberate stop/restart — note only
  - exit 1/2: crash — read log context, find root cause, fix config/code or open Plane task
  - exit 0: unexpected clean exit — read last 30 lines of that watcher log
Anti-flap rule: if the same watcher crashes unexpectedly (non-143) in TWO consecutive cycles,
do NOT blindly restart it — escalate with a Plane task instead. Do not pretend the platform
is healthy if a watcher cannot be restarted cleanly.
Restart a stopped watcher (only after root-cause is understood):
  scripts/operations-center.sh watch --role <role>

STEP 7 — LOG + COMMIT HYGIENE:
Append the structured cycle summary (see template below) to .console/log.md.
Update .console/backlog.md for any new/closed gaps.
Commit only after validation passes. Do not commit to main unless the operator has
explicitly allowed it for the current task/session. If not allowed, create a branch:
  git checkout -b oc-watchdog/<YYYYMMDD-HHMM>-<short-topic>
One logical commit per repo per cycle. Commit message must name: root cause, affected repo,
gate/check fixed. Never force-push, amend old loop commits, or commit generated noise.

STEP 8 — SCHEDULEWAKEUP:
Schedule next wake ~1 hour after this cycle completes. Pass this full /loop prompt verbatim.
```

---

## Execution gate — criteria reference

| Criterion | Direct fix allowed | Plane task only |
|-----------|-------------------|-----------------|
| Reproduced in current cycle | ✓ | ✗ |
| Affected repo known from tool output | ✓ | ✗ |
| Implementation-level work | ✓ | ✗ |
| ADR / policy / design-only | ✗ | ✓ |
| Blocked on credentials / infra | ✗ | ✓ |
| Requires destructive cleanup | ✗ | ✓ |
| Requires concurrency/model widening | ✗ | ✓ |

---

## Affected-repo discovery — deterministic sources

Affected repos come **only** from:

1. `operations-center-custodian-sweep --emit` — repo key in emitted Plane task metadata
2. `operations-center-ghost-audit --since 1h` — `repo` field in JSON output
3. `operations-center-flow-audit` — `repo` field in JSON output
4. `operations-center-graph-doctor` — manifest graph shows which repo's invariant failed
5. `operations-center-reaudit-check --json` — `backends[].needed=true` → OperationsCenter
6. `operations-center-check-regressions` — `findings[].repo` field
7. Watcher log root-cause classification — watcher process path identifies the role/repo
8. Plane task metadata after triage promotion — `label_detail` repo key

Do not infer affected repos from unrelated logs, memory, or prior sessions.

---

## Destructive-action guardrails

The loop must **never** do the following without explicit operator approval:

- `git reset --hard` — prefer a revert commit instead
- Broad deletion of untracked files (`git clean -fdx`)
- Deleting Docker volumes
- Deleting or recreating databases
- Unregistering WSL distributions
- Force-pushing (`git push --force`)
- Changing secrets or credentials
- Modifying ADRs (`docs/architecture/adr/`)
- Changing runtime model policy (`config/runtime_binding_policy.yaml`)
- Widening `kodo max_concurrent` beyond 1
- Replacing `ScheduleWakeup` with cron/systemd/daemon behavior

**Regression revert procedure** (when `check-regressions` confirms a regression):
1. Identify the exact branch/commit range from the tool output
2. State the evidence: which test failed, which commit introduced it
3. Create a normal revert commit: `git revert <sha> --no-edit`
4. Run validation before pushing
5. If operator approval is needed (e.g., force-push, destructive reset) → Plane task

---

## Branch and commit hygiene

**Default posture:** do not commit to `main` unless the operator explicitly allows it
for the current session/task.

If not allowed and currently on `main`:
```bash
git checkout -b oc-watchdog/$(date +%Y%m%d-%H%M)-<short-topic>
```

**Per cycle:**
- One logical commit per repo per cycle
- Commit only after validation passes (`pytest`, `ruff`, repo's own validation commands)
- Commit message format:
  ```
  fix(<repo>): <root cause> — <invariant/gate fixed>
  ```
- Never force-push
- Never amend previous loop commits automatically
- Do not commit generated reports, audit JSON outputs, or log files unless intentionally tracked

---

## Watcher restart classification and anti-flap

| Exit code | Classification | Action |
|-----------|---------------|--------|
| 143 (SIGTERM) | Benign deliberate stop | Note in cycle summary only |
| 1 or 2 | Crash | Read log context; fix config/code or open Plane task; restart once |
| 0 | Unexpected clean exit | Read last 30 log lines; treat as soft crash |

**Anti-flap rule:** if the same watcher produces a non-143 restart event in **two consecutive
cycles**, do not blindly restart it again. Instead:
1. Read the full log context for both events
2. Create or update a Plane task with root cause and log excerpts
3. Leave the watcher stopped until the Plane task is resolved
4. Report in the cycle summary that this role is intentionally paused

Track per-role crash events in the cycle summary. If `.console/log.md` shows two consecutive
`watcher_restart` entries for the same role, the anti-flap rule applies.

---

## Structured cycle summary

Append one block per completed cycle to `.console/log.md`:

```markdown
## OC Platform Watchdog Cycle — <YYYY-MM-DD HH:MM>

- Lock owner: pid=<pid> hostname=<host>
- Branch / commit: <branch> @ <short-sha>
- Plane status: <N> Ready-for-AI / <N> Running / <N> In-Review
- WorkStation / SwitchBoard status: <healthy|unreachable>
- Watchers: <N>/8 running | restarts this cycle: <role=exit_code,...>
- Audits run: custodian-sweep ghost-audit flow-audit graph-doctor reaudit-check regressions
- Findings reproduced this cycle: <tool=N,...> or "none"
- Plane tasks opened/updated: <N> (<task IDs or "none">)
- Direct fixes dispatched: <repo=description,...> or "none"
- Repos touched: <list or "none">
- Validation run: pytest er000_phase0_golden (<N> passed) | <other>
- Graph status: <nodes>/<edges> graph_built=<True|False>
- Regressions checked: <N findings or "none">
- Watcher restarts / crash classifications: <role=exit_code:classification,...> or "none"
- Follow-ups: <Plane task IDs or "none">
```

---

## What each cycle does

| Step | Tools / commands | Action on findings |
|------|-----------------|-------------------|
| Ownership | `watchdog-loop-acquire` | Abort if live lock held by another PID |
| Preflight | curl, git status, grep | Confirm all services + config up front |
| Investigate | 6 audit CLIs in parallel | Collect findings; classify affected repos |
| Triage | `triage-scan --apply` | Promote Backlog → Ready for AI |
| Execution gate | Manual criteria check | Gate direct fixes; route rest to Plane |
| Direct fixes | `autonomy-cycle --execute` per repo | One repo at a time; kodo max_concurrent=1 |
| Invariants | `pytest er000_phase0_golden` + targeted | Plane task on failure |
| Watcher health | `watch-all-status` + log grep | Anti-flap classification; restart if safe |
| Log | `.console/log.md` structured block | One summary per cycle |
| Commit | `git commit` after validation | Per-repo; branch hygiene respected |
| Schedule | `ScheduleWakeup ~3600s` | Pass full `/loop` prompt for next cycle |

---

## Custodian enforcement

The following invariants are enforced by Custodian detectors on every sweep:

- **OC10** (`kodo max_concurrent must be 1`) — reads
  `config/operations_center.local.yaml` and fails if `backend_caps.kodo.max_concurrent != 1`.
  Silently passes on fresh clones (local config absent).

Additional invariants maintained by runbook convention (not currently code-enforced):
- No cron/systemd/daemon replacement for this loop
- No ADR modification from watchdog/autonomy loop paths
- No destructive git operations in loop helpers
- No runtime model policy widening from loop actions

---

## Stopping the loop

The loop stops when you close the Claude Code session, or tell Claude to stop.
To stop explicitly, tell Claude: "stop the loop" — it will omit the next
`ScheduleWakeup` call and the loop ends naturally.

Before stopping, release the lock:
```bash
scripts/operations-center.sh watchdog-loop-release
```

---

## Cadence

Wakes ~1h after each cycle completes (based on completion time, not wall-clock).
Typical runtime per clean-stack cycle: ~7 minutes. The `ScheduleWakeup` delay is
set to 3600s, so the next fire is ~1h after the previous cycle started.
