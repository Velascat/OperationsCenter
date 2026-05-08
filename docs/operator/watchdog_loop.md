# OperationsCenter Platform Watchdog Loop Runbook

This runbook describes the **OC/Platform watchdog loop** — a self-paced
audit-and-stabilization cycle driven by Claude Code's `/loop` skill and
`ScheduleWakeup`. It is **not** the internal `watchdog` role that revives
watcher processes; that is `scripts/operations-center.sh watchdog`. This loop
is a higher-level operator loop that actively investigates blocked work,
unblocks stalled queues, escalates repeated failures, and enforces invariants
across the whole OC/Platform stack.

The loop is **not merely an hourly audit runner**. When the platform is
unhealthy it shortens its cadence and actively works to restore forward
progress. When healthy it backs off to maintenance frequency.

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
/loop Run the OC/Platform stabilization and audit cycle from /home/dev/Documents/GitHub/OperationsCenter. Source .env.operations-center.local first. Use .venv/bin/ for all CLIs. This loop is session-bound and uses ScheduleWakeup, not cron/systemd.

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

STEP 3 — BLOCKED/STALLED WORK INVESTIGATION:
Read the last 3 cycle summaries from .console/log.md to identify repeated patterns.

STARVATION — classify immediately (single cycle is sufficient) when ANY of these hold:
  - Propose repeatedly emits candidates that are skipped because duplicates exist in Blocked
  - tasks_created=0 or None across consecutive propose runs while Blocked count is nonzero
  - Ready-for-AI never drains despite work existing on the board
  - Blocked tasks with self-modify:approved never transition state
  - Same remediation candidates generated repeatedly with zero queue movement
Do NOT require multiple cycles of evidence when a closed retry/no-progress loop is already demonstrated.
Do NOT classify demonstrated starvation as "potential starvation" or "monitoring for recurrence."

CLOSED-LOOP STAGNATION — classify and escalate immediately when:
  - The platform repeatedly generates or retries equivalent remediation while queue/task/execution state does not materially change
  - Duplicate suppression is causing a deadlock (propose skips because Blocked duplicates exist; workers never consume Blocked; no one re-queues them)
  - Ready-for-AI count stays at zero while blocked count stays nonzero across a cycle
  - Autonomy-cycle retries produce no net task state change

Then actively investigate all of:
  (a) Plane tasks stuck in Ready-for-AI for >2 cycles without being claimed
  (b) Tasks in Blocked with self-modify:approved that should be Ready-for-AI
  (c) Same findings repeating from STEP 1 across consecutive cycles
  (d) Repos skipped repeatedly by the execution gate
  (e) Same regressions recurring across cycles
  (f) Autonomy-cycle failures in recent cycles
  (g) Flow-audit gaps open across multiple cycles
  (h) Graph invariants remaining broken
  (i) propose tasks_created=0/None while candidates are being emitted
  (j) Duplicate suppression causing queue deadlock (blocked duplicates preventing new task creation)

QUEUE-UNBLOCKING INVESTIGATION — when starvation or closed-loop stagnation is detected:
  - Identify why Blocked tasks remain blocked (failed execution? manual block? phase gate?)
  - Check whether duplicate suppression is the deadlock cause
  - Determine whether tasks should safely move: Blocked→Backlog or Blocked→Ready-for-AI
  - Do not blindly mutate queue state — but investigate the unblock path and escalate immediately

Classify each blocked item:
  - temporarily-blocked: retry next cycle — only valid when forward progress IS occurring elsewhere
  - infra-blocked: platform instability preventing execution
  - ownership-ambiguous: affected repo not determinable
  - validation-blocked: failing tests or regressions
  - structurally-blocked: requires operator or design action
  - crash-looping: same watcher or process failing repeatedly
  - starvation: work exists and remediation is generated, but produces no net forward progress
  - dead-remediation: retries occurring without any state improvement
  - closed-loop stagnation: platform generates activity without measurable queue or execution progress

For starvation, dead-remediation, closed-loop stagnation, or structurally-blocked: create/update Plane task immediately. Do not use "monitor for recurrence" language. Do not retry identical failing remediation paths.

FORWARD PROGRESS CHECK — before classifying as temporarily-blocked, confirm at least one of:
  - Blocked count decreased vs prior cycle
  - Ready-for-AI drained vs prior cycle
  - Task state transitions occurred
  - Regressions resolved
  - Watcher stabilized after crash
  - Autonomy-cycle outcomes improved
If none apply and remediation is actively running, classify as stagnation, not temporary delay.

