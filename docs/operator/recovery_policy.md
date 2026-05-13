# OC Platform Recovery Policy

This document contains the **machine-enforceable rules** that govern how the watchdog
loop, watchers, and queue semantics classify, escalate, and recover from platform failures.
It is the authoritative reference for classification tables, gate criteria, and invariants.

**Related docs:**
- [`watchdog_loop.md`](watchdog_loop.md) — operator runbook and embedded `/loop` prompt
- [`self_healing_model.md`](self_healing_model.md) — architecture, convergence phases, ownership model

---

## Queue Healing Rules

Queue healing is allowed only when structured metadata proves the transition is safe.

Automatic duplicate-deadlock breaker:

```text
IF duplicate exists in Blocked
AND no consumer can execute it
AND retry_safe = true
AND retry budget remains
THEN transition Blocked -> Ready for AI
```

Stale blocked recovery:

```text
IF task is Blocked beyond stale threshold
AND retry_safe = true
AND replay budget remains
THEN transition Blocked -> Backlog
```

Required queue metadata:
- `retry_lineage_id`
- `retry_safe`
- `backend_dependency`
- `recovery_attempt_count`
- `blocked_reason`
- `duplicate_key`

Required invariants:
- No infinite replay
- No duplicate storms
- No unsafe unblock
- No queue freeze caused by duplicate suppression

---

## Recovery Budgets

Investigation and recovery are bounded by machine-enforced budgets:

```yaml
recovery_budget:
  max_cycles_before_escalation: 3
  max_equivalent_retries: 2
  max_recovery_attempts: 5
```

After exhaustion, the system must escalate, park when appropriate, and wait for
semantic evidence change. The loop should not perform fresh deep investigation
for an unchanged, budget-exhausted root cause.

---

## Evidence Fingerprinting

Use canonical evidence hashes to distinguish real change from timestamp churn.

Hash inputs:
- exit code and signal
- normalized stacktrace or failure category
- queue state
- watcher state
- regression IDs
- backend health state

Ignore:
- timestamps
- cycle IDs
- run IDs
- log ordering noise

Parked and stalled decisions should use these hashes. Timestamp-only changes are
not new evidence.

---

## Starvation and Closed-Loop Stagnation

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

### Closed-Loop Stagnation

**Definition:** The platform repeatedly generates or retries equivalent remediation
work while queue state, task state, or execution state does not materially change.

Examples:
- Propose emits duplicates already in Blocked → skips → nothing changes → repeats
- Ready-for-AI never drains despite tasks present
- Same repos skipped every cycle at execution gate
- Same regressions recreated every cycle
- Blocked tasks never re-enter executable state

### Distinction from Temporary Delay

| Condition | Meaning | Cadence |
|-----------|---------|---------|
| Temporary delay | Work is still progressing | ACTIVE or HEALTHY |
| Starvation | No forward progress despite active work generation | STALLED minimum |
| Dead-remediation | Retries occurring without any state improvement | STALLED minimum |
| Closed-loop stagnation | Activity without measurable progress | STALLED minimum |
| Non-convergent automation | Retries reproduce equivalent outcomes with no evolution | STALLED minimum |
| Divergent automation | Platform health worsening under active remediation | DEGRADED minimum |
| Automation self-deception | Activity logs but state never evolves | DEGRADED minimum |
| Blocked queue deadlock | Work trapped in non-executable state | DEGRADED |
| Operator-blocked (parked) | Root cause known, Plane exists, no new evidence for 2+ cycles | PARKED_OPERATOR_BLOCKED (1800s) |

### Forbidden Language

Do not write:
- "potential starvation — monitor for recurrence"
- "flagging for next cycle"
- "will assess again after one more cycle"

once the evidence already demonstrates a closed retry/no-progress pattern. Similarly, do not
write "automation is running normally" when convergence analysis shows non-convergent behavior.

---

## Semantic Duplicate Remediation Detection

The watchdog does not require LLM-based semantic analysis. Compare across cycle summaries:

