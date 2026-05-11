# Contract Map

Single source of truth for which contracts are canonical cross-repo wire
contracts, which models are OperationsCenter-internal orchestration models,
and where mapping happens.

---

## Ownership

| Contract / Model | Owner | Role |
|---|---|---|
| `cxrp.contracts.TaskProposal` | CxRP | Canonical cross-repo task proposal wire contract |
| `cxrp.contracts.LaneDecision` | CxRP | Canonical cross-repo routing decision wire contract |
| `operations_center.contracts.proposal.OcPlanningProposal` | OperationsCenter | Internal planning/orchestration model |
| `operations_center.contracts.routing.OcRoutingDecision` | OperationsCenter | Internal routing/orchestration model |
| `cxrp.contracts.ExecutionRequest` | CxRP | Canonical cross-repo execution request wire contract |
| `cxrp.contracts.ExecutionResult` | CxRP | Canonical cross-repo execution result wire contract |
| `operations_center.contracts.execution.OcExecutionRequest` | OperationsCenter | Internal execution-boundary request model |
| `operations_center.contracts.execution.OcExecutionResult` | OperationsCenter | Internal execution-boundary result model |

Compatibility aliases kept for migration stability:

- `operations_center.contracts.proposal.TaskProposal`
  -> `OcPlanningProposal`
- `operations_center.contracts.routing.LaneDecision`
  -> `OcRoutingDecision`
- `operations_center.contracts.execution.ExecutionRequest`
  -> `OcExecutionRequest`
- `operations_center.contracts.execution.ExecutionResult`
  -> `OcExecutionResult`

Those aliases are not the canonical wire contract definitions.

---

## Flow

```text
PlanningContext
    -> OcPlanningProposal
    -> to_cxrp_task_proposal(...)
    -> CxRP TaskProposal  --------- HTTP ---------> SwitchBoard
    <- CxRP LaneDecision  <-------- HTTP ----------
    <- from_cxrp_lane_decision(...)
    <- OcRoutingDecision
    -> ProposalDecisionBundle
    -> OcExecutionRequest
    -> adapter
    -> OcExecutionResult
```

---

## Mapping Points

| Boundary | Mapper | Input | Output |
|---|---|---|---|
| OC -> SwitchBoard | `contracts.cxrp_mapper.to_cxrp_task_proposal` | `OcPlanningProposal` | `cxrp.contracts.TaskProposal` |
| SwitchBoard -> OC | `contracts.cxrp_mapper.from_cxrp_lane_decision` | CxRP lane decision payload | `OcRoutingDecision` |
| OC -> CxRP execution | `contracts.cxrp_mapper.to_cxrp_execution_request` | `OcExecutionRequest` | `cxrp.contracts.ExecutionRequest` |
| CxRP -> OC execution result | `contracts.cxrp_mapper.from_cxrp_execution_result` | CxRP execution payload | `OcExecutionResult` |

---

## Invariants

1. CxRP owns canonical cross-repo proposal, routing, and execution semantics.
2. OperationsCenter may own stricter internal orchestration and execution-boundary models.
3. OC internal proposal, routing, and execution-boundary models must map explicitly through CxRP at repo boundaries.
4. OC internal models must not be documented as canonical protocol contracts.
5. Compatibility aliases may remain temporarily, but they do not change ownership.

---

## Internal Boundary Types

| Type | File | Purpose |
|---|---|---|
| `PlanningContext` | `planning/models.py` | Raw planning input before OC proposal construction |
| `ProposalBuildResult` | `planning/models.py` | Wraps `OcPlanningProposal` + original context |
| `ProposalDecisionBundle` | `planning/models.py` | Pairs `OcPlanningProposal` + `OcRoutingDecision` |
| `ExecutionRuntimeContext` | `execution/handoff.py` | Runtime-resolved paths not present in the proposal |
| `PolicyDecision` | `policy/models.py` | Policy gate outcome consumed by `ExecutionCoordinator` |
| `ExecutionRecord`, `ExecutionTrace` | `observability/models.py` | Audit/trace types outside the wire boundary |
