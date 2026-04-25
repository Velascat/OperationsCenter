# Upstream Patch Evaluation

## Purpose

This layer provides a disciplined process for evaluating whether any upstream patch,
fork, or deeper native integration work is justified for external systems such
as `archon`, `openclaw`, and `kodo`.

Adapter-first remains the default integration posture until retained evidence shows
that recurring friction is costly enough to justify evaluating upstream work.

## Why Upstream Work Happens Late

The architecture was intentionally designed to move forward without early
upstream modification assumptions.

With enough retained evidence the system can ask better questions:

- is the issue recurring or just annoying?
- does it materially harm execution truth, observability, or reliability?
- is the adapter workaround expensive or brittle?
- would an upstream patch deliver meaningful value without unreasonable
  maintenance or divergence cost?

## Inputs

Evaluation should use normalized, retained evidence such as:

- backend support-check failures
- repeated changed-file uncertainty
- recurring normalization limits
- fallback and escalation patterns
- routing and tuning findings
- repeated policy collisions with backend limitations
- operator pain and wrapper complexity records

The layer should not be driven by vibes or one memorable incident.

## Core Separation

The system keeps these distinct:

1. observed recurring friction
2. evaluation findings
3. patch proposals
4. accepted roadmap work

Patch proposals are not roadmap commitments. They remain review-only outputs.

## Flow

```text
Retained execution / adapter friction evidence
  -> evaluation and classification
  -> findings + workaround assessments
  -> upstream patch proposals
  -> human-reviewed roadmap decisions later
```

## What This Layer Owns

- evaluating recurring integration friction
- classifying frequency, severity, and architectural impact
- comparing adapter workaround cost to patch value
- producing reviewable findings and proposals

## What It Does Not Own

- live backend execution
- routing decisions
- guardrail enforcement
- canonical contract ownership
- automatic patch application
- roadmap commitment

## Evaluation Dimensions

Current classifications are intentionally bounded:

- `FrequencyClass`: `rare`, `occasional`, `recurring`, `persistent`
- `SeverityClass`: `low`, `medium`, `high`, `critical`
- `ArchitecturalImpactClass`: `minor`, `moderate`, `major`
- `WorkaroundComplexityClass`: `simple`, `moderate`, `high`
- `EvidenceStrength`: `weak`, `moderate`, `strong`

Workaround and patch tradeoffs also remain explicit:

- workaround reliability
- maintenance burden
- divergence risk
- expected value

## Recommendation Posture

The evaluator is conservative by default.

Keep adapting locally when:

- evidence is weak
- friction is rare or merely occasional
- architectural impact is minor
- the adapter workaround is simple and stable

Consider upstream patch evaluation when:

- friction is recurring or persistent
- architectural impact is major
- workaround complexity is high or brittle
- maintenance cost is meaningful
- the likely value of native support is explicit

## Output Models

The layer produces:

- `IntegrationFrictionFinding`
- `AdapterWorkaroundAssessment`
- `UpstreamPatchProposal`
- `UpstreamPatchEvaluationReport`

These outputs are explicit and human-reviewable. They do not silently change
integration posture.

## Adapter-First Default

`adapter_first_default=True` remains part of the report model. A proposal does
not overturn that default; it only states that deeper integration work may now
be worth review.
