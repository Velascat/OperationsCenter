---
campaign_id: a4f82c19-7e3d-4b1a-ae56-9d3f01c8b275
slug: test-operational-entrypoints
phases:
  - implement
  - test
  - improve
repos:
  - control-plane
area_keywords:
  - entrypoints
  - ci_monitor
  - feedback
  - pipeline_trigger
  - error_ingest
status: active
created_at: 2026-04-19T14:30:00Z
---

## Overview

The operational entrypoints (`ci_monitor`, `feedback`, `pipeline_trigger`, `error_ingest`) collectively span ~1,100 lines of production logic but have little or no dedicated unit test coverage. `ci_monitor` has zero tests, the `feedback` CLI subcommands are untested, `pipeline_trigger` has only 3 snapshot-level tests, and `error_ingest` only tests its dedup helpers. This campaign adds focused unit tests for the core logic functions in each module, without requiring live services or network access.

## Goals

1. **Add `tests/test_ci_monitor.py` covering `ci_monitor/main.py` core logic**: Test `_build_fix_pr_description` output format, `_pr_is_awaiting_ci` with and without state files, `_load_ci_fix_state`/`_save_ci_fix_state` round-trip, and `run_ci_monitor_cycle` with a mocked `GitHubPRClient` and `PlaneClient` (verify it creates tasks for failing PRs, skips non-plane branches, skips already-tracked head SHAs, and skips PRs in `awaiting_ci` phase). Target: ≥8 test cases.

2. **Add `tests/test_feedback_cli.py` covering `feedback/main.py` subcommands**: Test `cmd_record` (creates file, respects `--force`, rejects invalid outcomes, writes calibration when `--confidence`/`--family` provided), `cmd_list` (empty dir, multiple records, `--limit`), and `cmd_show` (existing vs missing task). All tests use `tmp_path` to isolate the feedback directory. Target: ≥7 test cases.

3. **Expand `pipeline_trigger` tests in a new `tests/test_pipeline_trigger.py`**: Move and extend the 3 existing tests from `test_s9.py` (keep originals for backward compat). Add tests for `_run_pipeline` (mock `subprocess.run`, verify command construction with/without `--execute`, verify timeout handling), `run_trigger_loop` (mock sleep/snapshot to run 2 iterations verifying debounce logic), and `_get_trigger_sources` (verify it discovers FETCH_HEAD paths from settings). Target: ≥6 test cases.

4. **Add `tests/test_error_ingest_webhook.py` covering the HTTP handler and tail watcher**: Test `_make_webhook_handler` by sending mock POST requests via `http.client` to a locally-bound `ThreadingHTTPServer` (verify 200 on valid payload, 404 on wrong path, 400 on bad JSON, duplicate detection returns `{"status": "duplicate"}`). Test `_tail_log_file` by writing lines to a temp file and verifying task creation is called for matching lines and skipped for non-matching lines. Target: ≥6 test cases.

## Constraints

- **No network or live service dependencies**: All tests must mock external clients (`PlaneClient`, `GitHubPRClient`, `httpx`, `subprocess`). Use `tmp_path` for any file I/O.
- **Do not modify production code**: This campaign is test-only. If a function is hard to test, note it as a follow-up but do not refactor production code.
- **Follow existing test patterns**: Use `pytest` fixtures, `unittest.mock.patch`, and `tmp_path`. Mirror the style of `test_s7.py` and `test_s8.py` for mock setup.
- **One test file per goal**: Keep test files focused on a single entrypoint module.
- **Patch the feedback `PROPOSAL_FEEDBACK_DIR` constant**: The feedback module uses a module-level `Path` constant; patch it to `tmp_path` in tests rather than relying on cwd.

## Success Criteria

- `pytest tests/test_ci_monitor.py tests/test_feedback_cli.py tests/test_pipeline_trigger.py tests/test_error_ingest_webhook.py` passes with 0 failures.
- Combined new test count is ≥27 across the four files.
- No existing tests are broken (`pytest tests/` passes with the same count or higher).
- Each new test file imports only from its target entrypoint module and standard test utilities (no cross-entrypoint imports).
