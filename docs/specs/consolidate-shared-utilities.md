---
campaign_id: a7f3e1c4-8b29-4d6a-b5e2-9c1f0a3d7e84
slug: consolidate-shared-utilities
phases:
  - implement
  - test
  - improve
repos:
  - ControlPlane
area_keywords:
  - artifact_writer
  - decision/rules
  - insights/derivers
  - shared
status: active
created_at: 2026-04-20T00:00:00Z
---

## Overview

The codebase contains five near-identical `artifact_writer.py` files (observer, decision, insights, proposer, tuning) that all follow the same create-run-dir → write-JSON → write-markdown → return-paths pattern, plus a `_slug()` helper copy-pasted identically across four modules. This campaign extracts shared infrastructure into a common utilities layer and rewires consumers, reducing duplication by ~150 lines and creating a single point of change for artifact I/O conventions.

## Goals

1. **Extract `_slug()` into a shared utility** — Create `src/control_plane/shared/text.py` with the canonical `_slug(title: str) -> str` implementation. Update the four call sites (`decision/rules/arch_promotion.py`, `decision/rules/backlog_promotion.py`, `insights/derivers/arch_scheduler.py`, `insights/derivers/backlog_promotion.py`) to import from the shared module. Remove the local definitions. Add unit tests for the shared `_slug` covering edge cases (empty string, unicode, long input truncation).

2. **Extract a base `ArtifactWriter` class** — Create `src/control_plane/shared/artifact_writer.py` containing a base class that encapsulates the common pattern: accept a root `Path`, create `run_dir` from `root / run_id`, write a JSON file via `model_dump_json(indent=2)`, and return the list of written paths. Each domain-specific writer becomes a thin subclass that only defines its default root path, JSON filename, and optional markdown rendering. Refactor the observer, decision, insights, and proposer writers to inherit from it. Leave the tuning writer as a subclass with its multi-file override.

3. **Add tests for the consolidated artifact writer base** — Write `tests/test_artifact_writer_base.py` exercising: directory creation, JSON round-trip via `model_dump_json`, path list correctness, and that the tuning writer's multi-file output still works after refactoring. Verify all existing artifact-writer-related tests still pass after the consolidation.

## Constraints

- The `shared/` package must not import from any domain package (observer, decision, insights, proposer, tuning). Dependencies flow inward only.
- Do not change the on-disk artifact directory structure or filenames — downstream tools read these paths.
- Each writer's `write()` return type (`list[str]`) and semantics must remain identical.
- The tuning writer produces four files and uses a private `_json_dumps` helper; keep that specialization in `tuning/artifact_writer.py` as an override, not in the base.
- Do not introduce any new third-party dependencies.

## Success Criteria

- `grep -rn "def _slug" src/` returns exactly one result (in `shared/text.py`).
- `find src -name "artifact_writer.py"` returns six files (five domain + one shared base), with the domain files each under 30 lines (except tuning, under 50).
- All 1124+ existing tests pass with no modifications to test assertions.
- New tests in `test_artifact_writer_base.py` and `test_shared_text.py` cover the extracted utilities.
- `ruff check src/` reports no new violations.