---
status: deferred
---
# OperationsCenter Roadmap

This document tracks what is deferred, what is partially done, and what must be true before each item is unlocked.

---

## Status summary

| Milestone | Description | Status |
|-----------|-------------|--------|
| Observation | Passive observation and reporting | ✓ complete |
| Proposals | Proposal generation (dry-run) | ✓ complete |
| Auto-proposals | Bounded automatic proposal creation | ✓ complete |
| Validation | Validation profiles + execution feedback depth | ✓ complete |
| Signal depth | Richer signal depth (architecture, benchmark, security) | ✓ complete |
| Feedback hardening | Learning and feedback loop hardening | ✓ complete |
| Spec-director | Autonomous spec-driven campaign chain | ✓ complete |
| Calibration | Cross-run confidence calibration | deferred |
| Experiment mode | Bounded experiment mode | deferred — guarded |

---

## Validation profiles + execution feedback depth

This milestone has two halves. The first is complete; the second is TODO'd.

### ✓ Done

- `validation_profile` field in task body provenance — five profile constants (`ruff_clean`, `ty_clean`, `tests_pass`, `ci_green`, `manual_review`) mapped to all 12 families; auto-assigned by `CandidateBuilder`; appears in every task body
- `requires_human_approval` flag in task body — derived from `state == "Backlog"`
- `evidence_schema_version` in task body provenance — tracks the evidence bundle format
- `EvidenceBundle` in decision artifact — structured machine-readable evidence for `lint_fix` and `type_fix`

### ✓ Done — execution feedback depth (S8)

**`ExecutionOutcomeDeriver`** (`src/operations_center/insights/derivers/execution_outcome.py`)

Reads retained `control_outcome.json` and `stderr.txt` artifacts from `tools/report/kodo_plane/` to classify failure modes: `timeout_pattern` (≥2 timeout failures), `test_regression` (test-output pattern in validation failures), `validation_loop` (same task failed validation ≥3 times). Wired into `build_insight_service()` in `autonomy_cycle/main.py`.

Per-task validation profile tracking and cycle report profile fields remain as optional future improvements once lint_fix has ≥10 executions in the feedback store.

---

## Richer signal depth ✓ Complete

All three collectors and derivers are implemented and wired into the pipeline. The observer now collects 15 signals; the insight engine runs 22 derivers (including additions from S8 and S9).

### ✓ ArchitectureSignalCollector

Static coupling analysis via AST: module size, import depth, circular dependencies. Runs in the observer stage; never modifies the repo. Emits `ArchitectureSignal`.

- Collector: `src/operations_center/observer/collectors/architecture_signal.py`
- Deriver: `src/operations_center/insights/derivers/architecture_drift.py` — emits `arch_drift/coupling_high`, `arch_drift/module_bloat`
- Registered in `build_observer_service()` and `build_insight_service()` in `autonomy_cycle/main.py`
- Consumed by: `arch_promotion` family in the decision engine

### ✓ BenchmarkSignalCollector

Reads pre-existing benchmark output files (pytest-benchmark JSON, hyperfine JSON, custom `report.json`). Never runs benchmarks itself. Detects regressions against prior retained outputs. Emits `BenchmarkSignal`.

- Collector: `src/operations_center/observer/collectors/benchmark_signal.py`
- Deriver: `src/operations_center/insights/derivers/benchmark_regression.py` — emits `benchmark_regression/present`
- Registered in both factory functions

### ✓ SecuritySignalCollector

Reads pip-audit, npm audit, or trivy JSON output from retained artifacts. Never runs audit tools itself. Emits `SecuritySignal` when advisories are present.

- Collector: `src/operations_center/observer/collectors/security_signal.py`
- Deriver: `src/operations_center/insights/derivers/security_vuln.py` — emits `security_vuln/present`
- Registered in both factory functions

All three signals default to `status="unavailable"` when the tool output files are absent, making them safe no-ops on repos that do not use them.

---

## Autonomous Spec-Driven Campaign Chain ✓ Complete

A fully autonomous spec-driven development chain giving OperationsCenter a sixth watcher role (`spec`).

### What was built

**Trigger detection** (`spec_director/trigger.py`): Priority-ordered: drop-file (`state/spec_director_trigger.md`) > Plane label (`spec-director: trigger`) > queue drain (board goes quiet). Each trigger passes optional operator seed text to the brainstorm step.

**Brainstorm service** (`spec_director/brainstorm.py`): Direct Anthropic API call (claude-opus-4-6) given context bundle (git log, existing specs index, board summary, insight snapshot). Produces a full spec document with YAML front matter (`campaign_id`, `slug`, `phases`, `area_keywords`).

**Spec writer** (`spec_director/spec_writer.py`): Writes spec to `docs/specs/<slug>.md`; archives expired specs after 90 days.

