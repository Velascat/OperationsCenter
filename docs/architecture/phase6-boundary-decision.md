# Phase 6 Boundary Decision

## Decision

Phase 6 resolves to **Option B**: `OperationsCenter` is the canonical execution boundary.

There is no separate supported execution service outside OperationsCenter. The live
supported path is:

```text
OperationsCenter planning -> SwitchBoard routing -> OperationsCenter execution boundary
  -> Policy gate -> backend adapter dispatch -> observability retention
```

`ExecutionCoordinator` and `CanonicalBackendRegistry` are therefore part of the
intended architecture, not temporary leakage waiting to be extracted.

## Why this option

- The supported runtime already executes this way.
- No external execution layer exists as a real service boundary.
- Multiple OperationsCenter docs, tests, and the public README already depend on
  `ExecutionCoordinator` as the supported execution path.
- Re-describing the system as planning-only would keep architecture text in
  conflict with the actual code.

## What changed

- OperationsCenter docs and contract comments now state that execution lives inside
  OperationsCenter after routing.
- WorkStation architecture primers and checklist/result docs now reflect the
  same boundary and the real policy-before-execution order.
- Historical 9router notes remain only in explicitly historical migration or ADR
  material.
- The stale SwitchBoard adaptive-refresh test was removed because the runtime no
  longer exposes that loop.

## Final architecture truth

`OperationsCenter` plans work, consumes `SwitchBoard` routing, enforces policy,
dispatches bounded backend adapters, and records execution observability.
