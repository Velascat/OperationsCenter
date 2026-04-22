# Execution Handoff Cutover

## Supported Live Path

```text
TaskProposal
  -> LaneDecision
  -> ExecutionRequest
  -> adapter
  -> ExecutionResult
```

`ExecutionRequest` is no longer architecture theater. The supported runtime builds it in `control_plane.execution.handoff.ExecutionRequestBuilder` using:

- `ProposalDecisionBundle`
- runtime workspace context
- policy-effective scope when present

`ExecutionCoordinator` is the supported boundary that owns:

1. canonical `ExecutionRequest` construction
2. mandatory policy evaluation
3. canonical adapter selection
4. capture-aware execution when an adapter supports `execute_and_capture()`
5. observability recording, including retained `BackendDetailRef` entries by reference

## Runtime Gate Order

```text
ProposalDecisionBundle
  -> ExecutionRequestBuilder
  -> PolicyEngine.evaluate(...)
  -> adapter_registry.for_backend(...)
  -> adapter.execute(request) or adapter.execute_and_capture(request)
  -> ExecutionObservabilityService.observe(...)
```

Blocked or review-required policy outcomes never reach adapter invocation.

Changed-file certainty is preserved from the adapter result into observability.
Non-empty `changed_files` lists do not become authoritative unless the source is
authoritative (`git_diff`, `backend_manifest`, or equivalent explicit evidence).

SwitchBoard remains selector-only in the canonical path. Execution transport
proxy overrides have been removed from backend adapters and invokers.

## Legacy Status

The legacy execution runtime has been removed. `execution/coordinator.py` is
the only supported execution boundary.
