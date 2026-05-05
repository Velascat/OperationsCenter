# Archon — Subjective Commentary

## Strengths

- Multi-agent workflow graph for tasks that decompose into discrete steps.
- Output normalizes cleanly into ExecutionResult with internal trace
  flattened under `evidence.extensions.internal_trace_summary`.

## Weaknesses

- No per-request RuntimeBinding (G-001). Until upstream patch lands or
  fork happens, every Archon invocation runs whatever Archon's global
  config picks.
- Internal-routing observability is uneven (G-002).

## Good for

- Workflow-shaped tasks where the graph value outweighs the binding cost.

## Bad for

- The Special Use Case (`architecture_design → archon → claude_cli/opus`)
  until G-001 closes. Route through Kodo instead in the interim.
