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

**Related docs:**
- [`self_healing_model.md`](self_healing_model.md) — convergence phases 1–7, architecture, ownership model
- [`recovery_policy.md`](recovery_policy.md) — classification tables, gate criteria, enforcement invariants

---

## Prerequisites

Before starting the loop, confirm:

1. **Plane up** — `plane-app-*` containers healthy at `http://localhost:8080`
2. **PlatformDeployment SwitchBoard up**:
   ```bash
   cd /home/dev/Documents/GitHub/PlatformDeployment
   docker compose -f compose/docker-compose.yml -f compose/profiles/core.yml up -d
   docker ps --filter name=platformdeployment-switchboard --format '{{.Status}}'
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

## Preflight Checklist

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

# 5. PlatformDeployment / SwitchBoard up
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

## Loop Ownership Lock

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

## Starting the Loop

Invoke in the Claude Code session:

```
/loop Run the OC/Platform stabilization and audit cycle from /home/dev/Documents/GitHub/OperationsCenter. Source .env.operations-center.local first. Use .venv/bin/ for all CLIs. This loop is session-bound and uses ScheduleWakeup, not cron/systemd.

STEP 0 — OWNERSHIP + PREFLIGHT:
Acquire/verify logs/local/watchdog_loop.lock via:
  scripts/operations-center.sh watchdog-loop-acquire
If another live owner exists, abort. If stale, reclaim.
Then confirm: Plane at http://localhost:8080, PlatformDeployment/SwitchBoard at http://localhost:20401/health, all 8 OC watchers running, .venv CLIs present, runtime low-cost policy (sonnet/haiku), kodo max_concurrent=1 in config, working tree state via git status.

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
This scan now includes queue self-healing when tasks carry structured evidence labels:
  retry_safe, queue_deadlock/no_consumer, dedup:<key>, retry-lineage:<id>.
It may transition Blocked→Ready-for-AI or Blocked→Backlog only when retry budgets
and safety evidence allow it; otherwise it comments an escalation.

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

WATCHER HANDOFF INVESTIGATION — for each blocked/stalled item:
  - Which watcher produced this state?
  - Which watcher should consume this state next?
  - Did the producing watcher emit enough structured evidence for the consumer to act?
  - Did a handoff contract fail? (producer emitted; consumer ignored or errored)
  - Did the queue state become non-consumable by any watcher?
  - Is this a missing watcher behavior or a broken watcher behavior?
If the answer required manual log inference, that inference is a promotion candidate.

BEHAVIORAL CONVERGENCE CHECK — after stagnation/starvation classification:
Read the last 3 cycle summaries and classify automation behavior:
  CONVERGENT: retries materially evolve platform state toward resolution
  WEAKLY-CONVERGENT: progress occurring slowly but directionally
  NON-CONVERGENT: retries reproduce semantically equivalent outcomes with no net state change
  DIVERGENT: automation making platform health measurably worse each cycle

Classify NON-CONVERGENT when ANY hold:
  - Same propose duplicates skipped in 2+ consecutive cycles with no queue evolution
  - Same repo targeted by autonomy-cycle 3+ times with same failure or no-change outcome
  - Regression retries recreate identical findings each cycle
  - Blocked tasks recycled to Ready-for-AI with the same execution outcome repeatedly
  - Remediation titles/labels/root-causes semantically equivalent across multiple cycles

Classify DIVERGENT when:
  - Board health metrics worsening vs prior cycles despite active remediation
  - Blocked count increasing cycle-over-cycle while remediation runs
  - Retries introducing new regressions rather than resolving existing ones

SEMANTIC DUPLICATE DETECTION — compare across cycles:
  - Task titles with high textual similarity targeting the same repo
  - Same root-cause keywords in consecutive remediation attempts
  - Same regression signature reproduced after a "successful" fix
  - Same failure outcome for the same repo+task_type combination
If detected: classify non-convergent, escalate via Plane task, do NOT retry equivalent path.

