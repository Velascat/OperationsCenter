# Post-Extraction Runtime Validation — Rev 2 (2026-05-08)

Re-validation after G-V01 fix landed on `main` (commit `49f2457`, PR #125).
The first pass is preserved at `post_extraction_runtime_2026-05-08.md`;
this document only records what changed.

## 1. Environment

| Item | Value |
|------|-------|
| Repo branch | `main` |
| Head commit | `49f2457` (G-V01) on top of `53640d8` (validation-rev1) and `66cb700` (doctor-version) |
| operations-center | 0.1.0 (editable) |
| cxrp | 0.2.0 |
| rxp | 0.2.0 |
| executor-runtime | 0.1.0 |
| source-registry | 0.1.0 (installed; not exercised on the live execute paths) |
| platform-manifest | 1.0.0 |
| Python | 3.12.3 |
| Archon HTTP | unreachable at `localhost:8181`; no archon container in `docker ps` |

## 2. Pre-flight Results

| Check | Status |
|-------|--------|
| OC + cxrp + rxp + executor_runtime + source_registry imports | OK |
| `pytest --co` collects | OK — 3579 tests collected |
| Archon service reachable | NO — same as Rev 1; mocked path used for Run 4 |

## 3. Run Summary Table

All five runs exercised (Run 1 through demo entrypoint; Runs 2/3/5 driven
through `DirectLocalBackendAdapter` with real `ExecutorRuntime`; Run 4 via
the unit-tested mocked HTTP workflow path).

| Run | Backend | Runtime kind | Expected | RxP status | OC status | runtime_invocation_ref present? | Pass |
|-----|---------|--------------|----------|------------|-----------|---------------------------------|------|
| 1 — happy (demo) | demo_stub | (none — bypasses ExecutorRuntime by design) | succeeded | n/a | succeeded | None (correct) | ✅ |
| 2 — failure | direct_local | subprocess (`/bin/false`) | failed | failed | failed (`backend_error`) | yes — `direct-local-a0f49690`, paths exist | ✅ |
| 3 — timeout | direct_local | subprocess (1 s timeout, `sleep 5`) | timed_out | timed_out | timed_out (`timeout`) | yes — `direct-local-e5a6cec9`, paths exist | ✅ |
| 4 — Archon mocked | archon (http_async) | http_async | covered by mocked tests | succeeded/failed/paused/timed_out per test | mapped per case | yes (per Rev 1 + new test in `test_runtime_invocation_ref.py`) | ✅ |
| 5 — non-Archon | direct_local | subprocess (`/bin/true`) | succeeded | succeeded | succeeded | yes — `direct-local-43796465`, paths exist | ✅ |

Process-group invariant verified after Run 3: `pgrep -af 'sleeper.sh|sleep 5'`
returned no orphans. Stdout/stderr capture files exist for every run that
went through ExecutorRuntime (size 0 expected for `/bin/true` / `/bin/false`).

## 4. Traceability Findings — G-V01 closed

For every run that delegates to ExecutorRuntime, the OC `ExecutionResult`
now carries `runtime_invocation_ref` with `invocation_id`, `runtime_name`,
`runtime_kind`, `stdout_path`, `stderr_path`, `artifact_directory`. The
`invocation_id` matches the `RuntimeInvocation.invocation_id` that
ExecutorRuntime received (verified in
`tests/unit/backends/test_runtime_invocation_ref.py`).

Cross-layer linkage from OC `run_id` → RxP `invocation_id` → ExecutorRuntime
artifact directory → captured stdout/stderr is now reconstructable from
artifacts alone. **G-V01 closed.**

`demo_stub` correctly leaves the field None — it does not invoke
ExecutorRuntime by design.

## 5. Boundary Findings

Unchanged from Rev 1:

- No real adapter bypasses ExecutorRuntime; demo_stub remains the only
  intentional bypass.
- `RuntimeInvocation` carries no orchestration fields; `ExecutionRequest`
  carries no subprocess fields; `tools/boundary/switchboard_denylist.py`
  enforces.
- The new `runtime_invocation_ref` lives in `operations_center.contracts.execution`
  as a contract-layer mirror of identity-only RxP fields — no new RxP →
  OC import edges introduced.

## 6. Bugs / Gaps Status

| ID | Severity | Status as of Rev 2 | Notes |
|----|----------|--------------------|-------|
| G-V01 | HIGH | **CLOSED** in #125 | `runtime_invocation_ref` populated by every adapter that calls ExecutorRuntime; tests verify identity invariant |
| G-V02 | MEDIUM | OPEN | `decision.json` still carries routing rationale but `execution_record.json metadata` has only `policy / task_type / risk_level` keys — no `routing.*` block. Easy follow-up: merge `LaneDecision.rule / rationale / switchboard_version` into the record metadata in `RunArtifactWriter.write_run`. |
| G-V03 | LOW → **revised: lower-priority** | OPEN, smaller than first reported | The first pass over-stated this. Current `execution_trace.json` already carries `trace_id`, `record_id`, `headline`, `summary`, `changed_files_summary`, `validation_summary`, `warnings`, `key_artifacts`, `backend_detail_refs` — not `{"status": ...}` only. What it still lacks is forwarding of `runtime_invocation_ref` and routing rationale into the trace/key_artifacts list, but those are richness items rather than a missing trace. |
| G-V04 (G-005) | (out of scope) | OPEN | Capacity-exhaustion classifier still absent; not bundled here. |
| G-V05 | (deferred) | OPEN | Live Archon validation still pending — container not running. |

## 7. Minimal Fixes Applied

None this pass. G-V01 was fixed under PR #125 prior to this re-validation;
no inline fixes were required to drive Runs 1–5.

## 8. Invariant Check Summary

| Invariant | Result |
|-----------|--------|
| `RuntimeResult.invocation_id == RuntimeInvocation.invocation_id` | Holds across all real-runtime runs (asserted by adapter tests + `test_runtime_invocation_ref.py`) |
| Runtime success → OC success | Run 1, Run 5: ✅ |
| Runtime failure → OC failure (not partial/success) | Run 2: ✅ |
| Timeout → OC `timed_out` (not generic failure), with timeout reason | Run 3: ✅ — `failure_category=timeout`, `failure_reason="[aider] Timed out after 1s"` |
| Process group terminated on timeout | Run 3: ✅ — no orphans |
| stdout_path / stderr_path resolve when populated | Runs 2/3/5: ✅ |
| `artifact_directory` populated where adapter set it | Runs 2/3/5: ✅ |

## 9. Recommendation

> **Architecture validated enough to proceed.**

Rev 1 reported "mostly valid; fix G-V01 before broader use." G-V01 is now
closed and the OC ↔ RxP linkage is reconstructable from artifacts alone.
G-V02 remains a worthwhile small follow-up (routing rationale → execution
record metadata); G-V03 is a richness item rather than a true gap; G-V04
and G-V05 are out of scope per the original brief.

Re-running this rev's five runs end-to-end produces consistent, traceable
artifacts. The post-extraction architecture has now demonstrably proven
itself under real execution.
