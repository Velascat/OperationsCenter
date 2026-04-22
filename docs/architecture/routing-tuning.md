# Routing And Backend Tuning

## Purpose

Phase 13 adds an evidence-driven tuning layer for routing and backend strategy.
It exists to improve routing defaults from retained execution outcomes rather
than static assumptions, while staying bounded, inspectable, and subordinate to
explicit policy.

This layer does not execute work, does not replace canonical execution truth,
and does not silently mutate active SwitchBoard policy.

## Inputs

The tuning layer analyzes retained normalized evidence:

- `ExecutionRecord`
- `ExecutionTrace`
- normalized `ExecutionResult`
- retained artifact indexes
- validation summaries
- changed-file evidence states
- route selections, fallback, and escalation outcomes

Backend-native raw detail is diagnostic only. It is not the primary tuning
input unless explicitly referenced from normalized evidence.

## Core Separation

Three things stay separate in both code and output:

1. Current active routing policy
   `SwitchBoard` and accepted config decide live routing.
2. Observed historical evidence
   retained `ExecutionRecord` data captures what actually happened.
3. Proposed strategy changes
   tuning emits reviewable `RoutingTuningProposal` objects only.

The report model makes this explicit through:

- `active_policy_reference`
- `observed_evidence_source`
- `proposed_changes_status`
- `policy_guardrails_applied`

## Flow

```text
ExecutionRecords / ExecutionTraces
  -> analysis
  -> comparison summaries + findings
  -> tuning proposals
  -> reviewed policy/config updates later
```

## What The Tuning Layer Owns

- deriving routing-relevant evidence summaries
- comparing lanes and backends across retained runs
- labeling evidence strength honestly
- producing bounded findings
- producing review-only recommendations

## What It Does Not Own

- execution truth
- backend invocation
- policy enforcement
- live route selection
- silent policy mutation

## Comparison Dimensions

Current comparison summaries are intentionally bounded and explicit:

- success rate
- failure rate
- partial-result rate
- timeout rate
- validation pass rate
- validation skip rate
- latency class
- reliability class
- changed-file evidence class
- sample size
- evidence strength

Classes are coarse on purpose:

- `EvidenceStrength`: `weak`, `moderate`, `strong`
- `LatencyClass`: `fast`, `medium`, `slow`, `unknown`
- `ReliabilityClass`: `low`, `medium`, `high`
- `ChangeEvidenceClass`: `poor`, `partial`, `strong`, `unknown`

## Findings And Recommendations

`StrategyFinding` captures a bounded statement about observed evidence.
Examples:

- reliable backend for a scoped task class
- poor changed-file evidence
- excessive validation skips
- slow latency
- contradictory signals such as high success with weak change evidence

`RoutingTuningProposal` is review-only and cannot auto-approve changes in this
phase. Each proposal includes:

- explicit evidence strength
- source finding ids
- policy guardrail reminders
- `requires_review=True`

## Policy Bounds

The tuning layer cannot override:

- repo policy
- safety guardrails
- blocked task, path, or tool rules
- review gates
- active SwitchBoard policy

The implementation encodes these bounds in report-level
`policy_guardrails_applied` and proposal-level `policy_guardrails`.

## Recommendation Posture

Recommendations are conservative. They are meant to suggest:

- backend preference adjustments for a bounded scope
- local-first threshold changes
- escalation threshold changes
- validation requirement tightening
- backend restrictions when auditability is weak

They are not live policy changes. Any accepted strategy change must be applied
later through a separate reviewed config or policy flow.

## Evidence Honesty

The tuning layer does not pretend sparse evidence is strong evidence.

- fewer than 8 samples yields `weak` evidence
- weak evidence produces `sparse_data` findings only
- contradictory signals are surfaced in findings and report limitations
- missing duration or validation coverage is called out in report limitations

## Main API

Primary entry point:

```python
from control_plane.tuning import StrategyTuningService

service = StrategyTuningService.default()
report = service.analyze(records)
comparisons = service.compare(records)
proposals = service.recommend(report)
```

These APIs analyze evidence and emit recommendations. They do not mutate the
current routing policy.