REMEDIATION LINEAGE — before any direct fix or Plane task, check:
  - How many prior cycles targeted this exact finding?
  - Did prior remediation attempts change the execution outcome?
  - Did the remediation strategy adapt after failure?
If 2+ equivalent prior attempts with no outcome change: classify dead-remediation.
Do NOT replay identical remediation paths. Include prior-attempt history in the Plane task.

AUTOMATION SELF-DECEPTION — classify and escalate immediately when:
  - Retries/cycles occur but queue/task state never changes (activity without state evolution)
  - Tasks recreated under new IDs with semantically equivalent scope
  - Watchers healthy and propose active, but board state frozen across 2+ cycles
  - Remediation logs "completed" but the same regression immediately recurs
This condition forbids HEALTHY cadence regardless of individual audit cleanliness.

EXECUTOR-QUALITY INVESTIGATION — when non-convergent, divergent, or self-deception is classified:
  Investigate what the automation/framework actually DID, not merely whether it ran:
  - Did any retry change the execution strategy?
  - Did the planner emit tasks that evolved the queue state?
  - Did the autonomy-cycle pick a different remediation path after prior failure?
  - Did the propose stage adapt its candidate selection after repeated skips?
  - Did the execution path ever reach a worker, or was it blocked before dispatch?
  Do not accept "automation ran" as evidence of quality. Require evidence of adaptation.

Classify each blocked item:
  - temporarily-blocked: retry next cycle — only valid when forward progress IS occurring elsewhere
  - infra-blocked: platform instability preventing execution
  - ownership-ambiguous: affected repo not determinable
  - validation-blocked: failing tests or regressions
  - structurally-blocked: requires operator or design action
  - crash-looping: same watcher or process failing repeatedly
  - starvation: work exists and candidates generated, but no net forward progress
  - dead-remediation: retries occurring without any state improvement or adaptation
  - closed-loop stagnation: activity without measurable queue or execution progress
  - non-convergent: retries reproducing equivalent outcomes with no evolution
  - divergent: automation making platform health measurably worse
  - operator-blocked: root cause known, direct remediation impossible, requires operator/infrastructure action

For starvation, dead-remediation, closed-loop stagnation, non-convergent, divergent, or structurally-blocked: create/update Plane task immediately. Do not use "monitor for recurrence" language. Do not retry identical failing remediation paths.

OPERATOR-BLOCKED CLASSIFICATION — classify as operator-blocked when ALL hold:
  - Root cause is already known and has not changed across ≥3 cycles
  - A Plane escalation task exists and covers the blocker
  - No queue evolution has occurred across those cycles
  - No safe retry path exists (unsafe, pointless, or infrastructure-gated)
  - No new evidence has emerged (see NEW EVIDENCE EVALUATION below)
Required metadata: blocker_summary, first_detected_cycle, affected_tasks, related_plane_tasks,
  safe_retry_condition, last_new_evidence_cycle, retry_forbidden_reason.

NEW EVIDENCE EVALUATION — evaluate each cycle when in STALLED or PARKED state.
NEW_EVIDENCE_DETECTED = yes ONLY if at least one of these changed since the prior cycle:
  - watcher state (new crash, new PID, new exit code or signal)
  - queue state (Blocked/R4AI/InReview counts changed)
  - remediation outcome (different result for same task)
  - exit signature (new signal, exit code, or failure message)
  - stacktrace or error detail (changed content, not just timestamp)
  - regression profile (new or resolved regression)
  - task transitions (any Plane task moved state)
  - runtime behavior (config changed, new watcher behavior)
  - graph state (node/edge counts changed)
  - telemetry detail (new structured field emitted by a watcher)
  - execution path (new code path reached or blocked)
Repeated identical observations are NOT new evidence. Timestamp differences alone are NOT new evidence.

PARK TRANSITION — evaluate STALLED → PARKED_OPERATOR_BLOCKED when ALL hold:
  - operator-blocked classification is active this cycle
  - Same root cause for ≥3 consecutive cycles (no root-cause change)
  - Same affected tasks across those cycles
  - Plane escalation exists and is current
  - No queue evolution across those cycles
  - No remediation adaptation across those cycles
  - NEW_EVIDENCE_DETECTED = no for 2+ consecutive cycles
  - No safe retry path
