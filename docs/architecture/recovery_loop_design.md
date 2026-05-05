# Recovery Loop Design

> Inspired by `materialsproject/custodian`'s job recovery pattern. Implementation must NOT use the name `Custodian` — that name is taken by a sibling repo in this ecosystem.

## Current Execution Flow

`ExecutionCoordinator.execute(bundle, runtime)` at
`src/operations_center/execution/coordinator.py`:

```text
ProposalDecisionBundle
  → ExecutionRequestBuilder.build()       → ExecutionRequest
  → PolicyEngine.evaluate()               → PolicyDecision (BLOCK/REQUIRE_REVIEW/ALLOW_*)
    [if BLOCK or REQUIRE_REVIEW: short-circuit with _policy_blocked_result + observe → return]
  → ExecutionRequestBuilder.build()       → ExecutionRequest (rebuilt with policy_decision)
  → WorkspaceManager.prepare()            → clones repo, creates branch
    [on failure: _workspace_prep_failed_result + observe → return]
  → CanonicalBackendAdapter.execute()     → ExecutionResult
    (or .execute_and_capture() if adapter implements _CaptureCapableAdapter)
    [on raised exception: _adapter_crash_result]
  → WorkspaceManager.finalize()           → commits, pushes, opens PR (best-effort, non-fatal)
  → ExecutionObservabilityService.observe() → ExecutionRecord + ExecutionTrace
  → return ExecutionRunOutcome
```

Adapter implementations live in `src/operations_center/backends/`:
- KodoBackendAdapter, DirectLocalBackendAdapter, AiderLocalBackendAdapter,
  ArchonBackendAdapter, OpenClawBackendAdapter
- Registered in `CanonicalBackendRegistry` via `BackendName` enum

## Proposed Recovery Flow

The recovery loop wraps the **single adapter execution call**
(`_execute_adapter()`) inside `ExecutionCoordinator.execute()`. Everything
upstream (policy, workspace prep) and downstream (workspace finalize,
observability) is unchanged.

```text
[unchanged: build + policy + workspace prep]
  ↓
adapter = registry.for_backend(...)
recovery_actions = []
current_request = request
validated_for = current_request   # already validated by PolicyEngine above

for attempt in 1..policy.max_attempts:
    result, raw_refs, runtime_meta = _execute_adapter(adapter, current_request)
    record_attempt(attempt, result)
    context = RecoveryContext(original_request, current_request, attempt, prev_actions)
    outcome = recovery_engine.evaluate(result, context)
    recovery_actions.append(outcome.action)

    if outcome.decision == ACCEPT: break
    if outcome.next_request is None: break
    if outcome.delay_seconds is not None: bounded_sleep(...)

    request_changed = outcome.next_request is not current_request
    current_request = outcome.next_request

    if request_changed or outcome.requires_policy_revalidation:
        policy_decision = policy_engine.evaluate(...current_request)
        if policy_decision.status in {BLOCK, REQUIRE_REVIEW}:
            result = _policy_blocked_result(current_request, policy_decision)
            break
        validated_for = current_request

  ↓
result = attach_recovery_metadata(result, recovery_actions)
[unchanged: workspace finalize + observability]
```

## Naming Decision

The recovery layer is named **RecoveryLoop** (or **AttemptLoop** /
**ExecutionRecoveryLoop** as alternatives). Implementation modules live at:

```text
src/operations_center/execution/recovery_loop/
  __init__.py
  models.py     — ExecutionFailureKind, RecoveryDecision, RecoveryAction,
                  RecoveryContext, RecoveryOutcome, RecoveryMetadata,
                  AdapterErrorCode
  classifier.py — FailureClassifier protocol + DefaultFailureClassifier
  policy.py     — RecoveryPolicy + RetryBudgetChecker protocol
  handlers.py   — RecoveryHandler protocol + RetrySameRequestHandler +
                  RejectUnrecoverableHandler
  engine.py     — RecoveryEngine
  timing.py     — bounded_sleep helper
```

