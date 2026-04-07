# ControlPlane Roadmap

This document tracks what is deferred, what is partially done, and what must be true before each item is unlocked.

---

## Status summary

| Phase | Description | Status |
|-------|-------------|--------|
| 1 | Passive observation and reporting | ✓ complete |
| 2 | Proposal generation (dry-run) | ✓ complete |
| 3 | Bounded automatic proposal creation | ✓ complete |
| 4 | Validation profiles + execution feedback depth | ✓ complete (S8) |
| 5 | Richer signal depth (architecture, benchmark, security) | ✓ complete |
| 6 | Cross-run confidence calibration | deferred |
| 7 | Bounded experiment mode | deferred — guarded |

---

## Phase 4 — Validation profiles + execution feedback depth

Phase 4 has two halves. The first is complete; the second is TODO'd.

### ✓ Done

- `validation_profile` field in task body provenance — five profile constants (`ruff_clean`, `ty_clean`, `tests_pass`, `ci_green`, `manual_review`) mapped to all 12 families; auto-assigned by `CandidateBuilder`; appears in every task body
- `requires_human_approval` flag in task body — derived from `state == "Backlog"`
- `evidence_schema_version` in task body provenance — tracks the evidence bundle format
- `EvidenceBundle` in decision artifact — structured machine-readable evidence for `lint_fix` and `type_fix`

### ✓ Done — execution feedback depth (S8)

**`ExecutionOutcomeDeriver`** (`src/control_plane/insights/derivers/execution_outcome.py`)

Reads retained `control_outcome.json` and `stderr.txt` artifacts from `tools/report/kodo_plane/` to classify failure modes: `timeout_pattern` (≥2 timeout failures), `test_regression` (test-output pattern in validation failures), `validation_loop` (same task failed validation ≥3 times). Wired into `build_insight_service()` in `autonomy_cycle/main.py`.

Per-task validation profile tracking and cycle report profile fields remain as optional future improvements once lint_fix has ≥10 executions in the feedback store.

---

## Phase 5 — Richer signal depth ✓ Complete

All three Phase 5 collectors and derivers are implemented and wired into the pipeline. The observer now collects 14 signals; the insight engine runs 17 derivers.

### ✓ ArchitectureSignalCollector

Static coupling analysis via AST: module size, import depth, circular dependencies. Runs in the observer stage; never modifies the repo. Emits `ArchitectureSignal`.

- Collector: `src/control_plane/observer/collectors/architecture_signal.py`
- Deriver: `src/control_plane/insights/derivers/architecture_drift.py` — emits `arch_drift/coupling_high`, `arch_drift/module_bloat`
- Registered in `build_observer_service()` and `build_insight_service()` in `autonomy_cycle/main.py`
- Consumed by: `arch_promotion` family in the decision engine

### ✓ BenchmarkSignalCollector

Reads pre-existing benchmark output files (pytest-benchmark JSON, hyperfine JSON, custom `report.json`). Never runs benchmarks itself. Detects regressions against prior retained outputs. Emits `BenchmarkSignal`.

- Collector: `src/control_plane/observer/collectors/benchmark_signal.py`
- Deriver: `src/control_plane/insights/derivers/benchmark_regression.py` — emits `benchmark_regression/present`
- Registered in both factory functions

### ✓ SecuritySignalCollector

Reads pip-audit, npm audit, or trivy JSON output from retained artifacts. Never runs audit tools itself. Emits `SecuritySignal` when advisories are present.

- Collector: `src/control_plane/observer/collectors/security_signal.py`
- Deriver: `src/control_plane/insights/derivers/security_vuln.py` — emits `security_vuln/present`
- Registered in both factory functions

All three signals default to `status="unavailable"` when the tool output files are absent, making them safe no-ops on repos that do not use them.

---

## Phase 6 — Confidence calibration

**Why deferred:** Requires a minimum of 3 months of feedback data and ≥20 feedback records per family to produce statistically meaningful calibration. Implementing earlier produces noise.

**Unlock condition:** ≥20 feedback records for lint_fix and type_fix; feedback store running reliably for ≥3 months.

**What it does:** Tracks "when the system said confidence=high for family X, what was the actual acceptance rate?" Surfaces families where the confidence label is systematically miscalibrated (e.g., type_fix high-confidence proposals accepted only 40% of the time). Adds a calibration section to `tune-autonomy` output.

**Where it plugs in:**
- New module: `src/control_plane/tuning/calibration.py` — `ConfidenceCalibrationStore` (TODO comment in `metrics.py`)
- `DecisionContext.min_confidence` becomes per-family once calibration data is available
- Calibration report added to `src/control_plane/entrypoints/tune_autonomy/main.py` output

**Design sketch** (in `src/control_plane/tuning/metrics.py` TODO comment):
```python
class ConfidenceCalibrationStore:
    def record(self, family: str, confidence: str, outcome: str) -> None: ...
    def calibration_for(self, family: str, confidence: str) -> float | None: ...
    def report(self) -> list[CalibrationRecord]: ...
```

---

## Phase 7 — Bounded experiment mode

**Why deferred:** This is the highest-autonomy mode in the system. It must only be enabled after the feedback loop has demonstrated reliable proposal quality for the specific families involved.

**Unlock condition (all must be true):**
- lint_fix has ≥30 feedback records at ≥80% acceptance rate
- type_fix has ≥30 feedback records at ≥80% acceptance rate
- Phase 4 and Phase 5 are stable
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
- New entrypoint or `--experiment` flag in `src/control_plane/entrypoints/autonomy_cycle/main.py` (TODO comment already present)
- Requires explicit env var: `CONTROL_PLANE_EXPERIMENT_MODE=1`
- Validation profile check runs before PR creation; failure abandons the branch silently

**This is the only phase that resembles autoresearch behavior, and it is explicitly bounded to the narrowest possible case.**

---

## What is explicitly out of scope

- Mutation loops (agent modifies code until a metric improves)
- Open-ended "explore the codebase" steps not bounded by a specific signal
- Benchmark-as-objective evaluation (the system evaluates observable repo state, not LLM output quality)
- Auto-merge without human review
- Silent code modification without a reviewable PR or Plane task in the loop
