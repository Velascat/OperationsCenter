---
campaign_id: 3a8d17e2-6f4b-4c91-b0a3-e9c2d5f18764
slug: decompose-autonomy-cycle
phases:
  - implement
  - test
  - improve
repos:
  - OperationsCenter
area_keywords:
  - entrypoints/autonomy_cycle
  - autonomy_cycle
  - pipeline
  - observe
  - insights
  - decide
  - propose
status: active
created_at: 2026-04-18T15:00:00Z
---

## Overview

`src/operations_center/entrypoints/autonomy_cycle/main.py` is a 725-line file containing 7 top-level functions that mix service-factory wiring (observer, insight engine, decision engine), CLI argument parsing, a 4-stage pipeline orchestration duplicated between `main()` and `run_pipeline()`, a 150-line cycle-report writer, and a quiet-diagnosis escalation helper. This campaign extracts cohesive groups into focused submodules inside the `entrypoints/autonomy_cycle/` package, eliminating the pipeline duplication and reducing `main.py` to a thin CLI entrypoint of ≤ 150 lines.

## Goals

1. **Extract service-factory functions into `entrypoints/autonomy_cycle/factories.py`** — Move `build_observer_service`, `build_insight_service`, and `build_decision_service` into a new `factories.py` module. These three functions are pure wiring (instantiate collectors/derivers, return a service object) with no dependency on CLI args or pipeline state. They account for ~80 lines plus the 60-line import block that only they need. Update `main.py` to import from the new module and re-export all three symbols.

2. **Extract reporting functions into `entrypoints/autonomy_cycle/reporting.py`** — Move `_write_cycle_report` (lines 469–631, ~160 lines) and `_write_quiet_diagnosis` (lines 152–245, ~95 lines) into `reporting.py`. These are output-only functions that serialize pipeline results to JSON on disk and trigger escalation webhooks. They have no control-flow coupling to the pipeline stages. Update `main.py` to import and re-export both symbols.

3. **Unify duplicated pipeline logic into `entrypoints/autonomy_cycle/pipeline.py`** — The observe→insights→decide→propose sequence is implemented twice: once in `main()` (lines 300–466) and once in `run_pipeline()` (lines 633–721). Extract a shared `execute_pipeline(settings, client, *, repo_filter, max_candidates, cooldown_minutes, max_create, dry_run, all_families, escalation_webhook, escalation_cooldown_seconds) -> PipelineResult` function into `pipeline.py`. Both `main()` and `run_pipeline()` become thin wrappers: `main()` parses CLI args and prints human-readable output; `run_pipeline()` returns the summary dict for the watcher. Introduce a small `PipelineResult` dataclass to carry the stage artifacts.

## Constraints

- **Backward-compatible imports**: `main.py` must re-export every moved symbol so that existing imports (`from operations_center.entrypoints.autonomy_cycle.main import build_observer_service`, `run_pipeline`, `_write_quiet_diagnosis`) continue to work. Four call sites exist: `worker/main.py`, `test_phase5_collectors.py`, `test_s7.py`, `test_phase5_derivers.py`.
- **No logic changes**: Goals 1 and 2 are pure move-and-import refactors. Goal 3 unifies duplicated code but must preserve identical behavior for both the CLI path and the programmatic `run_pipeline` path.
- **Incremental**: Each goal is a standalone PR-able commit. Tests must pass after each extraction.
- **Goal ordering**: Goals 1 and 2 are independent. Goal 3 depends on both (it imports factories from Goal 1 and reporting from Goal 2).
- **Test files stay as-is**: `test_phase5_collectors.py`, `test_phase5_derivers.py`, and `test_s7.py` should not be restructured in this campaign.

## Success Criteria

- `main.py` is ≤ 150 lines, containing only `main()` (CLI parsing + print output) and `run_pipeline()` (thin delegation).
- `factories.py`, `reporting.py`, and `pipeline.py` each have a single clear responsibility and are independently testable.
- The observe→insights→decide→propose sequence exists in exactly one place (`pipeline.py`), eliminating the current duplication.
- All four existing importers (`worker/main.py`, `test_phase5_collectors.py`, `test_s7.py`, `test_phase5_derivers.py`) work without modification.
- `pytest` passes with no regressions after each goal.
