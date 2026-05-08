# Post-Extraction Runtime Architecture Validation

**Date:** 2026-05-08
**Branch:** `validate/post-extraction-runtime-2026-05-08`
**Base commit:** `66cb700` (main)
**Scope:** Validate that real tasks can flow through CxRP → SwitchBoard → SourceRegistry → RxP → ExecutorRuntime → OperationsCenter and be reconstructed from artifacts.

---

## 1. Environment

| Component | Version / Status |
|---|---|
| repo branch | `validate/post-extraction-runtime-2026-05-08` |
| commit SHA | `66cb700` |
| operations-center | 0.1.0 (editable, `src/operations_center`) |
| cxrp | 0.2.0 (installed wheel) |
| rxp | 0.2.0 (installed wheel) |
| executor-runtime | 0.1.0 (installed wheel) |
| source-registry | 0.1.0 (installed wheel) |
| platform-manifest | 1.0.0 (installed wheel) |
| switchboard | NOT installed as standalone package — surface lives in `operations_center.routing` (HTTP client) and `operations_center.contracts.cxrp_mapper` (CxRP narrowing). Routing is in-process where SwitchBoard service is not deployed. |
| workstation | NOT installed (used only for optional `discover_local_manifest`; OC degrades to None) |
| archon-client | NOT installed (HTTP-only integration via `backends/archon/http_client.py`) |
| Archon service | **NOT REACHABLE** — port 8181/3737 connection refused; no `archon-server` container running. Run 4 used the mocked integration path. |
| Python | 3.12.3 |
| OS | Linux 5.15.0 (host) |

## 2. Pre-Flight Results

| Check | Result | Notes |
|---|---|---|
| `import operations_center` | PASS | Required venv activation (`.venv/bin/activate`); shims-Python missed it. |
| Test collection | PASS | 3598 tests collected in 4.18s, no errors. |
| cxrp pin | PASS | 0.2.0 installed, all RxP/CxRP imports resolve. |
| ExecutorRuntime install | PASS | `executor_runtime.ExecutorRuntime`, runners (`SubprocessRunner`, `AsyncHttpRunner`, `HttpRunner`, `ManualRunner`) all importable. |
| RxP install | PASS | `rxp.contracts.RuntimeInvocation` / `RuntimeResult` constructible; vocabulary stable (`RUNTIME_KINDS`, `RUNTIME_STATUSES`). |
| SourceRegistry install | PASS | `source_registry` 0.1.0 importable. Not exercised in current execute paths — opportunistic. |
| SwitchBoard injection path | DEGRADED | Active as **schema-only / in-process** today: `LaneDecision` (`contracts/routing.py`) is the SB output contract; `routing/client.py` carries the HTTP client (`SwitchBoardUnavailableError`). Demo/CLI runs use stub routing. No live SB service was probed. |
| Archon service | UNAVAILABLE | Substitute with mocked integration tests per task allowance. |

Pre-flight verdict: **continue with validation**, with Run 4 mocked.

## 3. Run Summary Table

| # | Run label | Backend / Runtime | Expected | Actual RxP status | Actual OC status | Pass |
|---|---|---|---|---|---|---|
| 1 | Happy path (CLI) | `demo_stub` / N/A (no ExecutorRuntime) | success | n/a (stub bypasses RxP) | `succeeded` (success=True) | PASS (with caveat — see G-V01) |
| 1b | Happy path (ExecutorRuntime direct) | subprocess `/bin/echo` | success | `succeeded` exit=0 | n/a (no OC adapter) | PASS |
| 2 | Expected failure (ExecutorRuntime) | subprocess `exit 17` | failed | `failed` exit=17, stderr captured | (via direct_local tests) `FAILED`, `BACKEND_ERROR` | PASS |
| 3 | Timeout (ExecutorRuntime) | subprocess sleep 5 / timeout=1 | timed_out | `timed_out` exit=-9 (SIGKILL) | (via direct_local tests) `TIMED_OUT`, `TIMEOUT` | PASS |
| 4 | Archon workflow (mocked) | `archon` / `http_async` | live unavailable; mocked passes | mocked succeeded/failed/cancelled/paused | maps to OC success/failure/partial as designed | PASS (mocked); **NOT validated against live container** |
| 5 | Non-Archon backend | `direct_local` / subprocess via ExecutorRuntime | success/fail/timeout via real RxP | matched by 18 `tests/unit/backends/direct_local/test_adapter.py` cases | matched | PASS |

