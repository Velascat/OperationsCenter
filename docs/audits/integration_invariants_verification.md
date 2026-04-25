# Integration Invariants Verification
**Date:** 2026-04-25
**Scope:** OperationsCenter, SwitchBoard, WorkStation, OperatorConsole
**Type:** Verification-only — no code changes

---

## 1. Final Verdict

**READY FOR DAILY USE**

All 9 invariants pass. All 13 phases (0–12) are verified. No boundary violations found in active source code. No legacy identifiers (9router, ControlPlane, fob) present in any active source tree. All four test suites pass cleanly.

---

## 2. Invariant Status Table

| # | Invariant | Status | Evidence |
|---|-----------|--------|----------|
| 1 | Contracts defined once, in OperationsCenter | **PASS** | `TaskProposal`, `LaneDecision`, `ExecutionRequest`, `ExecutionResult` all live exclusively in `OperationsCenter/src/operations_center/contracts/` — no duplicate class definitions in SwitchBoard, WorkStation, or OperatorConsole source |
| 2 | SwitchBoard imports contracts from OperationsCenter | **PASS** | `SwitchBoard/src/switchboard/lane/engine.py:25` and `api/routes_routing.py:9` both import `from operations_center.contracts import LaneDecision, TaskProposal` |
| 3 | SwitchBoard does not execute adapters | **PASS** | SwitchBoard routing.py references backend names (kodo, archon, openclaw) only as string keys in cost/capability maps — no `execute()` calls, no adapter imports, no subprocess invocations |
| 4 | OperatorConsole does not do planning/routing/execution directly | **PASS** | OperatorConsole CLI calls `run_delegate()` → `operations_center.entrypoints.worker.main` and `operations_center.entrypoints.execute.main` — no direct TaskProposal construction, no LaneDecision logic in console source |
| 5 | WorkStation does not do orchestration | **PASS** | WorkStation Python code is limited to tools (`lane_manager.py`, `status.py`, `lane_cli.py`) and unit tests — zero occurrences of TaskProposal/ExecutionRequest/LaneDecision in non-docs source |
| 6 | Single execution path through canonical backend factory | **PASS** | `CanonicalBackendRegistry` in `backends/factory.py` is the sole adapter registry. Only two call sites in active code: `entrypoints/execute/main.py:19` and `execution/coordinator.py:21` |
| 7 | No legacy identifiers in active source | **PASS** | `9router`, `ControlPlane`, `control_plane`, `fob` — zero hits in all four active source trees. WorkStation docs contain only archival ADR/migration material, explicitly exempt |
| 8 | Artifact system persists canonical 5-file run records | **PASS** | `RunArtifactWriter.write_run()` confirmed to write `proposal.json`, `decision.json`, `execution_request.json`, `result.json`, `run_metadata.json`; proven by `tests/integration/test_execution_boundary.py::test_artifact_writer_produces_all_files` |
| 9 | auto_once is single-cycle (no loops) | **PASS** | `auto_once.py` contains exactly one function `run_auto_once()`, no `while`/`loop`/`daemon`/`background` constructs. Calls `run_delegate()` once and returns its exit code |

---

## 3. Phase Coverage Table

