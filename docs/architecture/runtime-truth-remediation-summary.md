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

- `control_plane.legacy_execution` has been removed from the runtime architecture.
- Supported entrypoints are the planning worker (`entrypoints/worker/main.py`) and the canonical execute entrypoint (`entrypoints/execute/main.py`).
- ControlPlane routing now defaults to `HttpLaneRoutingClient`, which crosses the SwitchBoard `/route` service boundary instead of importing SwitchBoard internals in-process.
- Backend invokers/adapters no longer accept execution proxy transport overrides.

## Deferred

- Rich optional backends still require explicit runtime adapter configuration. Unsupported configured backends now fail loudly instead of being rewritten to `kodo`.
- Historical provider-router references remain only in explicitly archival material.
