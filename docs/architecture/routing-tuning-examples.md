# Routing Tuning Examples

## Example 1: Local Lane Works For Bounded Tasks

Observed evidence:

- lane: `aider_local`
- backend: `kodo`
- task type: `bug_fix`
- risk: `low`
- 10 retained runs
- 90% success
- fast latency
- strong changed-file evidence

Expected result:

- `BackendComparisonSummary.reliability_class = high`
- `evidence_strength = moderate`
- finding: local lane is reliable for bounded low-risk tasks
- proposal: consider increasing preference for this lane/backend in low-risk bounded work

This remains a proposal only. It does not modify active routing.

## Example 2: Premium Backend Wins On Complex Tasks

Observed evidence:

- `archon @ claude_cli` on `refactor`
- `kodo @ aider_local` on `refactor`
- archon materially outperforms local runs on the same task class

Expected result:

- comparison summaries for both backend/lane pairs
- strong or moderate reliability finding for `archon`
- low reliability finding for local `kodo`
- proposal: increase escalation or premium-backend preference for that scoped task type

Guardrail:

- this does not bypass review requirements or blocked task rules

## Example 3: OpenClaw Acceptable Outcomes But Weak Change Evidence

Observed evidence:

- `openclaw @ claude_cli`
- acceptable success rate
- poor changed-file evidence across runs

Expected result:

- `change_evidence_class = poor`
- reliability finding may still be positive
- contradictory finding should appear
- proposal should recommend restricting OpenClaw to contexts where file-level
  auditability is not critical

This is intentionally not treated as a blanket ban. The recommendation remains
scoped and reviewable.

## Example 4: Small Sample Means Weak Evidence

Observed evidence:

- 3 successful runs for a new backend/task combination

Expected result:

- `evidence_strength = weak`
- only `sparse_data` findings
- no routing recommendation
- report limitations should explicitly say the sample is too small

The system should prefer honest uncertainty over false confidence.