Banned module/class names: `custodian.py`, `Custodian`, `CustodianLoop`,
`JobCustodian`, etc. Enforced by an invariant test.

## Relationship to Existing Dispatch FailureKind

`audit_dispatch/models.py` already has a `FailureKind` enum for subprocess
dispatch failures (PROCESS_NONZERO_EXIT, PROCESS_TIMEOUT, EXECUTOR_ERROR,
RUN_STATUS_MISSING, RUN_STATUS_INVALID, MANIFEST_PATH_MISSING,
MANIFEST_PATH_UNRESOLVABLE, UNKNOWN). That enum stays where it is — it is
specific to the audit dispatch path.

The new recovery layer uses **`ExecutionFailureKind`** (TRANSIENT, TIMEOUT,
RATE_LIMIT, AUTH, CONFIGURATION, CONTRACT_VIOLATION, BACKEND_UNAVAILABLE,
NONE, UNKNOWN). No semantic overlap; both names live in different packages.
An invariant test prevents anyone from creating a second generic
`FailureKind` enum in the codebase.

## Idempotency Rules

`ExecutionRequest` gains a new field:

```python
idempotent: bool = False
```

Default is **False** (safe). The recovery engine refuses retries for
non-idempotent requests unless the failure kind is in
`policy.pre_send_failure_kinds`. Default `pre_send_failure_kinds =
{BACKEND_UNAVAILABLE}`.

`TIMEOUT` is intentionally NOT considered pre-send: a timeout may happen
after the backend already began work.

## Rate Limit and Backoff Decision

`RATE_LIMIT` is allowed to retry only when ALL of the following hold:

1. `policy.rate_limit_retry_requires_backoff = True` (the default)
2. `result.error_details` contains a usable structured `retry_after` value
3. parsed delay > 0 and ≤ `policy.max_delay_seconds` (default 30s)
4. the coordinator performs the bounded synchronous sleep before the next attempt

If any condition fails, the engine returns `STOP_BACKOFF_REQUIRED`. A bare
retry on rate-limit just slams the wall again — the conservative v1 default
is to stop unless backoff is explicit.

Default `RecoveryPolicy.retryable_kinds` does NOT include `RATE_LIMIT`
until the bounded backoff path is fully wired and tested.

## Delay Enforcement Rule

`RecoveryOutcome.delay_seconds` is enforced via `bounded_sleep` from
`recovery_loop/timing.py`:

```python
def bounded_sleep(delay_seconds: float, max_delay_seconds: float) -> None:
    delay = max(0.0, min(delay_seconds, max_delay_seconds))
    time.sleep(delay)
```

The actual slept duration (clamped) is recorded on `RecoveryAction.delay_seconds`,
not the unclamped requested value.

## Cost Budget Decision

No vague integer "cost units" in v1. Cost gating uses an injectable
`RetryBudgetChecker` protocol:

```python
class RetryBudgetChecker(Protocol):
    def can_retry(request, context) -> bool: ...
```

Default: no checker installed → no cost gate. Refusal produces
`STOP_COST_BUDGET_EXHAUSTED`. Each checker owns its own unit semantics.

## Modified Request Revalidation Rule

**Hard rule:** No adapter execution happens without `PolicyEngine`
validation for the request being executed.

In the coordinator loop:
- The first request is validated before the first attempt
- An unchanged retry reuses the prior validation
- A modified request triggers a re-evaluation through `PolicyEngine`
- If re-evaluation returns `BLOCK`/`REQUIRE_REVIEW`, the loop terminates
  with `_policy_blocked_result`

Request-change detection uses object identity (`outcome.next_request is not
current_request`), not structural equality, since `RETRY_SAME_REQUEST`
explicitly returns the same instance.

## Validation Reuse vs Revalidation Decision

