# ADR 0001 — OperationsCenter as Canonical Execution Boundary

## Status

Accepted

## Context

The platform architecture needed a clear answer to where execution responsibility lives after SwitchBoard selects a routing decision. Two positions were evaluated:

**Option A — planning-only:** OperationsCenter handles task proposal and planning only. A separate execution service receives the routing decision and dispatches backend adapters.

**Option B — execution boundary inside OperationsCenter:** OperationsCenter owns the full path from planning through execution dispatch. After SwitchBoard returns a routing decision, OperationsCenter's execution boundary enforces policy, builds the `ExecutionRequest`, and dispatches the selected backend adapter.

The question was whether `ExecutionCoordinator` and `CanonicalBackendRegistry` were intended architecture or temporary leakage waiting to be extracted.

## Decision

**Option B is the canonical architecture.** OperationsCenter is the execution boundary.

The supported runtime path is:

```text
OperationsCenter planning → SwitchBoard routing → OperationsCenter execution boundary
  → Policy gate → backend adapter dispatch → observability retention
```

`ExecutionCoordinator` and `CanonicalBackendRegistry` are part of the intended architecture, not temporary placeholders.

## Consequences

- OperationsCenter docs, contract comments, and tests treat execution as living inside OperationsCenter after routing.
- PlatformDeployment architecture documentation reflects the same boundary and the real policy-before-execution order.
- Historical 9router notes remain only in explicitly historical migration or ADR material.
- There is no separate supported execution service outside OperationsCenter. Any future refactoring that would extract execution into a separate service boundary requires a new ADR with explicit evidence.
- The stale SwitchBoard adaptive-refresh test was removed because the runtime no longer exposes that loop.
- This decision is stable. Re-describing the system as planning-only would keep architecture text in conflict with the actual code.
