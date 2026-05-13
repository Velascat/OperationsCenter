# OC Platform Self-Healing Model

This document describes the **architecture and convergence model** that the watchdog
loop is designed to move the platform toward. It defines the phase roadmap, ownership
boundaries, and runtime health semantics that guide loop and watcher design decisions.

**Related docs:**
- [`watchdog_loop.md`](watchdog_loop.md) — operator runbook and embedded `/loop` prompt
- [`recovery_policy.md`](recovery_policy.md) — machine-enforceable rules and classification tables

---

## Self-Healing Convergence Model

The watchdog loop is not the final architecture. It is transitional
operational scaffolding that exists to help the platform evolve from
loop-centric operational intelligence toward watcher-owned healing,
runtime-owned recovery, queue-owned recovery semantics, and a passive oversight
loop.

The convergence roadmap is explicit:

```text
loop-centric operational intelligence
  -> watcher-owned telemetry
  -> assisted recovery
  -> watcher-owned recovery
  -> runtime-owned healing
  -> oversight loop
  -> operational convergence
```

The purpose of the loop changes by phase. Early phases need investigation and
classification. Later phases should push recovery ownership downward into
watchers, queue policies, and runtime recovery engines. The loop should shrink
over time, not grow into a permanent orchestration brain.

### Ownership Placement

| Owner | Belongs there | Does not belong there |
|-------|---------------|-----------------------|
| Loop | Invariant audit, convergence phase estimate, escalation, architecture drift, operator reporting | Common retry paths, queue mutation rules, backend health inference, hidden policy decisions |
| Watchers | Local recovery, structured telemetry, handoff evidence, retry-safe queue recommendations | Global policy overrides, destructive recovery, opaque state mutation |
| Runtime recovery systems | Backend health state, cooldowns, retry budgets, evidence fingerprints, recovery state machines | Prompt-only decisions, unbounded retries, runtime policy widening |
| Queue semantics | Retry lineage, duplicate deadlock handling, stale blocked recovery, replay protection | Silent unsafe unblock, duplicate storms, human approval bypass |

### Phase 1 — Observational Loop

```text
Loop notices failures
Loop performs deep investigation
Loop manually infers recovery state
Loop classifies stagnation and convergence
```

Characteristics:
- Heavy log inspection
- Repeated inference
- Low structured telemetry
- Loop-centric intelligence
- Reactive operation

This is the historical bootstrap phase. It is useful for discovering missing
platform behavior, but it is not a target state.

### Phase 2 — Classification Loop

```text
Loop classifies operational patterns deterministically
Watchers begin emitting structured evidence
Convergence/stagnation become machine-readable
```

Characteristics:
- `blocked_reason`
- Executor signals
- Remediation lineage
- Duplicate suppression evidence
- Structured convergence fields

Goal: reduce raw log inference.

### Phase 3 — Assisted Recovery

```text
Loop orchestrates bounded self-healing
Recovery policies become machine-enforced
```

Examples:
- Backend cooldowns
- Retry budgets
- Queue healing
- Stale blocked recovery
- Duplicate deadlock breaking
- Parked-state persistence

Goal: stop replaying unsafe or pointless retries.

### Phase 4 — Watcher-Owned Recovery

```text
Watchers perform local recovery autonomously
Loop stops coordinating common remediation paths
```

Examples:
- `propose` detects starvation directly
- `triage` heals retry-safe blocked tasks
- `improve` handles bounded retries
- `watchdog` applies backend cooldowns automatically

Goal: recovery ownership moves downward into the platform.

### Phase 5 — Runtime-Owned Healing

```text
Recovery engine and runtime state machines own operational healing
```

Examples:
- Backend health registry
- Recovery state machine
- Evidence fingerprints
- Automatic park/unpark transitions
- Recovery adaptation tracking

Goal: the platform becomes operationally self-healing.

### Phase 6 — Oversight Loop

```text
Loop becomes mostly passive
```

Loop responsibilities become:
- Invariant enforcement
- Convergence auditing
- Escalation
- Architecture drift detection
- Operator reporting

The loop no longer:
- Manually heals queues
- Repeatedly investigates frozen states
- Infers backend instability from logs
- Coordinates common retry paths

### Phase 7 — Operational Convergence

```text
Platform converges operationally without loop-driven healing
```