**Test suite:** `tests/unit/backends/` 418 pass; `tests/unit/backends/archon/` 169 pass; `tests/unit/backends/archon/test_http_workflow.py` 15/15 pass; full `tests/unit/` 2595 pass, 1 skipped, 1 warning in 28 s.

### Run 1 evidence

```
run_id      = 180da3e9-2e84-493f-8cb8-a3358c4731ba
proposal_id = 77ae7aa6-5b6f-4c5f-8b8a-29a5fe4b0360
decision_id = ce0cb41d-f2b1-47e8-8cdf-6f30bc13b4b9
artifact dir: /tmp/oc-validation/run1-happy/.operations_center/runs/180da3e9-.../
files: proposal.json, decision.json, execution_request.json, result.json,
       execution_record.json, execution_trace.json, run_metadata.json
status: SUCCEEDED, success=True
```

### Run 1b/2/3 evidence (direct ExecutorRuntime drive)

```
[Run1 happy]   inv_id=happy-001    status=succeeded  exit=0
                stdout: 'happy-path-output\n'
[Run2 failure] inv_id=fail-002     status=failed     exit=17
                stdout: 'line1\n'  stderr: 'err1\n'
[Run3 timeout] inv_id=timeout-003  status=timed_out  exit=-9 (SIGKILL)
                error_summary='process exceeded timeout of 1 seconds'
                stdout (partial): 'before-sleep\n'
IDENTITY INVARIANT (RuntimeResult.invocation_id == RuntimeInvocation.invocation_id): PASS
```

Process-group cleanup verified: post-timeout `pgrep -f 'sleep 5'` returns only the matching `pgrep` shell itself; no orphaned descendants.

## 4. Traceability Findings

| Identifier | OC Request | OC Result | OC Record | OC Trace | run_metadata | RxP Invocation | RxP Result |
|---|---|---|---|---|---|---|---|
| `run_id` | YES | YES | YES | NO (only `status`) | YES | n/a | n/a |
| `proposal_id` | YES | YES | YES | NO | YES | n/a | n/a |
| `decision_id` | YES | YES | YES | NO | YES | n/a | n/a |
| `backend` | n/a | NO | YES (`backend: demo_stub`) | NO | NO | YES (metadata) | YES (metadata) |
| `lane` | n/a | NO | YES (`lane: aider_local`) | NO | NO | n/a | n/a |
| RxP `invocation_id` | NO | **NO** | **NO** | NO | NO | YES | YES |
| RxP `runtime_kind` | NO | **NO** | NO | NO | NO | YES | YES |
| stdout_path / stderr_path | NO | **NO** (only `ExecutionArtifact.uri` for one log_excerpt; raw paths discarded) | NO | NO | NO | n/a | YES |
| ExecutorRuntime artifact_directory | NO | NO | NO | NO | NO | YES | implicit |
| SourceRegistry source name / SHA | NO | NO | NO | NO | NO | n/a | n/a |
| Archon `conversation_id` | NO | NO | (only via raw RxP metadata if surfaced — not in `ExecutionResult` schema) | NO | NO | YES (when adapter sets it) | YES |
| Archon `run_id` | NO | NO | (same) | NO | NO | YES | YES |

**Reconstructability verdict:** A human can follow OC `run_id` → `proposal/decision/result/record/trace` artifacts on disk. **A human cannot** follow that same `run_id` to a specific RxP `invocation_id`, ExecutorRuntime artifact directory, or external framework run id from artifacts alone — the linkage is lost between the adapter call and the result it returns.

## 5. Boundary Findings