**Campaign builder** (`spec_director/campaign_builder.py`): Converts spec phases into Plane tasks — `implement` phase → `goal` task kinds, `test` phase → `test_campaign`, `improve` phase → `improve_campaign`. Enforces a max-tasks cap per campaign.

**Campaign state** (`spec_director/state.py`): Atomic read/write of `state/campaigns/active.json` (`CampaignStateManager`). Tracks `status`, `last_progress_at`, `spec_revision_count` per campaign.

**Suppressor** (`spec_director/suppressor.py`): Blocks heuristic `propose` candidates that overlap an active campaign's `area_keywords`. Fail-open: returns `False` on any load error.

**Recovery service** (`spec_director/recovery.py`): Stall detection (24 h), spec revision via Anthropic API (budget 3), abandon + self-cancel (72 h).

**Spec compliance reviewer** (`spec_director/compliance.py`): Direct Anthropic API call (claude-sonnet-4-6) that compares PR diff against the spec and returns a structured JSON verdict (`LGTM`/`CONCERNS`/`FAIL`). Wired into the reviewer watcher as an upstream step before kodo self-review.

**New task kinds**: `test_campaign` (→ `kodo --test`), `improve_campaign` (→ `kodo --improve`). Both are claimed by their corresponding role workers via `ROLE_TASK_KINDS` in `worker/main.py`.

**Entrypoint** (`entrypoints/spec_director/main.py`): Polling loop, `--once` flag for supervised runs. `watch --role spec` registered in `scripts/operations-center.sh`.

---

## Confidence calibration — Deferred

**Why deferred:** Requires a minimum of 3 months of feedback data and ≥20 feedback records per family to produce statistically meaningful calibration. Implementing earlier produces noise.

**Unlock condition:** ≥20 feedback records for lint_fix and type_fix; feedback store running reliably for ≥3 months.

**What it does:** Tracks "when the system said confidence=high for family X, what was the actual acceptance rate?" Surfaces families where the confidence label is systematically miscalibrated (e.g., type_fix high-confidence proposals accepted only 40% of the time). Adds a calibration section to `tune-autonomy` output.

**Where it plugs in:**
- New module: `src/operations_center/tuning/calibration.py` — `ConfidenceCalibrationStore` (TODO comment in `metrics.py`)
- `DecisionContext.min_confidence` becomes per-family once calibration data is available
- Calibration report added to `tune-autonomy` output

**Design sketch** (in `src/operations_center/tuning/metrics.py` TODO comment):
```python
class ConfidenceCalibrationStore:
    def record(self, family: str, confidence: str, outcome: str) -> None: ...
    def calibration_for(self, family: str, confidence: str) -> float | None: ...
    def report(self) -> list[CalibrationRecord]: ...
```

---

## Bounded experiment mode — Deferred (guarded)

**Why deferred:** This is the highest-autonomy mode in the system. It must only be enabled after the feedback loop has demonstrated reliable proposal quality for the specific families involved.

**Unlock condition (all must be true):**
- lint_fix has ≥30 feedback records at ≥80% acceptance rate
- type_fix has ≥30 feedback records at ≥80% acceptance rate
- Validation profiles and signal depth are stable
- The operator has reviewed a dry-run of the experiment path end-to-end
- A hard rollback procedure is defined and documented

**What it does:** For tier-2 style families (`lint_fix`, `type_fix`) with `validation_profile=ruff_clean` or `ty_clean`, skips Plane task creation and instead:
1. Creates a branch from `base_branch`
2. Applies the fix (`ruff --fix` or `ty --fix`) directly
3. Runs the validation profile check
4. If clean: opens a PR for human review (never auto-merges)
5. If not clean: abandons the branch and falls back to normal task creation

**Hard constraints:**
- Max 5 files changed per experiment run
- Max 200 lines changed per experiment run
- No deletions
- Human still reviews and merges the PR; there is no auto-merge path

**Where it plugs in:**
- New entrypoint or `--experiment` flag in `src/operations_center/entrypoints/autonomy_cycle/main.py` (TODO comment already present)
- Requires explicit env var: `OPERATIONS_CENTER_EXPERIMENT_MODE=1`
- Validation profile check runs before PR creation; failure abandons the branch silently

**This is the only mode that resembles autoresearch behavior, and it is explicitly bounded to the narrowest possible case.**

---

## What is explicitly out of scope

- Mutation loops (agent modifies code until a metric improves)
- Open-ended "explore the codebase" steps not bounded by a specific signal
- Benchmark-as-objective evaluation (the system evaluates observable repo state, not LLM output quality)
- Auto-merge without human review
- Silent code modification without a reviewable PR or Plane task in the loop