When parked: do NOT rerun deep investigation each cycle. Check only for evidence change (unpark conditions).
Do NOT remain in STALLED indefinitely once park criteria are met.

UNPARK CONDITIONS — check each parked cycle. If ANY hold, transition back to STALLED/DEGRADED/ACTIVE:
  - Queue state changed (any count difference)
  - Watcher crashed or restarted unexpectedly (non-143)
  - New telemetry appeared (exit signal, stacktrace, or error message changed)
  - Plane task status changed (escalated, commented on, closed, or resolved)
  - Operator took action on the blocker
  - Safe retry condition became true
  - Runtime config changed
  - New affected repos appeared in tool output
  - Execution outcome changed from prior identical attempts
If no unpark condition holds: remain parked, schedule at PARKED_OPERATOR_BLOCKED cadence (1800s).

FORWARD PROGRESS CHECK — before classifying as temporarily-blocked, confirm at least one of:
  - Blocked count decreased vs prior cycle
  - Ready-for-AI drained vs prior cycle
  - Task state transitions occurred
  - Regressions resolved
  - Watcher stabilized after crash
  - Autonomy-cycle outcomes improved
  - Remediation strategy demonstrably adapted (not just re-ran)
If none apply and remediation is actively running, classify as stagnation, not temporary delay.

STEP 4 — CONVERGENCE PROMOTION CHECK:
For each blocked/stalled/non-convergent behavior found in STEP 3, ask:
  - Did the /loop perform this same judgment in a prior cycle?
  - Which watcher should eventually own this detection or transition?
  - Did the responsible watcher emit enough structured evidence?
    (duplicate candidate count, skipped candidate reason, blocked task reason,
     last attempted remediation id, prior remediation lineage, retry strategy changed,
     queue transition attempted, handoff target watcher, why task is not executable,
     why task is safe/unsafe to re-queue)
  - Is there a watcher handoff gap?
    (Which watcher produced this state? Which watcher should consume it?
     Did the producing watcher emit enough evidence for the next watcher?
     Did a handoff contract fail? Is the queue state non-consumable by any watcher?
     Is this missing watcher behavior or broken watcher behavior?)
  - Should this become a Plane task for watcher improvement, guardrail enforcement,
    or telemetry improvement?
If the same loop-only judgment occurred in 2+ cycles: create/update a Plane task to
promote that behavior into the responsible watcher or guardrail.
Do NOT redesign immediately. Capture evidence and ownership.
Do NOT create watcher redesign tasks for one-off failures unless they reveal a missing
invariant or repeated pattern.

STEP 5 — EXECUTION GATE:
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

STEP 6 — DIRECT FIXES (only if loop owns the lock):
For each affected repo that passes the execution gate, run one at a time:
  scripts/operations-center.sh autonomy-cycle --config config/operations_center.local.yaml --execute --repo <path>
Respect kodo max_concurrent=1 — do not dispatch two repos simultaneously.

STEP 7 — INVARIANT ENFORCEMENT:
  .venv/bin/pytest tests/unit/er000_phase0_golden/ -q --tb=short
Also run targeted tests for any repo touched in STEP 6.
If any test fails and cannot be fixed safely in this cycle → create/update Plane task.

STEP 8 — WATCHER HEALTH + RESTART INVESTIGATION:
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

