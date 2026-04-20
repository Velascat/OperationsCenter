---
campaign_id: d7e4a91b-3c6f-48a2-b5d0-1f8e72c6a439
slug: test-github-pr-adapter
phases:
  - implement
  - test
  - improve
repos:
  - control-plane
area_keywords:
  - adapters
  - github_pr
status: active
created_at: 2026-04-19T18:00:00Z
---

## Overview

`GitHubPRClient` in `adapters/github_pr.py` (341 lines, 20+ methods) is the primary integration point for all PR creation, merging, review checking, and CI status queries — yet it has zero dedicated unit tests. Six of its methods silently swallow exceptions via broad `except Exception` handlers that return empty defaults (`[]`, `""`, `None`, `False`), masking API failures and making debugging difficult. This campaign adds comprehensive unit tests and then narrows the exception handling to specific, logged failure modes.

## Goals

1. **Add `tests/test_github_pr_client.py` covering the core CRUD methods**: Test `create_pr`, `get_pr`, `merge_pr`, `delete_branch`, `close_pr`, `create_and_merge`, `post_comment`, and `update_pr_description` by mocking `httpx.request` responses. Verify correct URL construction, HTTP method selection, request body contents, and that `httpx.HTTPStatusError` propagates on non-2xx responses. Test `owner_repo_from_clone_url` with https, ssh, and invalid URLs. Target: ≥12 test cases.

2. **Add tests for query and list methods with rate-limit handling**: Test `_request` rate-limit retry logic by simulating a 429 response followed by a 200, verifying `Retry-After` header is respected and that the method gives up after `_GH_RATE_LIMIT_MAX_RETRIES` attempts. Test the low-quota warning log when `X-RateLimit-Remaining` drops below threshold. Test `list_open_prs`, `list_pr_files`, `list_pr_comments`, `list_pr_review_comments`, `list_pr_reviews`, `get_pr_reactions`, `get_comment_reactions`, `get_check_runs`, and `get_pr_diff` with mocked responses. Target: ≥10 test cases.

3. **Add tests for methods with broad exception swallowing and verify failure behaviour**: Test `get_failed_checks`, `list_pr_files`, `get_pr_diff`, `get_mergeable`, `pr_has_changes_requested`, and `get_branch_head` — the six methods that catch `except Exception`. For each, verify that (a) the happy path returns correct data and (b) an `httpx.HTTPStatusError` or `httpx.ConnectError` is caught and the documented default is returned. Test `get_failed_checks` filter logic with `ignored_checks` and various check-run conclusion values. Target: ≥10 test cases.

4. **Narrow broad exception handlers to specific exception types and add logging**: Replace the six `except Exception` handlers with `except (httpx.HTTPStatusError, httpx.ConnectError, httpx.TimeoutException)` and add `_logger.debug(...)` calls with the exception details. This makes the failure modes explicit and observable without changing the return-default-on-error contract. Update tests to confirm that unexpected exceptions (e.g., `TypeError`, `KeyError`) now propagate instead of being swallowed.

## Constraints

- **Mock at the `httpx.request` level**: All tests mock `httpx.request` (or use `respx` if already in dev-dependencies) so no network calls occur. Do not patch internal `_request` — test through the public API.
- **Do not change public method signatures or return types**: The error-narrowing in Goal 4 must preserve the existing return-default-on-error contract for the six affected methods.
- **Follow existing test patterns**: Use `pytest`, `unittest.mock.patch`, and fixtures consistent with the rest of the test suite.
- **Execute Goal 4 only after Goals 1–3 pass**: The narrowing refactor must be validated against the new test suite, not done blindly.
- **One test file**: All tests go in `tests/test_github_pr_client.py` since they target a single adapter class.

## Success Criteria

- `pytest tests/test_github_pr_client.py` passes with 0 failures and ≥32 test cases.
- No existing tests are broken (`pytest tests/` passes with the same count or higher).
- All six previously-broad `except Exception` handlers in `github_pr.py` are replaced with specific httpx exception types.
- Each narrowed handler includes a `_logger.debug(...)` call that logs the exception.
- Unexpected exceptions (e.g., `KeyError`) in the six methods now propagate to the caller — verified by at least one test per method.