Definition:
- Watchers recover locally
- Runtime healing converges automatically
- Queue deadlocks self-resolve safely
- Retries adapt automatically
- Parked states wake automatically on evidence change
- Loop primarily validates and escalates

Operator involvement becomes limited to:
- Infrastructure failures
- Architecture decisions
- Policy changes
- Destructive recovery approval

### Convergence Maturity Metrics

The loop summary should expose whether the platform is actually moving through
these phases:
- `loop_only_judgments_per_cycle`
- `manual_inference_events`
- `watcher_owned_recovery_rate`
- `automatic_queue_heal_rate`
- `parked_transition_accuracy`
- `recovery_adaptation_rate`
- `operator_escalation_rate`

These metrics are not vanity counters. They answer whether the platform is
becoming more self-healing or merely centralizing more intelligence inside the
loop.

---

## Anti-God-Object Guardrail

The watchdog loop must not evolve into:
- A permanent orchestration intelligence layer
- A hidden policy engine
- A universal recovery controller
- An opaque automation authority

The intended architecture is:
- Bounded
- Distributed
- Watcher-owned
- Runtime-owned
- Auditable
- Convergence-driven

The loop exists to help the platform evolve toward self-healing, not to
centralize all intelligence forever. Repeated loop-only reasoning is
convergence debt and should become watcher-owned, queue-owned, or
runtime-owned behavior.

---

## Convergence Promotion

The watchdog loop is a **temporary operator scaffold**, not the permanent brain of the platform.

Convergence promotion is the mechanism by which the platform transitions from
earlier convergence phases toward later phases. A loop-only judgment in Phase 1
or Phase 2 should become structured telemetry, watcher behavior, queue policy,
or recovery-engine logic in later phases.

When the loop repeatedly performs the same judgment, classification, unblock action, or
escalation, that behavior should be promoted into the responsible watcher or guardrail.
The goal is not for the `/loop` to become smarter forever. The goal is for the platform
to need the `/loop` less over time.

Repeated loop intelligence is technical debt. Every repeated loop-only judgment
should become platform-owned behavior unless there is a clear safety reason to
keep it operator-mediated.

| Repeated loop behavior | Promotion target |
|------------------------|-----------------|
| Duplicate propose churn detection | propose watcher |
| Blocked approved task detection | triage / intake watcher |
| Ready-for-AI starvation detection | goal / propose / watchdog coordination |
| Dead-remediation detection | improve / review watcher |
| Repeated executor failure detection | internal watchdog role |
| Graph drift detection | graph / audit watcher |
| Queue starvation detection | triage / goal watcher |
| Stale Plane task detection | intake / triage watcher |
| Semantic duplicate remediation detection | propose / review watcher |
| Remediation lineage tracking | improve / review watcher |
| Automation self-deception detection | watchdog + audit watchers |
| Cadence pressure / degraded health | internal watchdog role |

**Promotion rule:** if the `/loop` performs the same operational judgment in two or more
cycles, create or update a Plane task to move that judgment into the appropriate watcher,
guardrail, or telemetry source. Do not redesign immediately — capture evidence and ownership.

**Over-promotion guardrail:** Promotion candidates require at least one of:
- Repeated occurrence (2+ cycles)
- High-severity failure
- Missing structured evidence that blocked diagnosis
- Handoff gap that leaves work unowned
- Automation self-deception risk

Do not create watcher redesign tasks for one-off failures unless they reveal a missing
invariant or repeated pattern.

---

## Scaffold Removal Direction

The watchdog loop is allowed to coordinate temporarily, but repeated coordination should
become watcher-owned behavior. Over time, the loop should shrink from active operator
to oversight layer.

Healthy direction:
- Less manual inference
- More structured watcher evidence
- Fewer loop-only decisions
- Fewer repeated stuck states
- Clearer watcher handoffs
- Less need for short cadence

---

## Runtime Health Model

Backend stability is first-class platform state, not a conclusion the loop
reconstructs from log tails. Watchers and runtime services should write or
surface structured health records equivalent to:

```yaml
backend_health:
  kodo:
    state: unstable
    failure_count: 2
    last_success_at: null
    last_failure:
      signature: signal:SIGKILL
      signal: SIGKILL
      exit_code: null
    cooldown_until: "..."
    safe_retry_after: "..."
    recovery_strategy: reduce_pressure
```

Allowed states are `unknown`, `healthy`, `degraded`, `unstable`,
`unavailable`, `recovering`, and `operator_blocked`.

