# Anti-Collapse Invariant

## Purpose

The anti-collapse invariant prevents the OperationsCenter from becoming
self-modifying, non-deterministic, or un-auditable by enforcing a strict
one-way promotion pipeline between artifact evidence and any resulting action.

Without this invariant, calibration output could drift into executable policy.
Findings could begin prescribing config mutations. Recommendations could
trigger runtime changes without human oversight. The system would collapse into
a feedback loop with no stable reference point.

This document defines the invariant, explains why it matters, and describes
where enforcement lives in the code.

---

## One-Way Promotion Pipeline

```
artifact data  →  findings  →  recommendations  →  CalibrationDecision  →  applied change
    (Phase 7)     (Phase 8)      (Phase 8)        (human-approved gate)    (external)
```

Each stage is strictly downstream of the previous one. Information flows in
one direction only. No stage may read from or write to a downstream stage's
output.

### Core statement

```
Calibration outputs are evidence for humans and task creation,
not executable policy.
```

---

## What Collapse Looks Like

A collapsed system would:

- Read calibration findings and automatically adjust runtime config
- Apply recommendations without requiring a human decision
- Allow dispatch or routing modules to import `behavior_calibration`
- Generate findings that issue commands rather than describe observations
- Allow a recommendation's `suggested_action` to be executed directly

Any of these patterns breaks the audit trail and makes the system's behavior
depend on its own outputs — which are themselves derived from its behavior.

---

## Rules

### Rule 1 — Directionality

```
artifact_index → behavior_calibration   ✓ allowed
behavior_calibration → runtime          ✗ forbidden
behavior_calibration → config           ✗ forbidden
behavior_calibration → dispatch         ✗ forbidden
```

Runtime packages that must not import `behavior_calibration`:

- `audit_dispatch`
- `run_identity`
- `managed_repos`
- `config`
- `routing`
- `planning`
- `policy`
- `execution`
- `observability`

Enforced by AST scan in `TestImportBoundary`.

### Rule 2 — Findings Are Facts

A `CalibrationFinding` describes what was observed in the artifact index.
It must not prescribe action.

Constraints:
- All fields are immutable (Pydantic `frozen=True`)
- No `apply`, `execute`, or `mutate` attributes
- No `config_patch`, `runtime_patch`, or `auto_apply` fields
- `artifact_ids` must reference manifest artifact identifiers

### Rule 3 — Recommendations Are Advisory

A `CalibrationRecommendation` suggests what a human should consider.
It must not be executable.

Hard invariants:
- `requires_human_review = True` always — enforced by `enforce_requires_human_review()`
- `supporting_finding_ids` must be non-empty — recommendations without evidence are forbidden
- No mutation fields (`auto_apply`, `config_patch`, `runtime_patch`, etc.)
- All fields are immutable (Pydantic `frozen=True`)

Validated at runtime by `validate_recommendation_structure()` and
`validate_all_recommendations()` in `guardrails.py`.

### Rule 4 — Promotion Barrier

No recommendation may be applied without going through a `CalibrationDecision`.

`CalibrationDecision` is the gate:
- Created by a human or an authorized external system
- Records who approved, when, and what recommendation IDs are covered
- References the artifact representing the applied change (PR, task, issue)
- Never created automatically by `analyze_artifacts()` or any calibration code

The `CalibrationDecision` model is defined in
`behavior_calibration/decision.py` but is never instantiated by the runtime.

### Rule 5 — No Auto-Apply

These function names are forbidden in the `behavior_calibration` package:

```
auto_apply_recommendations()
self_tuning_runtime()
apply_recommendation()
execute_recommendation()
auto_tune()
```

Enforced by `TestNoAutoApply` via AST function name scan.

### Rule 6 — Import Boundary Enforcement

AST-level tests in `TestImportBoundary` scan every `.py` file in each runtime
package and fail if any file imports from `behavior_calibration`.

### Rule 7 — Schema Separation

