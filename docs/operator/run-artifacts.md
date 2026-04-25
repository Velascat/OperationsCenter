# Run Artifacts

Every execution run persists the four canonical contracts to disk for inspection
and post-mortem analysis.

---

## Location

```
~/.console/operations_center/runs/<run_id>/
```

Each `<run_id>` is the `ExecutionResult.run_id` (UUID4), set once at
`ExecutionRequest` construction and threaded through every contract.

---

## Files

| File | Contract | Contents |
|---|---|---|
| `proposal.json` | `TaskProposal` | Goal, target, constraints, validation profile |
| `decision.json` | `LaneDecision` | Selected lane and backend, routing confidence |
| `execution_request.json` | `ExecutionRequest` | Resolved request handed to the adapter |
| `result.json` | `ExecutionResult` | Status, success, failure category, artifacts |
| `run_metadata.json` | Summary | Cross-contract IDs, lane/backend, timing |

---

## Inspection

```bash
# List recent runs (newest last)
ls -lt ~/.console/operations_center/runs/

# Inspect a specific run
RUN=<run_id>
cat ~/.console/operations_center/runs/$RUN/run_metadata.json | python3 -m json.tool
cat ~/.console/operations_center/runs/$RUN/result.json | python3 -m json.tool

# Find all failed runs
grep -rl '"success": false' ~/.console/operations_center/runs/
```

---

## `run_metadata.json` fields

```json
{
  "run_id": "...",
  "proposal_id": "...",
  "decision_id": "...",
  "selected_lane": "claude_cli",
  "selected_backend": "kodo",
  "status": "success",
  "success": true,
  "executed": true,
  "written_at": "2026-04-24T10:00:00+00:00"
}
```

`failure_category` is included only when `success` is `false`.

---

## Partial runs

When execution fails before completion (e.g., SwitchBoard unreachable after
the proposal was built), `RunArtifactWriter.write_partial()` is called with
whatever contracts exist. The resulting `run_metadata.json` will contain:

```json
{
  "partial": true,
  "reason": "SwitchBoard unreachable",
  ...
}
```

---

## Disabling artifact writes

Pass `--no-artifacts` to the execute entrypoint to skip disk writes:

```bash
python -m operations_center.entrypoints.execute.main \
  --config operations_center.yaml \
  --bundle bundle.json \
  --workspace-path ./workspace \
  --task-branch auto/my-task \
  --no-artifacts
```

---

## Programmatic access

```python
from operations_center.execution.artifact_writer import RunArtifactWriter

writer = RunArtifactWriter()  # defaults to ~/.console/operations_center/runs/
written = writer.write_run(
    proposal=bundle.proposal,
    decision=bundle.decision,
    request=request,
    result=result,
    executed=True,
)
# written: list of absolute file paths
```