STEP 4 — EXECUTION GATE:
For each finding, decide: Plane task only vs direct fix.
Direct fix is allowed ONLY when ALL of these hold:
  (a) finding reproduced in the current cycle
  (b) scoped to a specific repo determined from tool output
  (c) implementation-level work (not ADR / policy / design-only)
  (d) not blocked on credentials or operator infrastructure
  (e) not requiring destructive cleanup (no git reset --hard, no volume wipes)
  (f) not requiring runtime-policy widening (no max_concurrent increase, no model upgrade)
  (g) not classified as dead-remediation, starvation, or closed-loop stagnation in STEP 3
If any condition fails → create/update Plane task, skip direct fix.

STEP 5 — DIRECT FIXES (only if loop owns the lock):
For each affected repo that passes the execution gate, run one at a time:
  scripts/operations-center.sh autonomy-cycle --config config/operations_center.local.yaml --execute --repo <path>
Respect kodo max_concurrent=1 — do not dispatch two repos simultaneously.

STEP 6 — INVARIANT ENFORCEMENT:
  .venv/bin/pytest tests/unit/er000_phase0_golden/ -q --tb=short
Also run targeted tests for any repo touched in STEP 5.
If any test fails and cannot be fixed safely in this cycle → create/update Plane task.

STEP 7 — WATCHER HEALTH + RESTART INVESTIGATION:
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

STEP 8 — LOG + COMMIT HYGIENE:
Append the structured cycle summary (see template in runbook) to .console/log.md.
Update .console/backlog.md for any new/closed gaps.
Commit only after validation passes. Do not commit to main unless the operator has
explicitly allowed it for the current task/session. If not allowed, create a branch:
  git checkout -b oc-watchdog/<YYYYMMDD-HHMM>-<short-topic>
One logical commit per repo per cycle. Commit message must name: root cause, affected repo,
gate/check fixed. Never force-push, amend old loop commits, or commit generated noise.

STEP 9 — ADAPTIVE SCHEDULEWAKEUP:
Assess platform health state and choose ScheduleWakeup delay accordingly:

  CRITICAL  — crash loops / graph broken / autonomy failing repeatedly:        180s
  DEGRADED  — watcher crashes (non-143) / blocked queue unchanged / flow gaps: 300s
  STALLED   — starvation active / closed-loop stagnation / no forward progress: 600s
  ACTIVE    — direct fixes dispatched this cycle / remediation in flight:       900s
  HEALTHY   — all audits clean, no starvation signals, all watchers up:        3600s

FORBIDDEN: Do not choose HEALTHY cadence if any of these are true:
  - starvation classified this cycle
  - closed-loop stagnation detected
  - Blocked tasks with self-modify:approved and zero Ready-for-AI
  - propose tasks_created=0/None while candidates emitted
  - blocked count unchanged from prior cycle while remediation ran