Each layer has its own distinct, non-overlapping model:

| Layer | Model | Mutable? |
|-------|-------|----------|
| Artifact data (Phase 7) | `IndexedArtifact` | No (frozen dataclass) |
| Finding (Phase 8) | `CalibrationFinding` | No (Pydantic frozen) |
| Recommendation (Phase 8) | `CalibrationRecommendation` | No (Pydantic frozen) |
| Decision (gate) | `CalibrationDecision` | No (Pydantic frozen) |

No shared mutable model. No dual-purpose fields.

---

## Valid vs Invalid Flows

### Valid

```
analyze_artifacts(input) → BehaviorCalibrationReport
    .findings: [CalibrationFinding(severity=ERROR, summary="run failed")]
    .recommendations: [CalibrationRecommendation(suggested_action="re-run after fix")]

# Human reviews the report, decides to act
decision = CalibrationDecision(
    source_recommendation_ids=[rec.recommendation_id],
    approved_by="alice",
    applied_changes_reference="https://github.com/org/repo/issues/123",
)

# Human or authorized system opens the issue, makes the config change
```

### Invalid — recommendation auto-applied

```python
# FORBIDDEN: this collapses recommendations into executable policy
for rec in report.recommendations:
    apply_recommendation(rec)   # ← does not exist, must not exist
```

### Invalid — runtime reads calibration

```python
# FORBIDDEN: runtime must not depend on calibration output
from operations_center.behavior_calibration import analyze_artifacts  # ← in dispatch module
result = analyze_artifacts(inp)
if result.has_errors:
    adjust_policy()   # ← non-deterministic, un-auditable
```

### Invalid — finding issues command

```python
# FORBIDDEN: findings describe observations, not prescriptions
CalibrationFinding(
    summary="should disable VoiceOverStage",  # ← imperative language
    config_patch={"stages": {"VoiceOverStage": False}},  # ← mutation field
)
```

---

## Human-in-the-Loop Requirement

The `CalibrationDecision` gate exists specifically to require a human decision
before any recommendation can influence behavior.

There is no code path from `CalibrationRecommendation` to any config, manifest,
or runtime state that bypasses a human creating a `CalibrationDecision`.

This is not a policy preference — it is a structural constraint enforced by:

1. The absence of any `apply` or `execute` method on `CalibrationRecommendation`
2. The `requires_human_review = True` invariant validated by `guardrails.py`
3. The import boundary tests that prevent runtime code from reading calibration output
4. The `TestNoAutoApply` AST scan that forbids auto-apply function names

---

## Enforcement Locations

| Rule | Where Enforced |
|------|----------------|
| Import boundary | `TestImportBoundary` (AST scan) |
| Findings are facts | `CalibrationFinding` model constraints + `TestFindingsAreFacts` |
| Recommendations are advisory | `guardrails.py` + `TestRecommendationsAreAdvisory` |
| Promotion barrier | `CalibrationDecision` model + `TestCalibrationDecision` |
| No auto-apply | `TestNoAutoApply` (AST scan) |
| Schema separation | `TestSchemaSeparation` |

All tests live in `tests/unit/behavior_calibration/test_anti_collapse.py`.

---

## Answering the Reviewer Questions

**Can recommendations change behavior automatically?**
No. Recommendations are frozen Pydantic models with no `apply` or `execute`
method. The guardrail validates `requires_human_review = True` at runtime.

**Can runtime modules read recommendations?**
No. AST tests verify that `audit_dispatch`, `run_identity`, `managed_repos`,
`config`, `routing`, `planning`, `policy`, `execution`, and `observability`
contain no imports of `behavior_calibration`.

**What is required to apply a recommendation?**
A human must create a `CalibrationDecision` that references the recommendation
ID, records who approved it, and references the applied-change artifact (PR,
issue, task).

**Where is the boundary enforced?**
In code (`guardrails.py`, model immutability), in tests (AST scans, guardrail
validation), and in this document.