STEP 9 — LOG + COMMIT HYGIENE:
Append the structured cycle summary (see template in runbook) to .console/log.md.
The summary MUST include behavioral convergence fields AND convergence promotion fields:
  - Convergence phase estimate: <1-7>
  - Loop-owned recovery decisions this cycle: <N>
  - Watcher-owned recoveries this cycle: <N>
  - Automatic recovery actions executed: <list or "none">
  - Manual inference required: <yes/no + reason>
  - Recovery ownership migration candidates: <details or "none">
  - Behavioral convergence: <convergent|weakly-convergent|non-convergent|divergent>
  - Executor adaptation observed: <yes/no + reason>
  - Semantic duplicate remediation suspected: <yes/no>
  - Automation self-deception detected: <yes/no>
  - Retry quality: <adaptive|repetitive|degenerate>
  - Queue evolution quality: <healthy|stalled|cycling>
  - Convergence promotion candidates: <watcher=behavior,...> or "none"
  - Loop-only judgments repeated: <judgment=N cycles,...> or "none"
  - Watcher handoff gaps: <producer→consumer: gap,...> or "none"
  - Missing watcher evidence: <watcher=evidence needed,...> or "none"
  - Behavior to move out of /loop: <details or "none">
  - Convergence maturity metrics: loop_only_judgments_per_cycle=<N>,
    manual_inference_events=<N>, watcher_owned_recovery_rate=<0-1>,
    automatic_queue_heal_rate=<0-1>, parked_transition_accuracy=<0-1>,
    recovery_adaptation_rate=<0-1>, operator_escalation_rate=<0-1>
  - Operator-blocked state: <yes/no>
  - Parked state active: <yes — since cycle N | no>
  - Park reason: <blocker summary or "none">
  - New evidence detected: <yes — detail | no>
  - Safe retry condition: <condition or "none">
  - Last evidence-changing cycle: <cycle id or "N/A">
  - Repeated unchanged cycles: <N>
  - Active remediation suspended: <yes — reason | no>
Update .console/backlog.md for any new/closed gaps.
IMPORTANT: Run git diff --staged before committing to ensure only loop-owned files are staged.
Commit only after validation passes. Do not commit to main unless the operator has
explicitly allowed it for the current task/session. If not allowed, create a branch:
  git checkout -b oc-watchdog/<YYYYMMDD-HHMM>-<short-topic>
One logical commit per repo per cycle. Commit message must name: root cause, affected repo,
gate/check fixed. Never force-push, amend old loop commits, or commit generated noise.

STEP 10 — ADAPTIVE SCHEDULEWAKEUP:
Assess platform health state and choose ScheduleWakeup delay accordingly:

  CRITICAL                — crash loops / graph broken / autonomy failing repeatedly:              180s
  DEGRADED                — watcher crashes (non-143) / blocked queue unchanged / flow gaps:       300s
  STALLED                 — starvation active / closed-loop stagnation / no forward progress:      600s
  ACTIVE                  — direct fixes dispatched this cycle / remediation in flight:            900s
  PARKED_OPERATOR_BLOCKED — root cause known, Plane escalation exists, no new evidence:           1800s
  HEALTHY                 — all audits clean, no starvation signals, all watchers up:             3600s

PARK TRANSITION DECISION — evaluate STALLED → PARKED_OPERATOR_BLOCKED at end of each STALLED cycle:
  If ALL of the following hold, transition to PARKED (skip deep investigation next cycle):
    - operator-blocked classification active this cycle
    - Same root cause for ≥3 consecutive cycles (no root-cause change)
    - Same affected tasks across those cycles
    - Plane escalation exists and is current
    - No queue evolution across those cycles
    - No remediation adaptation across those cycles
    - NEW_EVIDENCE_DETECTED = no for 2+ consecutive cycles
    - No safe retry path
  If parked: set delaySeconds=1800, check only unpark conditions next cycle.
  Do NOT remain STALLED indefinitely once park criteria are met.

UNPARK TRANSITION DECISION — evaluate each parked cycle before scheduling:
  If ANY hold, transition back to STALLED/DEGRADED/ACTIVE (run full cycle):
    - Queue state changed (any count difference)
    - Watcher crashed or restarted unexpectedly (non-143)
    - New telemetry appeared (exit signal, stacktrace, or error message changed)
    - Plane task status changed (escalated, commented on, closed, or resolved)
    - Operator took action on the blocker
    - Safe retry condition became true
    - Runtime config changed
    - New affected repos appeared in tool output
    - Execution outcome changed from prior identical attempts
  If no unpark condition holds: remain parked at 1800s.