We reuse the prior `policy_decision` when the request didn't change. This
is safe because `PolicyEngine.evaluate()` is a pure function of
`(proposal, decision, request)`. Re-evaluating the unchanged tuple would
return the same result and add cost (the engine may inspect repos, paths,
etc.).

If a future implementation makes `PolicyEngine.evaluate()` cheap and
side-effect-free, the loop may revalidate every iteration. That is a
documented opt-in per the spec.

## Unknown Failure Semantics

`ExecutionFailureKind.UNKNOWN` is the classifier's "I cannot map this
failure" bucket. Default behavior is to **reject as unrecoverable** —
unknown failures should never be silently retried.

A policy knob (`retry_unknowns: bool = False`) can opt in retries with a
separate `unknown_retry_limit`. Hidden retries via handler flags are
forbidden.

`AdapterErrorCode.UNKNOWN` (the adapter-side "I cannot categorize my own
failure") is a different concept — it is one input the classifier looks at
when deciding `ExecutionFailureKind`.

## R1–R12 Design Review Rationale

Captured here for permanence; the spec text moves through 5 review rounds
and the rationale otherwise rots.

- **R1 — No `Custodian` naming.** Sibling repo named Custodian exists. Clash creates confusion. Naming is enforced by invariant test.
- **R2 — Avoid collision with existing `FailureKind`.** `audit_dispatch.FailureKind` is subprocess-oriented; we use `ExecutionFailureKind`.
- **R3 — Model idempotency before retrying.** Many backend operations are non-idempotent (file writes, git commits, API calls with side effects). Default-False is safe.
- **R4 — Rate limit retry requires backoff.** A bare retry on rate-limit hits the wall again. Default conservative: don't retry rate-limit unless backoff is wired.
- **R5 — Modified requests must be revalidated.** Skipping policy on a corrected request is a security hole. Hard rule, enforced by invariant test.
- **R6 — UNKNOWN does not retry by default.** Unknown failures should fail loudly, not silently get extra attempts.
- **R7 — Circuit breaker is explicit v2.** Per-backend health suppression deserves its own design pass.
- **R8 — Avoid free-form drifting adapter error codes.** Free-form strings drift; use a controlled enum.
- **R9 — Drop USER_INPUT_INVALID.** Validation belongs upstream, not in the recovery layer.
- **R10 — Simplify handler protocol.** Single `recover() -> RecoveryOutcome | None` instead of `can_handle()` + `recover()`.
- **R11 — Cost-awareness via checker, not undefined integer units.** Cost units must be defined; if undefined, use a checker that owns the unit semantics.
- **R12 — Phase 1 must produce a durable design artifact.** This document.

## Observability Requirements

Per attempt:
- request_id, attempt, backend/lane (if available), status, ok
- execution_failure_kind, recovery_decision, handler_name
- idempotent, cost_budget_state, backoff_delay_seconds (if any)

Final summary event:
- total_attempts, final_status, final_recovery_decision, actions_taken, retry_refused_reason

Existing `ExecutionObservabilityService.observe()` is extended (not replaced).
The final `ExecutionResult` carries `RecoveryMetadata` with the full action
chain.

## Non-Goals

- Background scheduling
- Plane polling
- Autonomous task picking
- Fallback lane reselection
- Cross-repo recovery handlers
- Backend-specific policy inside adapters
- Wholesale rewrite of `ExecutionCoordinator`
- v1 circuit breaker / backend health suppression
- Hidden hosted-backend cost multiplication
- Retry of non-idempotent side-effectful requests unless explicitly proven pre-send

## V2: Circuit Breaker / Backend Health Suppression

When a backend is consistently failing, retrying every request still
hammers it. v2 should add:
- Per-backend failure-rate tracking
- Configurable thresholds (e.g. "5 failures in 60s → trip")
- Half-open recovery probes
- Backend health registry
- Adaptive retry budgets

Out of scope for v1. Tracked here so it doesn't get retrofit ad-hoc.