| Phase | Description | Status | Evidence |
|-------|-------------|--------|----------|
| 0 | Repos exist and can be imported | **VERIFIED** | All four repos present; test suites import from each repo successfully |
| 1 | WorkStation startup mechanism | **VERIFIED** | `WorkStation/scripts/up.sh` — single-entrypoint compose startup script that waits on SwitchBoard health (`:20401`) |
| 2 | SwitchBoard lane selection endpoint | **VERIFIED** | `SwitchBoard/src/switchboard/api/routes_routing.py:16` — `@router.post("/route")` accepts `TaskProposal`, returns `LaneDecision`; `/route-plan` variant also present |
| 3 | Contracts in OperationsCenter/contracts/ | **VERIFIED** | `src/operations_center/contracts/` contains `proposal.py`, `routing.py`, `execution.py`, `enums.py`, `common.py`, `__init__.py` |
| 4 | OperationsCenter calls SwitchBoard via HTTP | **VERIFIED** | `routing/client.py` — `HttpLaneRoutingClient.select_lane()` POSTs to `{base_url}/route` via `httpx`; URL configurable via `OPERATIONS_CENTER_SWITCHBOARD_URL` env var (default `http://localhost:20401`) |
| 5 | Adapter implementations with tests | **VERIFIED** | Four backends: `kodo/adapter.py`, `openclaw/adapter.py`, `archon/adapter.py`, `direct_local/adapter.py` — each with `execute(ExecutionRequest) -> ExecutionResult`; `tests/unit/backends/` covers them |
| 6 | End-to-end integration test | **VERIFIED** | `tests/integration/test_execution_boundary.py` — full pipeline `PlanningContext → TaskProposal → ExecutionRequest → ExecutionResult` tested without live services (`test_full_boundary_pipeline_missing_binary`) |
| 7 | Artifact persistence | **VERIFIED** | `execution/artifact_writer.py` — `RunArtifactWriter.write_run()` writes 5 canonical files per run; `observer/artifact_writer.py` and `decision/artifact_writer.py` write auxiliary pipeline artifacts |
| 8 | `delegate` and `auto-once` CLI commands | **VERIFIED** | `OperatorConsole/src/operator_console/cli.py:619` — `case "run" | "delegate"` dispatches to `run_delegate()`; `case "cycle" | "auto-once"` dispatches to `run_auto_once()` |
| 9 | Failure/error path tests | **VERIFIED** | `tests/integration/test_execution_boundary.py::test_adapter_returns_canonical_result_for_missing_binary` — asserts `result.success is False`, `result.failure_category == FailureReasonCategory.BACKEND_ERROR`; `tests/test_validation_history.py`, `tests/test_tuning_guardrails.py` cover failure pattern tracking |
| 10 | No 9router code in active source | **VERIFIED** | Zero hits for `9router` or `nine.router` in all four active source trees; ADR `0001-remove-9router.md` in WorkStation confirms archival removal |
| 11 | auto_once single-cycle implementation | **VERIFIED** | `auto_once.py::run_auto_once()` — observe → delegate_args → `run_delegate()` → return. No loops. Docstring: "execute exactly one autonomous cycle" |
| 12 | Repeated-run stability evidence | **VERIFIED** | `OperatorConsole/tests/test_repeated_runs.py` — explicit Phase 12 test file; covers `list_runs()` sort by timestamp, `latest_run()`, artifact isolation (no overwrites), unique run IDs across consecutive runs |

---

## 4. Boundary Violations

**None found.**

Specific checks performed:
- SwitchBoard source: `kodo`, `archon`, `openclaw` appear only as string literals in cost/capability classification tables in `lane/routing.py` — no imports, no execution calls
- OperatorConsole source: the words `TaskProposal`, `LaneDecision`, `ExecutionRequest` appear only in `demo.py` comments/docstrings describing the flow, and in `delegate.py` comments — no live construction
- WorkStation Python source: zero occurrences of contract class names in non-docs code

---

## 5. Flow Validation

**Confirmed: exactly ONE execution path exists.**

```
OperatorConsole
  └── cli.py: case "run"|"delegate" → run_delegate()
      └── delegate.py: run_delegate()
          ├── subprocess: operations_center.entrypoints.worker.main  (planning)
          │       └── PlanningService.plan()
          │           ├── build_proposal(context) → TaskProposal
          │           └── HttpLaneRoutingClient.select_lane(proposal)
          │               └── POST http://localhost:20401/route → LaneDecision
          └── subprocess: operations_center.entrypoints.execute.main (execution)
                  └── ExecutionCoordinator
                      └── CanonicalBackendRegistry.for_backend(backend)
                          └── adapter.execute(ExecutionRequest) → ExecutionResult
```

Code evidence for the choke point:
- `routing/client.py:HttpLaneRoutingClient.select_lane()` — the single HTTP crossing to SwitchBoard
- `execution/coordinator.py` and `entrypoints/execute/main.py:19` — the only two callers of `CanonicalBackendRegistry`

---

## 6. Artifact System

**Five canonical files per run, written by `RunArtifactWriter.write_run()`:**

```
~/.console/operations_center/runs/<run_id>/
  proposal.json          # TaskProposal fields (proposal_id, goal_text, task_type, target, constraints)
  decision.json          # LaneDecision fields (decision_id, selected_lane, selected_backend, confidence, policy_rule_matched)
  execution_request.json # ExecutionRequest (proposal_id, decision_id, goal_text, repo_key, task_branch, timeout_seconds)
  result.json            # ExecutionResult (status, success, [failure_category])
  run_metadata.json      # Cross-reference: run_id, proposal_id, decision_id, written_at, status, success, executed, selected_lane, selected_backend
```

Sample `proposal.json` (from test artifact in `/tmp/pytest-of-dev/`):
```json
{"proposal_id": "prop-run-abc", "goal_text": "Fix lint errors", "task_type": "lint_fix",
 "target": {"repo_key": "svc", "clone_url": "https://example.invalid/svc.git", "base_branch": "main", "allowed_paths": []},
 "constraints": {}}
```

