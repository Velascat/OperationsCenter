# Policy Pre-Execution Gate

## Gate Location

The supported pre-execution gate sits here:

```text
ProposalDecisionBundle
  -> ExecutionRequestBuilder
  -> PolicyEngine.evaluate(...)
  -> adapter invocation
```

Policy runs after canonical request construction so it can evaluate:

- routed lane/backend choice
- effective scope / allowed paths
- validation command presence
- runtime execution constraints

## Enforced Outcomes

- `allow`: execution proceeds
- `allow_with_warnings`: execution proceeds and warnings are retained
- `require_review`: autonomous execution stops
- `block`: execution stops

`require_review` and `block` are not advisory in the supported runtime. They prevent adapter invocation.

## Evidence Retention

When policy stops execution, the runtime still materializes a canonical blocked `ExecutionResult` and retains:

- the `PolicyDecision`
- the blocked `ExecutionRecord`
- the derived `ExecutionTrace`

This keeps policy-gated non-execution inspectable instead of disappearing as an unlogged branch.