- **Title similarity** — task titles targeting the same repo with >80% shared tokens
- **Root-cause keywords** — same root-cause summary appearing in consecutive remediation attempts
- **Regression signatures** — same test name/path failing after a "successful" fix
- **Failure outcomes** — same repo+task_type+outcome combination repeated across cycles
- **Label overlap** — same `family:` and `area:` label combination on consecutive tasks

If any pattern is detected:
1. Classify as non-convergent
2. Create a Plane task with: which cycles showed the duplication, what was equivalent, what strategy should differ
3. Do NOT create another equivalent task
4. Do NOT retry the equivalent remediation path this cycle

Semantic duplication is evidence of planner non-convergence, not bad luck. The watchdog
must surface it rather than treating each retry as an independent fresh attempt.

---

## Watcher Handoff Investigation

When a task is blocked or stalled, the loop must ask not only "what is blocked?" but also:

- Which watcher produced this state?
- Which watcher should consume this state?
- Did the producing watcher emit enough evidence for the next watcher?
- Did a handoff contract fail?
- Did the queue state become non-consumable by any watcher?
- Is this a missing watcher behavior or a broken watcher behavior?

If the answer to any of these is "unknown" and the loop had to infer the answer manually,
that inference is a promotion candidate — the producing watcher should emit it as structured
evidence.

---

## Watcher-Owned Evidence

When the watchdog has to infer behavior from logs manually, the responsible watcher should
emit structured evidence instead. Examples of evidence that should be watcher-emitted:

| Evidence | Producing watcher |
|----------|------------------|
| Duplicate candidate count + reason | propose |
| Skipped candidate reason | propose |
| Blocked task reason | triage / intake |
| Last attempted remediation id | improve / review |
| Prior remediation lineage | improve / review |
| Retry strategy changed yes/no | improve |
| Queue transition attempted yes/no | goal / triage |
| Handoff target watcher | any transition |
| Why task is not executable | triage / goal |
| Why task is safe/unsafe to re-queue | triage / intake |

If this evidence is missing and caused watchdog guesswork in the current cycle, create or
update a Plane task for telemetry improvement targeting the responsible watcher.

---

## Automation Self-Deception

**Definition:** The platform reports activity, retries, task creation, or remediation
while meaningful execution progress does not occur. The system appears healthy by
operational metrics while producing no forward progress.

### Detection Signals

- Retries and cycles run but queue/task state is frozen across 2+ cycles
- Tasks recreated under new IDs with semantically equivalent scope and labels
- Watchers all healthy and propose active, but board state unchanged between cycles
- Remediation logs "completed" but the same regression or gap immediately recurs
- `propose.tasks_created > 0` but Blocked count never decreases
- Custodian sweep reports 0 findings but flow-audit reports the same open gaps cycle-over-cycle

### Why It Matters

Automation self-deception means the platform's health signals are decoupled from reality.
A cycle summary that says "audits clean, tasks created, watchers running" may still be
describing a frozen platform. The watchdog must not confuse **activity** with **progress**.

### Required Response

1. Classify the cycle as containing automation self-deception in the summary
2. Investigate: which specific metric claimed progress while state did not change?
3. Create a Plane task: what is the deception mechanism? (duplicate creation? false-clean audit?)
4. DEGRADED cadence minimum — HEALTHY is forbidden
5. Do not retry the same execution path this cycle

---

## Executor-Quality Investigation

When non-convergent, divergent, or self-deception is classified, investigate what the
automation/framework actually **did**, not merely whether it produced output.

### Evidence to Examine

| Question | Where to look |
|----------|--------------|
| Did the retry change execution strategy? | Compare task labels/descriptions across cycles |
| Did the planner emit tasks that evolved queue state? | Compare Blocked/Ready counts before/after propose |
| Did autonomy-cycle pick a different path after failure? | Autonomy-cycle logs + cycle summary history |
| Did propose adapt candidate selection after repeated skips? | propose log events: `tasks_skipped`, `tasks_created` trend |
| Did the execution path reach a worker? | Watcher logs + Running state transitions |
| Did remediation adapt after failure? | Failure categories in consecutive cycle summaries |

### Classification

| Executor quality | Evidence | Required action |
|----------------|----------|-----------------|
| `adaptive` | Strategy materially changed after failure | Convergent — continue |
| `repetitive` | Same strategy rerun without adaptation | Non-convergent — escalate |
| `degenerate` | Retries making state worse or introducing new failures | Divergent — escalate + DEGRADED |