Use the WORST health state observed across all steps. Starvation/stagnation signals
force STALLED minimum immediately — single cycle evidence is sufficient.
Log the chosen cadence and the driving signal in the cycle summary.
Pass this full /loop prompt verbatim as the ScheduleWakeup prompt.
```

---

## Adaptive cadence

The loop shortens its wake interval automatically when the platform is unhealthy.
The goal is to stabilize broken states quickly and back off once forward progress
resumes. Do not stay at 1h cadence when the platform is actively broken.

| Platform state | Wake delay | Trigger signals |
|----------------|-----------|-----------------|
| CRITICAL | 180s (~3m) | crash loops; graph broken; autonomy failing repeatedly |
| DEGRADED | 300s (~5m) | non-143 watcher crashes; blocked queue unchanged; open flow gaps |
| STALLED | 600s (~10m) | starvation active; closed-loop stagnation; no forward progress |
| ACTIVE | 900s (~15m) | direct fixes dispatched; remediation in flight |
| HEALTHY | 3600s (~60m) | all clean; no starvation signals; all watchers up |

**Forbidden cadence widening:** HEALTHY cadence is forbidden while any of these signals are active:
- Starvation classified (current or prior cycle unresolved)
- Closed-loop stagnation detected
- `tasks_created=0/None` from propose while candidates were emitted
- Blocked tasks with `self-modify:approved` and zero Ready-for-AI
- Blocked count unchanged from prior cycle while remediation ran

A single demonstrated starvation/stagnation cycle drops cadence to STALLED minimum.
It does not require two consecutive cycles of evidence.

---

## Starvation and closed-loop stagnation

These are distinct from simple "blocked work" and require immediate escalation.

### Starvation

**Definition:** Work exists and remediation candidates are generated, but the pipeline
produces no net forward progress because execution paths are blocked, duplicated,
skipped, or never consumed.

Starvation does NOT require multiple hours or multiple cycles of evidence. A single
cycle is sufficient when the pattern is already closed:

| Signal | Interpretation |
|--------|---------------|
| `propose: skipped=N, created=0` while Blocked has duplicates | Deduplication deadlock — starvation |
| Blocked tasks with `self-modify:approved`, Ready-for-AI=0 | Workers cannot consume — starvation |
| Same candidates emitted and skipped repeatedly | No queue movement — starvation |
| `tasks_created=None` while board has blocked approved work | Propose stuck — starvation |

### Closed-loop stagnation

**Definition:** The platform repeatedly generates or retries equivalent remediation
work while queue state, task state, or execution state does not materially change.

Examples:
- Propose emits duplicates already in Blocked → skips → nothing changes → repeats
- Ready-for-AI never drains despite tasks present
- Same repos skipped every cycle at execution gate
- Same regressions recreated every cycle
- Blocked tasks never re-enter executable state

### Distinction from temporary delay

| Condition | Meaning | Cadence |
|-----------|---------|---------|
| Temporary delay | Work is still progressing | ACTIVE or HEALTHY |
| Starvation | No forward progress despite active work generation | STALLED minimum |
| Dead-remediation | Retries occurring without any state improvement | STALLED minimum |
| Closed-loop stagnation | Activity without measurable progress | STALLED minimum |
| Blocked queue deadlock | Work trapped in non-executable state | DEGRADED |

### Forbidden language

Do not write:
- "potential starvation — monitor for recurrence"
- "flagging for next cycle"
- "will assess again after one more cycle"

once the evidence already demonstrates a closed retry/no-progress pattern.

---

## Queue-unblocking investigation

When starvation or closed-loop stagnation is detected, investigate:

1. **Why are Blocked tasks blocked?**
   - Did a prior kodo run fail and move them to Blocked?
   - Were they manually blocked by the operator?
   - Is a phase gate holding them (spec campaign phase not yet active)?

2. **Is duplicate suppression causing a deadlock?**
   - Propose emits candidates → skips because Blocked duplicates exist
   - Workers only consume Ready-for-AI → Blocked tasks never execute
   - No one re-queues them → permanent starvation

3. **What is the safe unblock path?**
   - Blocked → Backlog: safe if the task should be retried from scratch
   - Blocked → Ready-for-AI: safe if the task should be retried immediately
   - Leave blocked: only if operator action is explicitly required

4. **Do not blindly mutate queue state.** Identify the path and escalate via Plane task.
   If the operator has indicated that `self-modify:approved` tasks may be re-queued,
   moving them to Backlog is the safe default.

---

## Forward progress invariant

**Invariant:** Repeated remediation activity without measurable queue or execution
progress is platform degradation, not normal operation.

Measurable forward progress (any of these counts):
- Blocked count decreased vs prior cycle
- Ready-for-AI drained (tasks moved to Running or Done)
- Task state transitions occurred (Blocked→Ready, Running→Done, etc.)
- Regressions resolved (regression check findings decreased)
- Graph recovered (graph-doctor status improved)
- Watcher stabilized after a prior crash
- Autonomy-cycle outcomes improved (success rate increased)
- Propose `tasks_created > 0`

If remediation runs but none of these are true:
- Classify as stagnation
- Shorten cadence
- Escalate immediately
- Stop blindly retrying identical remediation paths

This invariant applies even if all individual audit tools report clean.
A platform that runs fine but produces no forward progress is stagnant.

---

## Blocked/stalled work investigation

The loop must actively detect and classify stuck work — not just report audit
findings. Each cycle reads the last 3 cycle summaries from `.console/log.md`
before classifying blocked items.

**Stagnation signals (any of these triggers immediate classification):**
- propose `skipped > 0` and `created = 0` while Blocked tasks exist (single cycle)
- Blocked count nonzero and Ready-for-AI is zero (single cycle, if approved work exists)
- Same audit finding present in 2+ consecutive cycle summaries
- Same Plane task title appearing in `Follow-ups` across 2+ cycles
- Same repo repeatedly skipped at execution gate
- Same watcher role in crash restarts across 2+ cycles
- Autonomy-cycle failures in recent cycles with no resolution
- Graph invariant failures persisting across cycles
- Flow-audit gaps open across 2+ cycles

**Blocked work classification:**

| Class | Meaning | Action |
|-------|---------|--------|
| `temporarily-blocked` | retry next cycle — forward progress IS occurring elsewhere | note in summary |
| `infra-blocked` | platform instability preventing execution | Plane task + DEGRADED cadence |
| `ownership-ambiguous` | affected repo not determinable | Plane task + skip execution |
| `validation-blocked` | failing tests / regressions | Plane task + targeted fix |
| `structurally-blocked` | needs operator or design action | Plane task + escalate |
| `crash-looping` | same failure repeating | Plane task + anti-flap rule |
| `starvation` | work exists and candidates generated, but no net forward progress | Plane task + STALLED + investigate queue |
| `dead-remediation` | retries with no state improvement | Plane task + STALLED + stop retrying |
| `closed-loop stagnation` | activity without measurable progress | Plane task + STALLED + investigate loop |

`structurally-blocked`, `dead-remediation`, `starvation`, and `closed-loop stagnation`
must be escalated with a Plane task immediately. Do not retry them in the same cycle.
Do not use "monitor for recurrence" language once the pattern is demonstrated.

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
| Classified dead-remediation, starvation, or closed-loop stagnation | ✗ | ✓ |

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
5. Treat the stopped watcher as a DEGRADED signal for adaptive cadence

Track per-role crash events in the cycle summary. If `.console/log.md` shows two consecutive
`watcher_restart` entries for the same role, the anti-flap rule applies.

---

## Structured cycle summary

Append one block per completed cycle to `.console/log.md`:

```markdown
## OC Platform Watchdog Cycle — <YYYY-MM-DD HH:MM>