Sample `decision.json`:
```json
{"decision_id": "dec-run-a", "proposal_id": "prop-run-a", "selected_lane": "claude_cli",
 "selected_backend": "kodo", "confidence": 0.9, "policy_rule_matched": "test"}
```

Additional artifact writers exist for observer pipeline stages:
- `observer/artifact_writer.py` → `repo_state_snapshot.json` / `.md` per run_id
- `decision/artifact_writer.py` → `proposal_candidates.json` / `.md`

---

## 7. Failure Handling

**Explicit failure handling — no silent fallbacks.**

| Layer | Mechanism | Evidence |
|-------|-----------|----------|
| OperationsCenter routing | `SwitchBoardUnavailableError` raised on `ConnectError` or `TimeoutException` | `routing/client.py:HttpLaneRoutingClient.select_lane()` |
| SwitchBoard routing | Returns HTTP 503 with `routing_error` code when policy evaluation fails | `SwitchBoard/src/switchboard/api/errors.py:62` |
| SwitchBoard admin | Returns HTTP 404 for unknown request_id | `routes_admin.py:117` |
| Backend execution | `FailureReasonCategory.BACKEND_ERROR` written to `ExecutionResult.failure_category` when binary missing | `integration/test_execution_boundary.py::test_adapter_returns_canonical_result_for_missing_binary` |
| Fallback routing | SwitchBoard tracks `fallback_used: bool` in `LaneExplanation`; logs when no policy rule matched | `lane/policy.py:94`, `lane/explain.py:47` |
| Validation failures | `ValidationFailureRecord` tracked per task; `overall_failure_rate` computed; tasks with ≥2 failures at ≥50% rate surfaced | `observer/collectors/validation_history.py:99–107` |
| Goal file retries | Retry mutations are idempotent — each rewrite reconstructs from scratch, no duplicate sections | `test_goal_file_idempotency.py` |

---

## 8. Autonomy Verification

**auto_once is confirmed single-cycle.**

Full implementation of `run_auto_once()` in `OperatorConsole/src/operator_console/auto_once.py`:

1. `observe(args)` — derives goal, repo_key, clone_url from CLI flags / `.console/task.md` / defaults
2. Builds `delegate_args` list from observed context
3. Calls `run_delegate(delegate_args)` exactly once
4. Returns the integer exit code from `run_delegate()`

No `while`, `loop`, `daemon`, or `background` constructs anywhere in the file. The docstring reads: "execute exactly one autonomous cycle."

`test_auto_once.py` mocks `run_delegate` and verifies the function returns its exit code without looping.

---

## 9. Test Health Summary

| Repo | Passed | Skipped | Failed | Notes |
|------|--------|---------|--------|-------|
| OperationsCenter | 1,863 | 4 | 0 | 120 test files; includes integration/, unit/ |
| SwitchBoard | 264 | 0 | 0 | 17 test files |
| OperatorConsole | 93 | 0 | 0 | 4 test files |
| WorkStation | 147 | 0 | 0 | unit/ only (smoke tests require live stack) |
| **Total** | **2,367** | **4** | **0** | |

The 4 skipped tests in OperationsCenter are the live-aider tests gated on `shutil.which("aider")` — correct behavior when aider is not installed.

---

## 10. Remaining Gaps

**No blockers. Three minor observations:**

1. **WorkStation smoke tests unrunnable in isolation.** `test/smoke/test_stack_health.py` requires a live Docker stack. No pytest environment configured at the system level — WorkStation's `.venv` is present and unit tests pass (147), but smoke tests are integration-only by design. This is expected, not a gap.

2. **No dedicated Phase 6 e2e test file.** The integration test that proves the full pipeline is `tests/integration/test_execution_boundary.py`, not a file named `test_e2e_*` or `test_integration_*`. It covers the full PlanningContext → ExecutionResult boundary but uses a stub LaneDecision (no live SwitchBoard). A test that fires the actual HTTP call to SwitchBoard does not exist at the unit level — that is the smoke/live-stack tier. Acceptable gap given the architecture, but worth noting.

3. **archon and openclaw adapters conditionally registered.** `CanonicalBackendRegistry.from_settings()` requires `archon_adapter` and `openclaw_runner` to be passed explicitly; they default to None. This means the kodo and direct_local backends are always available, but archon/openclaw are live-configuration-dependent. Tests cover the conditional path but the adapters require live Archon/OpenClaw infrastructure for full validation.

None of these gaps affect daily use.
