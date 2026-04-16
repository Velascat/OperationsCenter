---
campaign_id: 7c3e8f1a-2d4b-4e9c-a6f0-8b1c3d5e7f9a
slug: fix-spec-director-test-drift
phases:
  - implement
  - test
repos:
  - ControlPlane
area_keywords:
  - tests/spec_director
  - src/control_plane/spec_director
status: active
created_at: 2026-04-15T00:00:00+00:00
---

## Overview

The spec-director module was migrated from the Anthropic SDK to the `claude` CLI subprocess wrapper (`_claude_cli.call_claude`), but 6 tests still use the old `client=` constructor signature and stale default values. These tests need to be updated to mock `call_claude` instead of passing a mock Anthropic client, and the settings default assertion needs to match the current source of truth.

## Goals

1. **Fix `test_brainstorm.py` (2 failures):** Replace `client=mock_client` construction with the current `model=` -only constructor. Patch `control_plane.spec_director.brainstorm.call_claude` to return the fake spec text directly. Update token assertions to expect `0` (CLI wrapper does not expose token counts).

2. **Fix `test_compliance.py` (3 failures):** Replace `client=mock_client` construction with the current `model=` -only constructor. Patch `control_plane.spec_director.compliance.call_claude` to return the raw JSON string. Preserve existing test logic for truncation, LGTM parsing, and API-failure fallback.

3. **Fix `test_models.py` (1 failure):** Update the `test_spec_director_settings_defaults` assertion for `spec_trigger_queue_threshold` from `3` to `5` to match the current default in `config/settings.py:73`.

4. **Verify full green:** Run `pytest tests/spec_director/ -q` and confirm 44/44 pass with 0 failures and 0 warnings.

## Constraints

- Do **not** modify any production source files — only files under `tests/spec_director/`.
- Mock at the `call_claude` function boundary (e.g., `unittest.mock.patch("control_plane.spec_director.brainstorm.call_claude")`), not at `subprocess.run`.
- Keep tests deterministic — no network calls, no filesystem side effects.
- Do not remove or weaken any existing assertion; only adjust constructor signatures and mock targets.

## Success Criteria

- `pytest tests/spec_director/ -q` exits 0 with all tests passing.
- `ruff check tests/spec_director/` reports no lint errors.
- Each previously-failing test covers the same logical behavior it covered before (parsing, error handling, truncation, defaults).