- Lock owner: pid=<pid> hostname=<host>
- Branch / commit: <branch> @ <short-sha>
- Health state: <HEALTHY|ACTIVE|STALLED|DEGRADED|CRITICAL>
- Next cadence: <Ns> — <reason / driving signal>
- Plane status: <N> Ready-for-AI / <N> Running / <N> Blocked / <N> In-Review
- WorkStation / SwitchBoard status: <healthy|unreachable>
- Watchers: <N>/8 running | restarts this cycle: <role=exit_code,...>
- Audits run: custodian-sweep ghost-audit flow-audit graph-doctor reaudit-check regressions
- Findings reproduced this cycle: <tool=N,...> or "none"
- Blocked work: <N items> | classes: <class=N,...> or "none"
- Repeated findings (vs prior cycles): <tool=finding,...> or "none"
- Forward progress observed: <yes — reason | no — reason>
- Queue movement: <state transitions that occurred, or "none">
- Closed-loop stagnation detected: <yes — detail | no>
- Duplicate remediation churn: <yes — N skipped / no>
- Blocked queue deadlock suspected: <yes — detail | no>
- Stagnation detected: <yes — detail | no>
- Plane tasks opened/updated: <N> (<task IDs or "none">)
- Direct fixes dispatched: <repo=description,...> or "none"
- Repos touched: <list or "none">
- Repos skipped (gate failed): <list or "none">
- Validation run: pytest er000_phase0_golden (<N> passed) | <other>
- Graph status: <nodes>/<edges> graph_built=<True|False>
- Regressions checked: <N findings or "none">
- Watcher restarts / crash classifications: <role=exit_code:classification,...> or "none"
- Anti-flap escalations: <role=reason,...> or "none"
- Autonomy-cycle outcomes: <repo=success|fail,...> or "none"
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
| Blocked work | `.console/log.md` history + Plane | Classify stuck items; escalate starvation/stagnation immediately |
| Execution gate | Criteria check + stagnation check | Gate direct fixes; route rest to Plane |
| Direct fixes | `autonomy-cycle --execute` per repo | One repo at a time; kodo max_concurrent=1 |
| Invariants | `pytest er000_phase0_golden` + targeted | Plane task on failure |
| Watcher health | `watch-all-status` + log grep | Anti-flap classification; restart if safe |
| Log | `.console/log.md` structured block | One summary per cycle with all stagnation fields |
| Commit | `git commit` after validation | Per-repo; branch hygiene respected |
| Schedule | `ScheduleWakeup` adaptive delay | 180–3600s; STALLED minimum during starvation |

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
- Adaptive cadence must not widen kodo concurrency regardless of urgency
- **HEALTHY cadence forbidden while starvation or closed-loop stagnation is active**
- **Demonstrated stagnation escalates immediately — "monitor for recurrence" is not an action**
- **Repeated duplicate remediation generation with zero queue movement is starvation, not noise**

---

## Design-change procedure

If remediation appears to require a platform design change:

1. Stop direct remediation for that issue
2. Identify the exact invariant currently failing
3. Trace the full stack: config source → orchestration path → watchdog behavior →
   autonomy behavior → Plane/task emission → validation coverage
4. Compare at least two implementation strategies
5. Choose the smallest operationally safer change
6. Add/update Custodian or guardrail enforcement first
7. Validate that the change cannot silently regress
8. Document reasoning and blast radius in `.console/log.md`
9. Create/update Plane tasks for deferred follow-up work

The watchdog loop must not silently evolve platform architecture through
incidental remediation.

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

The loop uses adaptive cadence based on platform health state (see table above).
On a clean healthy stack the typical cycle runs ~7 minutes and sleeps ~60m.
On a degraded stack the loop shortens to 5–10m intervals until forward progress
resumes. The `ScheduleWakeup` delay is set at the end of each cycle based on
the worst health signal observed.

**The loop must optimize for restoring forward progress, not for observing
repeated failures.** Activity without progress is degradation.