FORBIDDEN: Do not choose HEALTHY cadence if any of these are true:
  - starvation classified this cycle
  - closed-loop stagnation detected
  - Blocked tasks with self-modify:approved and zero Ready-for-AI
  - propose tasks_created=0/None while candidates emitted
  - blocked count unchanged from prior cycle while remediation ran
  - behavioral convergence classified as non-convergent or divergent
  - automation self-deception detected
  - kodo SIGKILL open issue unresolved AND new OC improve tasks appear in Ready-for-AI
  - 2+ consecutive cycles with semantically equivalent failed remediation and no adaptation

FORBIDDEN: Do not choose STALLED cadence when PARKED_OPERATOR_BLOCKED criteria are met.
  PARKED is not a downgrade — it is the correct state when the blocker is known, escalated,
  and no new evidence is available. Remaining at STALLED when park criteria are met wastes cycles.

Non-convergent automation: STALLED minimum cadence.
Divergent automation: DEGRADED minimum cadence.
Automation self-deception: DEGRADED minimum cadence + create Plane escalation task.

Use the WORST health state observed across all steps. Starvation/stagnation/convergence signals
force STALLED minimum immediately — single cycle evidence is sufficient.
Log the chosen cadence and the driving signal in the cycle summary.
Pass this full /loop prompt verbatim as the ScheduleWakeup prompt.
```

---

## Adaptive Cadence

The loop shortens its wake interval automatically when the platform is unhealthy.
The goal is to stabilize broken states quickly and back off once forward progress
resumes. Do not stay at 1h cadence when the platform is actively broken.

| Platform state | Wake delay | Trigger signals |
|----------------|-----------|-----------------|
| CRITICAL | 180s (~3m) | crash loops; graph broken; autonomy failing repeatedly |
| DEGRADED | 300s (~5m) | non-143 watcher crashes; blocked queue unchanged; open flow gaps |
| STALLED | 600s (~10m) | starvation active; closed-loop stagnation; no forward progress |
| ACTIVE | 900s (~15m) | direct fixes dispatched; remediation in flight |
| PARKED_OPERATOR_BLOCKED | 1800s (~30m) | root cause known; Plane escalation exists; no new evidence across 2+ cycles |
| HEALTHY | 3600s (~60m) | all clean; no starvation signals; all watchers up |

**Forbidden cadence widening:** HEALTHY cadence is forbidden while any of these signals are active:
- Starvation classified (current or prior cycle unresolved)
- Closed-loop stagnation detected
- `tasks_created=0/None` from propose while candidates were emitted
- Blocked tasks with `self-modify:approved` and zero Ready-for-AI
- Blocked count unchanged from prior cycle while remediation ran
- Behavioral convergence classified as non-convergent or divergent
- Automation self-deception detected (activity without state evolution)
- 2+ consecutive cycles of semantically equivalent failed remediation with no strategy adaptation

**STALLED cadence is also forbidden** when PARKED_OPERATOR_BLOCKED criteria are fully met.
Staying at 600s when the blocker is known, escalated, and evidence-frozen wastes cycles without
producing new information. Transition to PARKED (1800s) is required in that case.

A single demonstrated starvation/stagnation/non-convergence cycle drops cadence immediately.
It does not require two consecutive cycles of evidence.

---

## Structured Cycle Summary

Append one block per completed cycle to `.console/log.md`:

```markdown
## OC Platform Watchdog Cycle — <YYYY-MM-DD HH:MM>