| Concern | Status | Evidence |
|---|---|---|
| OC backend bypasses ExecutorRuntime | **OBSERVED, INTENTIONAL** for `demo_stub` only — written-as-stub by design (`backends/demo_stub/adapter.py` writes file directly, no RxP). All real adapters (`direct_local`, `kodo`, `archon`, `openclaw`) route through `ExecutorRuntime.run()`. |
| ExecutorRuntime contains backend policy | NONE OBSERVED | `executor_runtime.runners.*` are `SubprocessRunner`/`AsyncHttpRunner`/`HttpRunner`/`ManualRunner` — runtime mechanics only. |
| SourceRegistry executes something | NONE OBSERVED | `source_registry` imported in OC: not used in execute paths today. |
| RxP carries orchestration fields | NONE OBSERVED | `RuntimeInvocation` fields are runtime-shape (command, env, working_directory, timeout, artifact_directory) — no proposal_id/decision_id/run_id leakage. |
| CxRP carries subprocess fields | NONE OBSERVED | `ExecutionRequest` fields are orchestration (workspace_path, task_branch, goal_text, constraints) — no runtime mechanics. |
| SwitchBoard decision not visible downstream | PARTIAL | `decision_id`, `lane`, `backend` reach the record. **`switchboard_version` field exists on `LaneDecision` but is not surfaced into `ExecutionResult` or written to artifacts.** Selection rationale (`rule`, `rationale`) is only in the `LaneDecision` JSON, not propagated. |
| Boundary tooling | PRESENT | `tools/boundary/switchboard_denylist.py` denylist enforces orchestration symbols stay out of `switchboard.*` (passes today). |

## 6. Bugs / Gaps Found

### G-V01 — `ExecutionResult` has no RxP `invocation_id` (HIGH)
**Title:** OC ExecutionResult / ExecutionRecord schemas drop the RxP linkage.
**Severity:** HIGH (defeats the point of RxP-as-traceability-contract).
**Evidence:** `ExecutionResult.model_fields` enumerated (Section 4 table). DirectLocalBackendAdapter calls `self._runtime.run(invocation)` and binds `rxp_result` locally, then constructs `ExecutionResult` without forwarding `rxp_result.invocation_id`, `runtime_name`, `runtime_kind`, `stdout_path`, `stderr_path`, or `artifacts` (the ArtifactDescriptor list).
**Suggested owner:** OperationsCenter (`contracts/execution.py` schema + each backend adapter).
**Suggested next action:** Add an optional `runtime_invocation_ref` field to `ExecutionResult` carrying `{invocation_id, runtime_name, runtime_kind, stdout_path, stderr_path, artifact_directory}`; have each adapter populate it from the `RuntimeResult`. One contract change, N adapter changes — additive, backward-compatible.

### G-V02 — SwitchBoard selection rationale not propagated (MEDIUM)
**Title:** `LaneDecision.rule` / `rationale` / `switchboard_version` are written to `decision.json` but not embedded in the OC observability record.
**Severity:** MEDIUM (operators can still find it via the decision artifact; cross-system queries cannot).
**Evidence:** `execution_record.json` for Run 1 carries `metadata.policy.*` but no `metadata.routing.*` block.
**Suggested owner:** OperationsCenter (`execution/coordinator.py` record builder).
**Suggested next action:** Merge a `routing` dict (`{decision_id, lane, backend, rule, rationale, switchboard_version}`) into the record metadata before persistence.

### G-V03 — `execution_trace.json` is reduced to a status string (LOW)
**Title:** Trace artifact emitted as `{"status": "succeeded"}` only.
**Severity:** LOW (retained as smoke; provides no event replay).
**Evidence:** Run 1 `execution_trace.json` contents.
**Suggested owner:** OperationsCenter (`observability/` trace builder).
**Suggested next action:** Either populate the trace with structured events (timing, invocation refs) or rename / delete the artifact so its absence isn't taken as missing observability.

### G-V04 — G-005 classifier still absent (KNOWN, NOT FIXED)
**Title:** No string classifier for "extra usage / capacity exhausted" stdout from runtime backends.
**Severity:** HIGH (per existing audit) but **not in this task's scope**.
**Evidence:** `grep -rE "extra usage|capacity exhausted|usage.*resets" src/operations_center/` returns 0 hits. `grep -rE "extra_usage|capacity_exhaust" src/` also empty.
**Suggested owner:** OperationsCenter or per-backend adapter (kodo/archon) — open question.
**Suggested next action:** Build the classifier in OC's `execution/validation.py` or in the per-backend normalizer. Track as a separate task; not bundled into this validation.