### Guardrail

Do not accept "automation ran without errors" as evidence of quality. Require evidence that
the automation strategy **changed** after a prior failure before classifying as adaptive.

---

## Queue-Unblocking Investigation

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

## Forward Progress Invariant

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

## Blocked/Stalled Work Classification

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
| `dead-remediation` | retries with no state improvement or adaptation | Plane task + STALLED + stop retrying |
| `closed-loop stagnation` | activity without measurable progress | Plane task + STALLED + investigate loop |
| `non-convergent` | retries reproducing equivalent outcomes with no evolution | Plane task + STALLED + convergence analysis |
| `divergent` | automation measurably worsening platform health | Plane task + DEGRADED + executor investigation |
| `operator-blocked` | root cause known and unchanged; Plane escalation exists; no safe retry; no new evidence for 2+ cycles | Remain parked at PARKED_OPERATOR_BLOCKED (1800s); check unpark conditions only |

`structurally-blocked`, `dead-remediation`, `starvation`, `closed-loop stagnation`,
`non-convergent`, and `divergent` must be escalated with a Plane task immediately.
Do not retry them in the same cycle.
Do not use "monitor for recurrence" language once the pattern is demonstrated.
`operator-blocked` requires no further Plane escalation if one already exists — transition to PARKED.

---

## Execution Gate — Criteria Reference

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

## Affected-Repo Discovery — Deterministic Sources

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

## Custodian Enforcement Invariants

The following invariant is enforced by Custodian on every sweep:

- **OC10** (`kodo max_concurrent must be 1`) — reads
  `config/operations_center.local.yaml` and fails if `backend_caps.kodo.max_concurrent != 1`.
  Silently passes on fresh clones (local config absent).

Additional invariants maintained by runbook convention (not currently code-enforced):

**Platform architecture:**
- The loop is temporary operational scaffolding, not the permanent execution brain
- Repeated loop-only reasoning is convergence debt
- Common recovery paths must migrate into watchers or recovery engines
- The platform should become more autonomous without becoming opaque
- Recovery ownership should move downward toward runtime and watcher layers
- The loop should shrink over time, not grow indefinitely

**Operational guardrails:**
- No cron/systemd/daemon replacement for this loop
- No ADR modification from watchdog/autonomy loop paths
- No destructive git operations in loop helpers
- No runtime model policy widening from loop actions
- Adaptive cadence must not widen kodo concurrency regardless of urgency

**Stagnation and convergence:**
- HEALTHY cadence forbidden while starvation, closed-loop stagnation, non-convergent, or divergent automation is active
- Demonstrated stagnation escalates immediately — "monitor for recurrence" is not an action
- Repeated duplicate remediation generation with zero queue movement is starvation, not noise
- Non-convergent automation (retries with no adaptation) requires STALLED cadence + Plane task
- Divergent automation (health worsening under remediation) requires DEGRADED cadence + escalation
- Automation self-deception (activity without state evolution) forbids HEALTHY cadence
- Semantic duplicate remediation across 2+ cycles requires lineage investigation before retry
- "Automation ran" is not evidence of quality — adaptation after failure must be demonstrated

**Promotion and handoffs:**
- The watchdog loop is a temporary scaffold — repeated loop-only judgment is technical debt
- Same loop judgment in 2+ cycles requires a Plane task promoting it to the responsible watcher
- Watcher handoff gaps (producing watcher didn't emit enough evidence) are promotion candidates
- Promotion is evidence-driven — do not create redesign tasks for one-off failures

**Park and evidence:**
- Do NOT remain STALLED indefinitely when park criteria are met — transition to PARKED_OPERATOR_BLOCKED
- PARKED state requires a Plane escalation task; if none exists, remain STALLED and create one
- Parked cycle must check unpark conditions before scheduling — do not skip the check
- Timestamp differences alone do not qualify as new evidence — evidence requires changed state
- Operational convergence ≠ recovery — correct abstention from unsafe retry is a valid convergent outcome
- A loop that correctly parks (identifies blocker, creates escalation, abstains from replay) has converged
