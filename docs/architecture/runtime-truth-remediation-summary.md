# Runtime Truth Remediation Summary

## Fixed

- Routing fidelity: `LaneDecision.selected_backend` now represents the real selector backend universe directly. Silent coercion to `kodo` was removed.
- Canonical execution handoff: the supported execution path now builds a real `ExecutionRequest` from `ProposalDecisionBundle` plus runtime context.
- Mandatory policy gate: `ExecutionCoordinator` evaluates policy after request construction and before any adapter invocation.
- Observability continuity: executed and policy-blocked runs both produce canonical `ExecutionResult` data that can be retained as `ExecutionRecord` plus `ExecutionTrace`.
- Changed-file truthfulness: retained records preserve source-aware changed-file evidence and do not silently upgrade non-empty file lists to authoritative certainty.
- Backend detail retention: capture-capable adapters now feed raw backend detail refs into the canonical retained record path by reference.
- Recommendation-only tuning: supported runtime no longer auto-applies tuning changes.

## Supported Runtime Shape

```text
ControlPlane proposes work
  -> SwitchBoard selects how
  -> ExecutionCoordinator builds ExecutionRequest
  -> PolicyEngine constrains
  -> canonical adapter executes
  -> observability records
```

## Legacy / Quarantined

- `control_plane.legacy_execution` still exists only as an explicitly gated compatibility path. It is disabled unless `CONTROL_PLANE_ENABLE_LEGACY_EXECUTION=1`.
- Supported entrypoints are the planning worker (`entrypoints/worker/main.py`) and the canonical execute entrypoint (`entrypoints/execute/main.py`).
- ControlPlane routing now defaults to `HttpLaneRoutingClient`, which crosses the SwitchBoard `/route` service boundary instead of importing SwitchBoard internals in-process.
- `switchboard_url` on backend invokers/adapters is compatibility-only. The canonical execution path does not route backend traffic through SwitchBoard as a transport proxy.

## Deferred

- Rich optional backends still require explicit runtime adapter configuration. Unsupported configured backends now fail loudly instead of being rewritten to `kodo`.
- Historical `9router` references remain in ADR and migration material where clearly marked as historical context.