- Lock owner: pid=<pid> hostname=<host>
- Branch / commit: <branch> @ <short-sha>
- Health state: <HEALTHY|ACTIVE|STALLED|DEGRADED|CRITICAL|PARKED_OPERATOR_BLOCKED>
- Next cadence: <Ns> — <reason / driving signal>
- Plane status: <N> Ready-for-AI / <N> Running / <N> Blocked / <N> In-Review
- PlatformDeployment / SwitchBoard status: <healthy|unreachable>
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
- Convergence phase estimate: <1-7>
- Loop-owned recovery decisions this cycle: <N>
- Watcher-owned recoveries this cycle: <N>
- Automatic recovery actions executed: <list or "none">
- Manual inference required: <yes/no — reason>
- Recovery ownership migration candidates: <details or "none">
- Behavioral convergence: <convergent|weakly-convergent|non-convergent|divergent>
- Executor adaptation observed: <yes — reason | no — reason>
- Semantic duplicate remediation suspected: <yes — N cycles / no>
- Remediation lineage investigated: <yes — N prior attempts | no>
- Automation self-deception detected: <yes — detail | no>
- Retry quality: <adaptive|repetitive|degenerate>
- Queue evolution quality: <healthy|stalled|cycling>
- Convergence promotion candidates: <watcher=behavior,...> or "none"
- Loop-only judgments repeated: <judgment=N cycles,...> or "none"
- Watcher handoff gaps: <producer→consumer: gap,...> or "none"
- Missing watcher evidence: <watcher=evidence needed,...> or "none"
- Behavior to move out of /loop: <details or "none">
- Convergence maturity metrics: loop_only_judgments_per_cycle=<N>,
  manual_inference_events=<N>, watcher_owned_recovery_rate=<0-1>,
  automatic_queue_heal_rate=<0-1>, parked_transition_accuracy=<0-1>,
  recovery_adaptation_rate=<0-1>, operator_escalation_rate=<0-1>
- Operator-blocked state: <yes/no>
- Parked state active: <yes — since cycle N | no>
- Park reason: <blocker summary or "none">
- New evidence detected: <yes — detail | no>
- Safe retry condition: <condition or "none">
- Last evidence-changing cycle: <cycle id or "N/A">
- Repeated unchanged cycles: <N>
- Active remediation suspended: <yes — reason | no>
- Follow-ups: <Plane task IDs or "none">
```

---

## What Each Cycle Does

| Step | Tools / commands | Action on findings |
|------|-----------------|-------------------|
| Ownership | `watchdog-loop-acquire` | Abort if live lock held by another PID |
| Preflight | curl, git status, grep | Confirm all services + config up front |
| Investigate | 6 audit CLIs in parallel | Collect findings; classify affected repos |
| Triage | `triage-scan --apply` | Promote Backlog → Ready for AI; queue self-healing if evidence allows |
| Blocked work | `.console/log.md` history + Plane | Classify stuck items; escalate starvation/stagnation immediately |
| Behavioral convergence | Last 3 cycle summaries | Classify convergence state; detect semantic duplication; check remediation lineage |
| Phase estimate | Structured telemetry + recovery actions | Estimate self-healing phase 1–7; identify ownership migration candidates |
| **Park evaluation** | STALLED classification + evidence check | Evaluate STALLED→PARKED transition; check unpark conditions if already parked |
| **Convergence promotion** | Loop history + watcher mapping | Identify repeated loop-only judgments; create Plane tasks for watcher ownership |
| Execution gate | Criteria check + stagnation/convergence check | Gate direct fixes; route rest to Plane |
| Direct fixes | `autonomy-cycle --execute` per repo | One repo at a time; kodo max_concurrent=1 |
| Invariants | `pytest er000_phase0_golden` + targeted | Plane task on failure |
| Watcher health | `watch-all-status` + log grep | Anti-flap classification; restart if safe |
| Log | `.console/log.md` structured block | One summary per cycle with all stagnation + promotion fields |
| Commit | `git commit` after validation | Per-repo; branch hygiene respected |
| Schedule | `ScheduleWakeup` adaptive delay | 180–3600s; STALLED minimum during starvation |

---

## Destructive-Action Guardrails

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

## Branch and Commit Hygiene

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

## Watcher Restart Classification and Anti-Flap

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

## Operator-Blocked Lifecycle

When the platform is blocked on an infrastructure or operator dependency that cannot be resolved
by automation alone, the loop should transition to a **passive evidence-monitoring posture** rather
than continuing to run deep investigation each cycle.

### Lifecycle Stages

```
New finding → STALLED (active investigation, 600s cadence)
                ↓ (if park criteria met — see below)
           PARKED_OPERATOR_BLOCKED (passive evidence watch, 1800s cadence)
                ↓ (if unpark condition triggers)
           STALLED/DEGRADED/ACTIVE (resume active investigation)
