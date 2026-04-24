# Contract Map

Single source of truth for what each canonical contract is, where it lives,
who produces it, and who consumes it.

---

## Canonical Contracts

| Contract | File | Line | Description |
|---|---|---|---|
| `TaskProposal` | `src/control_plane/contracts/proposal.py` | 31 | What needs to be done, where, and under what constraints |
| `LaneDecision` | `src/control_plane/contracts/routing.py` | 28 | Selected lane/backend and routing rationale from SwitchBoard |
| `ExecutionRequest` | `src/control_plane/contracts/execution.py` | 38 | Everything a backend adapter needs to carry out the work |
| `ExecutionResult` | `src/control_plane/contracts/execution.py` | 146 | Backend-agnostic outcome of one execution run |

All four are Pydantic v2 `BaseModel` with `model_config = {"frozen": True}`.
All four are fully serializable via `.model_dump(mode="json")` / `.model_validate()`.
All four are re-exported from `control_plane.contracts` (the public API surface).

---

## Supporting Types

| Type | File | Role |
|---|---|---|
| `TaskTarget` | `contracts/common.py` | Repo coordinates (key, clone URL, base branch, allowed paths) |
| `ExecutionConstraints` | `contracts/common.py` | Execution limits (timeout, max files, path restriction) |
| `ValidationProfile` | `contracts/common.py` | Validation commands and fail-fast policy |
| `BranchPolicy` | `contracts/common.py` | Branch prefix, push-on-success, PR policy |
| `ExecutionArtifact` | `contracts/execution.py` | Discrete artifact produced during execution (diff, log, report) |
| `RunTelemetry` | `contracts/execution.py` | Timing and token counts for one run |
| `LaneName`, `BackendName`, `TaskType`, etc. | `contracts/enums.py` | Closed enum sets for all typed fields |

---

## Contract Flow

```
PlanningContext          internal ControlPlane type; pre-validation raw input
    │
    │  planning/proposal_builder.py :: build_proposal()
    │  (enum validation, field mapping, branch/validation policy construction)
    ▼
TaskProposal             frozen Pydantic model; backend-agnostic
    │
    │  routing/client.py :: HttpLaneRoutingClient.select_lane()
    │  (HTTP POST /route → SwitchBoard; deserializes response)
    ▼
LaneDecision             frozen Pydantic model; produced exclusively by SwitchBoard
    │
    │  [bundled as ProposalDecisionBundle in planning/models.py]
    │
    │  execution/handoff.py :: ExecutionRequestBuilder.build()
    │  (merges proposal + decision + runtime context)
    ▼
ExecutionRequest         frozen Pydantic model; adapter input
    │
    │  backends/{kodo,archon,openclaw,direct_local}/adapter.py :: execute()
    ▼
ExecutionResult          frozen Pydantic model; backend-agnostic outcome
    │
    │  observability/service.py :: ExecutionObservabilityService.observe()
    ▼
ExecutionRecord + ExecutionTrace   internal observability types (not part of the public contract chain)
```

---

## Producer / Consumer Table

| Contract | Producer | Consumers |
|---|---|---|
| `TaskProposal` | `planning/proposal_builder.py::build_proposal()` | `HttpLaneRoutingClient` (→ SwitchBoard), `PolicyEngine`, `ExecutionRequestBuilder` |
| `LaneDecision` | SwitchBoard (external service, via `routing/client.py`) | `ExecutionCoordinator`, `PolicyEngine`, `ExecutionRequestBuilder` |
| `ExecutionRequest` | `execution/handoff.py::ExecutionRequestBuilder.build()` | All backend adapters (`kodo`, `archon`, `openclaw`, `direct_local`) |
| `ExecutionResult` | Backend adapters | `ExecutionCoordinator`, `ExecutionObservabilityService` |

---

## Invariants

1. **One definition per contract.** Each of the four contracts is defined in exactly
   one file. No aliases, re-implementations, or competing definitions exist.

2. **SwitchBoard owns `LaneDecision`.** ControlPlane never constructs a `LaneDecision`
   in the live execution path. The only live producer is `HttpLaneRoutingClient`, which
   deserializes the SwitchBoard HTTP response.

3. **`TaskProposal` is produced once per task.** Only `proposal_builder.py::build_proposal()`
   constructs `TaskProposal` in the live path. No adapter, coordinator, or policy engine
   creates one.

4. **No raw dicts cross contract boundaries.** `ExecutionRequest` and `ExecutionResult`
   are always Pydantic models at the adapter boundary. Dicts appear only in serialization
   output (`.model_dump(mode="json")`) and in observability metadata annotations.

5. **Policy gates validate, not re-route.** `PolicyEngine._check_routing_constraints()`
   blocks proposals whose labels conflict with SwitchBoard's decision — it does not
   override or replace the routing decision with a different lane.

6. **`ExecutionCoordinator` constructs `ExecutionResult` only on policy block.**
   In the policy-blocked case, no adapter runs; the coordinator synthesizes a
   `SKIPPED` result. This is the one exception to "adapters produce `ExecutionResult`"
   and is intentional — no execution occurred.

---

## Internal Boundary Types (Not Contracts)

These types carry context within ControlPlane but are not part of the public contract chain:

| Type | File | Purpose |
|---|---|---|
| `PlanningContext` | `planning/models.py` | Raw planning input; pre-validation; converted to `TaskProposal` by `proposal_builder.py` |
| `ProposalBuildResult` | `planning/models.py` | Wraps `TaskProposal` + original context for traceability |
| `ProposalDecisionBundle` | `planning/models.py` | Pairs `TaskProposal` + `LaneDecision` for handoff to execution |
| `ExecutionRuntimeContext` | `execution/handoff.py` | Runtime-resolved paths (workspace, branch) not present in the proposal |
| `PolicyDecision` | `policy/models.py` | Policy gate outcome; consumed by `ExecutionCoordinator` only |
| `ExecutionRecord`, `ExecutionTrace` | `observability/models.py` | Audit log entries; not returned to callers |