Runtime watchers own these transitions:
- Success resets backend health to `healthy`
- `SIGKILL` transitions to `unstable`, applies cooldown, and forbids immediate replay
- Repeated backend failures transition toward `unavailable` and escalation
- Exhausted recovery marks `operator_blocked` with an explicit reason

The loop should consume this state before reading raw logs. Raw logs are fallback
evidence only when structured health records are missing or contradictory.

---

## Recovery Ownership Boundaries

The permanent target is:

```text
watchers = healer + coordinator
loop = oversight + escalation + audit
```

This boundary is the transition from Phase 3 to Phase 4 in the self-healing
convergence model. Early phases allow the loop to coordinate recovery while
the platform learns; later phases require watchers to own common local recovery
paths directly.

Watcher telemetry must include enough structured evidence for the next watcher
or policy layer to act without prompt-side inference.

Required telemetry by owner:
- `improve`: `executor_exit_code`, `executor_signal`, `retry_strategy_used`,
  `retry_strategy_changed`, `remediation_attempt_number`,
  `remediation_lineage_id`, `prior_failure_signature`
- `triage`: `blocked_reason`, `blocked_by_backend`, `retry_safe`,
  `queue_transition_recommendation`
- `goal` / `propose`: `duplicate_reason`, `suppression_reason`,
  `starvation_detected`, `queue_deadlock_detected`
- `watchdog` / runtime supervisor: `backend_health_transition`,
  `cooldown_applied`, `recovery_attempt_started`, `recovery_attempt_result`
- `review`: remediation lineage and retry adaptation fields matching `improve`

When these fields exist, the loop should validate and audit them. It should not
repeat the same log analysis unless the structured telemetry is absent,
incomplete, or semantically changed.

---

## Recovery Strategies

Autonomous recovery is bounded and auditable. The platform may attempt:
- Executor restart: restart backend process, restart watcher, reinitialize runtime
- Queue healing: `Blocked -> Backlog`, `Blocked -> Ready for AI`, stale lock cleanup
- Runtime pressure mitigation: pause backend temporarily, reroute lightweight tasks,
  defer expensive remediation
- Cooldown enforcement: wait until `safe_retry_after` before retrying the same lineage

Recovery strategies begin as Phase 3 assisted recovery, where the loop may
orchestrate bounded actions. They should migrate toward Phase 4 watcher-owned
recovery and Phase 5 runtime-owned healing as soon as the safety conditions,
budgets, and telemetry are machine-enforced.

Guardrails:
- Do not widen runtime policy automatically
- Do not increase `kodo` concurrency automatically
- Do not bypass execution gates silently
- Do not replay unsafe retries
- Do not mutate queue state without structured evidence

---

## Self-Healing State Machine

The platform state machine is:

```text
HEALTHY
  -> DEGRADED
  -> RECOVERING
  -> HEALTHY

RECOVERING
  -> UNSTABLE
  -> COOLDOWN
  -> RECOVERING

UNSTABLE
  -> OPERATOR_BLOCKED
  -> PARKED_OPERATOR_BLOCKED
```

The loop orchestrates and audits transitions. Watchers own the local recovery
attempts and must emit transition events.

State-machine ownership belongs to later convergence phases. The loop may audit
and explain transitions, but recovery state machines should gradually replace
prompt-driven recovery decisions.

---

## Formal Parked Behavior

`PARKED_OPERATOR_BLOCKED` is a persisted system state, not just loop prose.
PARKED is not failure. It represents successful convergence of the loop's role
for a known blocker: the loop determined that no safe retry exists, escalation
already exists, no new evidence is present, and monitoring-only behavior is
required until evidence changes.

Parked metadata must include:

```yaml
parked_state:
  root_cause_signature: kodo_sigkill_plan_phase
  parked_reason: backend cooldown exhausted without safe retry
  unchanged_cycles: 14
  last_evidence_hash: abc123
  unpark_conditions:
    - backend_health_change
    - queue_change
    - runtime_config_change
    - watcher_state_change
    - execution_outcome_change
```

When parked, skip repeated deep investigation. Check only unpark conditions and
semantic evidence hash changes.
This is Phase 5/6 behavior: runtime and evidence state decide whether to wake;
the loop audits the decision instead of repeatedly re-investigating frozen
facts.

---

## Recovery Telemetry