```

### Park Criteria (all must hold)

1. Root cause is known and has not changed across ≥3 consecutive cycles
2. A Plane escalation task exists and covers the blocker
3. No queue evolution has occurred across those cycles
4. No safe retry path exists (unsafe, pointless, or infrastructure-gated)
5. No new evidence has emerged for 2+ consecutive cycles

When ALL five hold, do NOT continue running full deep-investigation cycles. Switch to:
- Read `.console/log.md` for the last cycle's parked-state fields only
- Check each unpark condition (9 conditions listed in STEP 3 and STEP 10)
- If no unpark condition: schedule 1800s, emit minimal parked-cycle summary
- If any unpark condition: run full cycle at STALLED/DEGRADED/ACTIVE cadence

### Required Metadata When Parking

The cycle that triggers the transition must record in its summary:
- `Park reason:` — one-sentence blocker summary
- `Last evidence-changing cycle:` — cycle ID where something last changed
- `Repeated unchanged cycles:` — how many consecutive cycles showed no change
- `Safe retry condition:` — what must be true before retrying (operator action, infra fix, etc.)
- `Active remediation suspended:` — yes, and why

### Unpark Conditions

Any of these triggers a return to full investigation:
- Queue state changed (any Blocked/R4AI/InReview count difference)
- Watcher crashed or restarted unexpectedly (non-143)
- New telemetry appeared (exit signal, stacktrace, or error message changed content)
- Plane task status changed (escalated, commented on, closed, or resolved)
- Operator took visible action on the blocker
- Safe retry condition became true
- Runtime config changed
- New affected repos appeared in tool output
- Execution outcome changed from prior identical attempts

---

## Canonical Example: kodo SIGKILL (9c7f4bb9)

This section documents the SIGKILL block that ran for ~179 cycles (2026-05 session) as a
reference for future operator-blocked situations.

### What Happened

- kodo exited -9 (SIGKILL) at "Analyzing project and creating plan" regardless of task scope
- Both OC improve tasks and bounded CxRP tasks (ShippingForm) reproduced the pattern
- Root cause hypothesis: time-of-day resource exhaustion (confirmed by AgentTopology succeeding
  at 20:22Z, ShippingForm SIGKILL'd at 23:46Z)
- Plane task 5d8bd236 escalated with DIVERGENT finding

### How the Loop Should Have Behaved (Retroactive)

| Cycle range | Correct behavior | Actual behavior |
|-------------|-----------------|-----------------|
| 1–3 (discovery) | Classify operator-blocked; create Plane task; run full investigation | ✓ Correct |
| 4–5 (confirmation) | Verify same root cause, same tasks; park criteria met | Continued STALLED — missed park transition |
| 6–179 (frozen) | PARKED_OPERATOR_BLOCKED (1800s); check unpark conditions only | Ran full 600s cycles with no new information |

### What Should Have Triggered the Park Transition

After cycle 5 (≥3 cycles, same root cause, same tasks, Plane task 5d8bd236 exists, no queue
evolution, NEW_EVIDENCE_DETECTED=no for 2 consecutive cycles):

- Transition: STALLED → PARKED_OPERATOR_BLOCKED
- Cadence: 600s → 1800s
- Behavior: skip deep investigation; check 9 unpark conditions only
- Summary: minimal parked-cycle entry with park metadata fields

### Safe Retry Condition for This Case

`kodo SIGKILL resolved` — operator must diagnose and fix kodo resource exhaustion before
any blocked improve tasks or ShippingForm can be re-queued.

---

## Design-Change Procedure

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

## Stopping the Loop

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
