# ControlPlane Roadmap

Phases 1–4 are complete. This document tracks what is deferred, why, and what must be true before each item is unlocked.

---

## Status summary

| Phase | Description | Status |
|-------|-------------|--------|
| 1 | Passive observation and reporting | ✓ complete |
| 2 | Proposal generation (dry-run) | ✓ complete |
| 3 | Bounded automatic proposal creation | ✓ complete |
| 4 | Validation profiles + structured evidence bundles | ✓ complete |
| 5 | Richer signal depth (architecture, benchmark, security) | deferred |
| 6 | Cross-run confidence calibration | deferred |
| 7 | Bounded experiment mode | deferred — guarded |

---

## Phase 5 — Richer signal depth

**Why deferred:** The current signal set (lint, type, CI, execution health, test continuity, dependency drift, hotspots, todos, validation history) is not yet stabilized in terms of acceptance rate. Adding more signals before the feedback loop has data on the existing ones generates noise before value.

**Unlock condition:** lint_fix and type_fix have ≥10 feedback records each; arch_promotion has been reviewed at least once by a human.

### ArchitectureSignalCollector

**What it does:** Static coupling analysis of the source tree — module size, import depth, circular dependencies. Runs in the observer stage; never modifies the repo.

**Where it plugs in:**
- New collector: `src/control_plane/observer/collectors/architecture_signal.py`
- New signal model: `ArchitectureSignal` in `src/control_plane/observer/models.py`
- Register in: `src/control_plane/entrypoints/autonomy_cycle/main.py` → `build_observer_service()` (TODO comment already present)
- New deriver: `src/control_plane/insights/derivers/architecture_drift.py` — emits `arch_drift/coupling_high`, `arch_drift/module_bloat`
- Register deriver in: `build_insight_service()` (TODO comment already present)
- Consumed by: existing `arch_promotion` rule in `src/control_plane/decision/rules/arch_promotion.py`

**Validation gate:** ArchitectureSignal appearing in snapshot artifacts on at least 3 consecutive runs; at least one arch_promotion proposal reviewed and accepted by a human.

---

### BenchmarkSignalCollector

**What it does:** Reads pre-existing benchmark output files from retained artifacts — pytest-benchmark JSON, hyperfine JSON, custom `report.json`. Never runs benchmarks itself. Detects regressions by comparing current output to prior retained outputs.

**Where it plugs in:**
- New collector: `src/control_plane/observer/collectors/benchmark_signal.py`
- New signal model: `BenchmarkSignal` in `src/control_plane/observer/models.py`
- Register in: `build_observer_service()` (TODO comment already present)
- New deriver: `src/control_plane/insights/derivers/benchmark_regression.py` — emits `benchmark_regression/present`
- Register deriver in: `build_insight_service()` (TODO comment already present)
- New rule: `src/control_plane/decision/rules/performance_regression.py` — new `performance_regression` family
- New validation profile: `BENCHMARK_CLEAN = "benchmark_clean"` in `src/control_plane/decision/validation_profiles.py`

**Validation gate:** At least one benchmark output format present in the repo with ≥5 retained outputs; BenchmarkSignal appearing in snapshot artifacts; at least one performance_regression proposal reviewed by a human.

---

### SecuritySignalCollector

**What it does:** Reads pip-audit, npm audit, or trivy JSON output from retained artifacts. Never runs audit tools itself. Emits a SecuritySignal when known vulnerabilities are present.

**Where it plugs in:**
- New collector: `src/control_plane/observer/collectors/security_signal.py`
- New signal model: `SecuritySignal` in `src/control_plane/observer/models.py`
- Register in: `build_observer_service()` (TODO comment already present)
- New deriver: `src/control_plane/insights/derivers/security_vuln.py` — emits `security_vuln/present`
- Register deriver in: `build_insight_service()` (TODO comment already present)
- New rule: `src/control_plane/decision/rules/security_followup.py` — new `security_followup` family
- New validation profile: `AUDIT_CLEAN = "audit_clean"` in `src/control_plane/decision/validation_profiles.py`

**Validation gate:** At least one audit tool running in CI with output retained; SecuritySignal appearing in snapshots; at least one security_followup proposal reviewed by a human.

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