Recovery events should be emitted as structured records:
- `recovery_attempt_started`
- `recovery_attempt_result`
- `cooldown_applied`
- `backend_health_transition`
- `queue_healing_decision`
- `recovery_budget_exhausted`
- `parked_state_entered`
- `parked_state_unparked`

Loop summaries should report metrics derived from these records:
- recovery success rate
- retry adaptation rate
- queue evolution quality
- backend stability state
- unchanged evidence cycles
- convergence phase estimate
- loop-only judgments per cycle
- manual inference events
- watcher-owned recovery rate
- automatic queue heal rate
- parked transition accuracy
- operator escalation rate

---

## Behavioral Convergence Analysis

**Definition:** Automation behavior is convergent when retries, remediation, and planning
evolve platform state toward resolution rather than reproducing equivalent outcomes repeatedly.

| Convergence state | Meaning | Required action |
|------------------|---------|-----------------|
| `convergent` | Retries evolve toward resolution | Note in summary |
| `weakly-convergent` | Progress occurring slowly but directionally | Monitor; note in summary |
| `non-convergent` | Retries reproduce equivalent outcomes with no net state change | STALLED + Plane task |
| `divergent` | Automation making platform health measurably worse | DEGRADED + escalate |

### Non-Convergent Signals (any of these is sufficient)

- Same propose duplicates skipped in 2+ consecutive cycles with no queue evolution
- Same repo targeted by autonomy-cycle 3+ times with identical failure or no-change outcome
- Regression retries recreate identical findings each cycle
- Blocked tasks recycled to Ready-for-AI with the same failed execution outcome repeatedly
- Remediation titles/labels/root-causes semantically equivalent across multiple cycles without strategy change

### Divergent Signals

- Blocked count increasing cycle-over-cycle while remediation is actively running
- Retries introducing new regressions rather than resolving existing ones
- Board health metrics worsening across consecutive cycles despite audits reporting clean

### What Convergence Is NOT

Convergence is not measured by whether the automation ran or produced logs. A retry that
produces the same outcome as all prior retries is not convergent even if it ran successfully.
The watchdog must ask: **did the automation adapt after failure?** If not, it is not converging.

### Remediation Lineage

Before any direct fix or Plane task creation, check the remediation history:

1. How many prior cycles targeted this exact finding?
2. Did prior remediation attempts change the execution outcome?
3. Did the strategy adapt, or was it a replay of the same path?

If 2+ equivalent prior attempts produced no outcome change: classify `dead-remediation`.
Do not replay identical paths. Include the prior-attempt history in the Plane task description.

---

## Operational Convergence Exit

**Convergence does not mean recovery.** A loop can converge correctly even when the platform
is still unhealthy — if the loop has correctly identified the blocker, escalated via Plane,
safely abstained from unsafe retry, and transitioned to PARKED state.

Operational convergence is Phase 7 in the self-healing convergence model. It
means the loop is no longer needed for common recovery paths because watchers,
queue semantics, and runtime recovery engines converge without prompt-driven
healing.

### The Convergence Exit Definition

The watchdog loop has reached **operational convergence** when:

1. The root cause of the block is identified and documented
2. A Plane escalation task exists, is current, and covers the blocker
3. No safe automation path exists to progress further
4. The loop has transitioned to PARKED_OPERATOR_BLOCKED (not looping at STALLED)
5. The loop is actively monitoring for new evidence (checking unpark conditions each cycle)

At this point, **the loop's job is done until the operator acts**. The loop is not failing;
it is correctly waiting. Remaining at STALLED cadence past this point is waste, not diligence.

### Behavioral Convergence vs Operational Convergence

| Concept | Meaning |
|---------|---------|
| Behavioral convergence | Retries evolve toward resolution |
| Operational convergence | Loop no longer needed for common recovery paths |

| | Behavioral convergence | Operational convergence |
|---|---|---|
| Definition | Retries materially evolve platform state toward resolution | Loop correctly identifies blocker, escalates, abstains from unsafe retry, parks |
| Platform needs to recover? | Yes — convergent means it's making progress | No — convergent means the loop's role is complete until operator acts |
| Outcome when correct | CONVERGENT or WEAKLY-CONVERGENT | PARKED_OPERATOR_BLOCKED |
| Outcome when incorrect | NON-CONVERGENT | STALLED indefinitely (loop running but not converging) |
