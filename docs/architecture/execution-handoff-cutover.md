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
4. observability recording

## Runtime Gate Order

```text
ProposalDecisionBundle
  -> ExecutionRequestBuilder
  -> PolicyEngine.evaluate(...)
  -> adapter_registry.for_backend(...)
  -> adapter.execute(request)
  -> ExecutionObservabilityService.observe(...)
```

Blocked or review-required policy outcomes never reach adapter invocation.

## Legacy Status

`LegacyExecutionRequest` remains only inside `control_plane.legacy_execution`. It is not the supported mainline execution request model.
