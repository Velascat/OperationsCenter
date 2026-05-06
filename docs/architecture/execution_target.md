# ExecutionTarget — OC View

**Companion to** `CxRP/docs/spec/execution_target.md` — read that first
for the full asymmetry rationale and the wire-side contract.

## Two shapes, one concept

```text
SwitchBoard / wire           OperationsCenter
─────────────────────        ─────────────────────────
ExecutionTargetEnvelope      BoundExecutionTarget
  (CxRP, flexible)             (OC, strict + bound)
```

The arrow between them is `bind_execution_target(envelope, catalog, policy)`.

## Files

- `cxrp/contracts/execution_target.py` — `ExecutionTargetEnvelope` (wire)
- `operations_center/execution/target.py` — `BoundExecutionTarget` + `BackendProvenance`
- `operations_center/execution/binding.py` — `bind_execution_target()` + typed errors
- `operations_center/contracts/execution.py` — `BoundExecutionTargetMirror` + `BackendProvenanceMirror` (contract-layer mirrors to avoid import cycle)

## Naming canon

Use these phrases exactly. Avoid ambiguous shorthand in shared docs.

| Phrase | Meaning |
|---|---|
| `ExecutionTargetEnvelope` | The CxRP wire-shape execution target intent |
| `BoundExecutionTarget`    | The OC-validated, dispatch-ready target |
| `BackendProvenance`       | OC's record of which fork + ref + patches power a backend |
| `bind_execution_target()` | The narrowing function — wire → bound |

Forbidden in shared docs (informal notes are fine):

```text
"lane/backend/runtime thingy"
"the execution thing"
"execution metadata"     (when you mean BoundExecutionTarget)
"target spec"            (ambiguous; use envelope or bound)
```

## What goes where

| Concern | CxRP | OC |
|---|---|---|
| Lane category vocabulary | `LaneType` enum | (kept as string in BoundExecutionTarget) |
| Backend names | open `str` | `BackendName` enum |
| Executor names | open `str` | `LaneName` enum (legacy name; carries executor identities) |
| Runtime binding | `RuntimeBinding` (validated dataclass) | `RuntimeBindingSummary` (Pydantic mirror) |
| Provenance (fork/ref/patches) | **not present** | `BackendProvenance` |

## Lifecycle

```text
1. SwitchBoard:
     selector.select(proposal) → OC LaneDecision
     adapters.cxrp_mapper.to_cxrp_lane_decision(decision)
       → CxRP LaneDecision with .execution_target = ExecutionTargetEnvelope

2. OperationsCenter receive boundary:
     envelope = decision.execution_target
       (or fall back to scattered legacy fields if envelope absent)

3. Coordinator binding:
     target = bind_execution_target(
         envelope, catalog=executor_catalog, policy=execution_policy,
     )
     # target is a BoundExecutionTarget
     # → unknown backend?  UnknownBackendError
     # → unknown executor? UnknownExecutorError
     # → invalid runtime?  InvalidRuntimeBindingError
     # → policy reject?    PolicyViolationError
     # → missing fork prov? MissingProvenanceError (when require_provenance=True)

4. Adapter dispatch:
     adapter.execute_and_capture(request)
     # request now carries .bound_target as BoundExecutionTargetMirror

5. ExecutionResult:
     records the BoundExecutionTarget for replay/audit
```

## Typed errors

```python
from operations_center.execution.binding import (
    InvalidRuntimeBindingError,
    MissingProvenanceError,
    PolicyViolationError,
    TargetBindError,           # base class
    UnknownBackendError,
    UnknownExecutorError,
)
```

Catch `TargetBindError` to handle all of them; catch the specific ones
when callers need to distinguish (e.g. `UnknownBackendError` is a
"validate the inbound CxRP message" failure; `PolicyViolationError` is
a "this is an admin/governance issue" failure).

## When to require provenance

`bind_execution_target(..., require_provenance=True)` raises
`MissingProvenanceError` if the bound backend has no entry in OC's
`upstream/registry.yaml`. Use this:

- In production CI dispatch flows where every backend MUST be a
  registered fork (Phase 14 strict mode)
- In audits / catalog-validation passes

Skip it (the default `require_provenance=False`) when:

- Running tests with non-forked backends (`direct_local`)
- Local dev where the registry might be incomplete
- Read-only paths that just need to verify routing, not dispatch

## Compatibility with the legacy scattered fields

CxRP's `LaneDecision` and `ExecutionRequest` still carry the
`executor`, `backend`, and `runtime_binding` fields directly. Producers
may emit either shape:

```yaml
# Legacy producer:
executor: claude_cli
backend: kodo

# Phase 2 producer:
execution_target:
  lane: coding_agent
  executor: claude_cli
  backend: kodo

# Both at once (SwitchBoard does this for transition):
executor: claude_cli
backend: kodo
execution_target:
  lane: coding_agent
  executor: claude_cli
  backend: kodo
```

Consumer rule: **prefer `execution_target` when present**, fall back to
the scattered fields for legacy producers.
