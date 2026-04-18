---
campaign_id: a7f3e1c4-9b2d-4e8a-b6d1-3c5f0a8e72d9
slug: decompose-reviewer-main
phases:
  - implement
  - test
  - improve
repos:
  - ControlPlane
area_keywords:
  - entrypoints/reviewer
  - reviewer
  - pr_review
  - self_review
status: active
created_at: 2026-04-18T00:00:00Z
---

## Overview

`src/control_plane/entrypoints/reviewer/main.py` is a 1,472-line monolith containing 30+ top-level functions that mix spec-compliance extraction, proposal feedback persistence, rejection-pattern tracking, PR description quality checks, bot-comment management, merge/rebase orchestration, self-review processing, human-review processing, CI-fix handling, and the main polling loop. This campaign extracts cohesive function groups into focused submodules inside the `entrypoints/reviewer/` package, turning the single file into a package of ≤ 400-line modules with a thin `main.py` that re-exports the public API.

## Goals

1. **Extract spec-compliance and proposal-feedback helpers** — Move `_get_spec_campaign_id`, `_get_spec_file`, `_get_task_phase`, `_get_spec_coverage_hint`, `_run_spec_compliance`, `_write_proposal_feedback`, `_extract_rejection_patterns`, `_record_rejection_patterns`, `load_rejection_patterns`, and `_check_pr_description_quality` into `entrypoints/reviewer/feedback.py`. These are stateless utility functions with no cross-dependencies on the review state machine. Update imports in `main.py` to re-export from the new module.

2. **Extract bot-comment and PR interaction helpers** — Move `_bot_marker`, `_post_bot_comment`, `_is_bot_comment`, `_concerns_indicate_merge_conflict`, `_try_auto_rebase`, `_load_pr_states`, and `_merge_and_finalize` into `entrypoints/reviewer/pr_ops.py`. These functions manage the mechanical GitHub PR interactions (commenting, merging, rebasing, state file I/O) and form a cohesive group.

3. **Extract review-phase state machine functions** — Move `_process_self_review`, `_escalate_to_human`, `_process_human_review`, `_requeue_as_goal`, `_handle_dependency_conflict`, `_process_awaiting_ci`, and `_process_pr_state` into `entrypoints/reviewer/review_phases.py`. These are the core state-machine transition handlers that drive the review lifecycle. They depend on helpers from Goals 1 and 2, so this extraction comes last.

## Constraints

- **Backward-compatible imports**: `main.py` must re-export every moved symbol so that `from control_plane.entrypoints.reviewer.main import X` continues to work for external callers and tests. Use `from .feedback import *` style re-exports.
- **No logic changes**: Each goal is a pure move-and-import refactor. Do not rename functions, change signatures, or alter behavior.
- **Incremental**: Each goal is a standalone PR-able commit. Tests must pass after each extraction.
- **Goal ordering matters**: Goal 3 depends on Goals 1 and 2 because the review-phase functions call helpers extracted in those goals. Goals 1 and 2 are independent of each other.
- **Shared constants stay in `main.py`**: Module-level constants (`PR_REVIEW_STATE_DIR`, `PROPOSAL_FEEDBACK_DIR`, `REVIEW_TIMEOUT_SECONDS`, `MAX_CI_FIX_ATTEMPTS`, compiled regexes) remain in `main.py` until the improve phase, where they can be collected into a shared `_constants.py`.
- **Test files stay as-is**: `test_reviewer_entrypoint.py` and `test_self_review_verdict.py` should not be split in this campaign.

## Success Criteria

- `reviewer/main.py` is under 500 lines (down from 1,472), containing only `backfill_pr_reviews`, `run_review_loop`, `main`, constants, and re-exports.
- Three new modules exist: `feedback.py`, `pr_ops.py`, `review_phases.py`.
- `python -m pytest tests/test_reviewer_entrypoint.py tests/test_self_review_verdict.py tests/spec_director/test_reviewer_compliance.py` passes with zero import errors and no test changes.
- `ruff check src/control_plane/entrypoints/reviewer/` reports no new lint violations.
- Every previously-importable symbol from `reviewer.main` is still importable from that path.