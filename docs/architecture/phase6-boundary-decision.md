# Phase 6 Boundary Decision

## Decision

Phase 6 resolves to **Option B**: `ControlPlane` is the canonical execution boundary.

There is no separate supported execution service outside ControlPlane. The live
supported path is:

```text
ControlPlane planning -> SwitchBoard routing -> ControlPlane execution boundary
  -> Policy gate -> backend adapter dispatch -> observability retention
```

`ExecutionCoordinator` and `CanonicalBackendRegistry` are therefore part of the
intended architecture, not temporary leakage waiting to be extracted.

## Why this option

- The supported runtime already executes this way.
- No external execution layer exists as a real service boundary.
- Multiple ControlPlane docs, tests, and the public README already depend on
  `ExecutionCoordinator` as the supported execution path.
- Re-describing the system as planning-only would keep architecture text in
  conflict with the actual code.

## What changed

- ControlPlane docs and contract comments now state that execution lives inside
  ControlPlane after routing.
- WorkStation architecture primers and checklist/result docs now reflect the
  same boundary and the real policy-before-execution order.
- Historical 9router notes remain only in explicitly historical migration or ADR
  material.
- The stale SwitchBoard adaptive-refresh test was removed because the runtime no
  longer exposes that loop.

## Final architecture truth

`ControlPlane` plans work, consumes `SwitchBoard` routing, enforces policy,
dispatches bounded backend adapters, and records execution observability.