### G-V05 — Live Archon path NOT validated this pass (DEFERRED)
**Title:** Archon container not running on this host; only mocked path exercised.
**Severity:** MEDIUM (mocked tests pass; real-API findings already captured in earlier "Archon real-workflow live validation" log entry from 2026-05-07).
**Evidence:** `curl http://localhost:8181/api/health` connection refused; `docker ps` shows no archon container.
**Suggested owner:** Operator (start container) or follow-up validation pass.
**Suggested next action:** Bring up `WorkStation/compose/profiles/archon.yml`, re-run probe + a real workflow dispatch, confirm `conversation_id` and `run_id` are persisted in OC observability.

## 7. Minimal Fixes Applied

**None.** Validation completed without code edits. The G-V01/V02/V03 findings are real, but each needs schema design discussion rather than an inline fix — they belong in a follow-up task per the "do not fix every discovered issue inline" guidance.

## 8. Invariant Check Summary

| Invariant | Status |
|---|---|
| `RuntimeResult.invocation_id == RuntimeInvocation.invocation_id` | PASS (verified for 3 direct ExecutorRuntime runs) |
| OC ExecutionResult references same `run_id`/`proposal_id`/`decision_id` as request | PASS |
| RunMemory points to OC `run_id` | n/a — RunMemory is a separate primitive (ER-002) and not invoked by the demo entrypoint; coordinator's `record_execution_result` is wired only when `run_memory_index_dir` is supplied (None in demo). |
| Runtime success → OC failure (false negative) | NOT OBSERVED |
| Runtime failure → OC success (false positive) | NOT OBSERVED in unit tests; **G-V04 (G-005) means it remains possible at runtime for stdout-pattern false-success in capacity exhaustion** |
| Timeout → generic failure (without timeout reason) | NOT OBSERVED — direct_local + archon both map timeout → TIMED_OUT/TIMEOUT distinctly |
| Paused Archon → partial | PASS (test_paused_maps_to_partial_and_is_not_abandoned) |
| `stdout_path` exists when stdout captured | PASS |
| `stderr_path` exists when stderr captured | PASS |
| Process-group terminated on timeout | PASS (no descendants found post-timeout) |

## 9. Recommendation

**Architecture mostly valid; fix listed traceability gaps before broader use.**

Reasoning:
- Layer mechanics work end-to-end: success, failure, timeout, paused, cancelled all map correctly through CxRP → adapter → RxP → ExecutorRuntime and back to OC, both in unit tests and in a live ExecutorRuntime drive.
- Identity invariant holds at the RxP boundary.
- Boundary cleanliness is good: orchestration is not leaking into runtime, runtime mechanics are not leaking into orchestration. Boundary tooling (`switchboard_denylist.py`) is in place.
- **However:** an operator cannot fully reconstruct one run from artifacts alone because `ExecutionResult` does not carry the RxP `invocation_id` or stdout/stderr paths (G-V01). For the architecture to honour its stated traceability goal, that schema field needs to land. G-V02 (routing rationale missing from record) is a smaller version of the same problem.
- G-005 (G-V04) is unaddressed and remains a real false-success risk for any backend whose stdout can carry capacity-exhaustion text — out of scope for this task per the brief.
- Live Archon was not validated this pass (service not running). Mocked path is comprehensive but the prior 2026-05-07 live run remains the latest real-service validation.

**Suggested follow-ups, grouped by owner:**
- **OperationsCenter:** add `runtime_invocation_ref` field on `ExecutionResult`; merge routing rationale into record metadata; either flesh out `execution_trace.json` or remove it.
- **Operator:** bring up Archon container and re-run a real workflow to close G-V05.
- **OperationsCenter (separate task):** address G-005 / G-V04 with a stdout classifier — out of scope here.

---

_End of report. Author: validation pass on `validate/post-extraction-runtime-2026-05-08`._
