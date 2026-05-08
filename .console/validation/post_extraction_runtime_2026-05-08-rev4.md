# Post-Extraction Runtime Validation — Rev 4 (2026-05-08)

Confirmation pass. Re-runs the full validation matrix on `main` after
all five surfaced gaps were closed (#125, #127, #128, #129, #130-live).
**No code changes expected; none required.**

## 1. Environment

| Item | Value |
|------|-------|
| Repo head | `a375950` (`main`) |
| operations-center | 0.1.0 (editable) |
| cxrp / rxp / executor-runtime / source-registry / platform-manifest | 0.2.0 / 0.2.0 / 0.1.0 / 0.1.0 / 1.0.0 |
| Python | 3.12.3 |
| Archon | container stopped per operator request after Rev 3; Run 4 covered via mocked path per task spec ("If Archon service is unavailable, document this and run a mocked integration path instead.") |

## 2. Pre-Flight Results

| Check | Status |
|-------|--------|
| OC + cxrp + rxp + executor_runtime + source_registry imports clean | OK |
| `pytest --co` collects | OK — 3601 tests collected |
| Closed-gap features present on main | OK — `RuntimeInvocationRef`, `ExecutionResult.runtime_invocation_ref`, `ExecutionTrace.runtime_invocation_ref`, `ExecutionTrace.routing`, `classify_capacity_exhaustion` all reachable via import |
| Archon reachable | NO — container stopped (`workstation-archon` not in `docker ps`); use mocked path |

## 3. Run Summary Table

| Run | Backend | Runtime kind | Expected | RxP status | OC status | runtime_invocation_ref | record.metadata.routing | trace.routing | Pass |
|-----|---------|--------------|----------|------------|-----------|------------------------|-------------------------|---------------|------|
| 1 — happy (demo) | demo_stub | (none — bypass by design) | succeeded | n/a | succeeded | None (correct) | populated (8 keys) | populated (8 keys) | ✅ |
| 2 — failure | direct_local | subprocess (`/bin/false`) | failed | failed | failed/`backend_error` | populated, paths resolve | populated | populated | ✅ |
| 3 — timeout | direct_local | subprocess (1 s timeout, `sleep 5`) | timed_out | timed_out | timed_out/`timeout`, "Timed out after 1s" | populated, paths resolve | populated | populated | ✅ |
| 4 — Archon (mocked) | archon | http_async | covered by mocked tests | per-case | mapped per case | populated (per fixture) | n/a in mock | n/a in mock | ✅ — 169/169 archon adapter tests pass |
| 5 — non-Archon | direct_local | subprocess (`/bin/true`) | succeeded | succeeded | succeeded | populated, paths resolve | populated | populated | ✅ |
| Bonus — G-V04 false-success guard | direct_local | subprocess (success-faking runtime printing "out of extra usage") | flipped to FAILED | succeeded (raw) | **failed/`backend_error`, "capacity exhaustion detected: You're out of extra usage · resets 4:20am"** | populated | populated | populated | ✅ — classifier intercepted exit-0 false success |

Process-group invariant: `pgrep -af 'sleeper.sh|sleep 5'` after Run 3 returned no orphans.

## 4. Cross-Layer Traceability — Per Run

| Identifier | Run 1 | Run 2 | Run 3 | Run 4 | Run 5 | G-V04 |
|------------|-------|-------|-------|-------|-------|-------|
| OC `run_id` | ✅ | ✅ | ✅ | ✅ (mocked) | ✅ | ✅ |
| CxRP request id | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| SwitchBoard `decision_id` (in record + trace) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| backend / runtime name | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| RxP `invocation_id` (matches result) | n/a | ✅ | ✅ | ✅ | ✅ | ✅ |
| ExecutorRuntime artifact dir | n/a | ✅ exists | ✅ exists | ✅ (mock) | ✅ exists | ✅ exists |
| `stdout_path` / `stderr_path` resolvable | n/a | ✅ | ✅ | n/a (http_async) | ✅ | ✅ |
| SourceRegistry source / SHA | n/a — no SourceRegistry-managed backend on these runs | | | | | |
| External framework run_id | n/a | n/a | n/a | mocked | n/a | n/a |
| ExecutionRecord id | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

Acceptance target met: each real-runtime run is reconstructable from
`execution_trace.json` alone — OC run → SwitchBoard rule → RxP invocation
→ stdout/stderr/artifact_dir.

## 5. Boundary Findings

Unchanged across Revs 1→4:

- No real adapter bypasses `ExecutorRuntime`; `demo_stub` remains the only intentional bypass.
- `RuntimeInvocation` carries no orchestration fields; `ExecutionRequest` carries no subprocess fields.
- `tools/boundary/switchboard_denylist.py` enforces.
- New observability fields are contract-layer mirrors only; no new RxP→OC import edges.
- SourceRegistry remains installed but is not exercised on the live execute paths in these runs.

## 6. Invariant Checks

| Invariant | Result |
|-----------|--------|
| `RuntimeResult.invocation_id == RuntimeInvocation.invocation_id` | Holds; verified by `tests/unit/backends/test_runtime_invocation_ref.py` + observed live in Runs 2/3/5 + G-V04 |
| Runtime success → OC success (unless validation fails) | Run 1, Run 5: ✅ |
| Runtime failure → OC failure | Run 2: ✅ |
| Timeout → OC `timed_out` with timeout reason | Run 3: ✅ — `failure_category=timeout`, message `[aider] Timed out after 1s` |
| Process group cleaned on timeout | Run 3: ✅ — no orphans |
| Paused Archon → `partial` | Verified by mocked tests in `tests/unit/backends/archon/` |
| Capacity exhaustion → flip to FAILED, not silent success | G-V04 bonus run: ✅ — `failure_reason` names the matched line |

## 7. Bugs / Gaps Found

**None new.** All gaps surfaced in Revs 1–3 remain closed.

| ID | Severity | Status |
|----|----------|--------|
| G-V01 | HIGH | CLOSED in #125; verified live this rev |
| G-V02 | MEDIUM | CLOSED in #127; verified live this rev (record + trace both carry routing) |
| G-V03 | LOW | CLOSED in #129; verified live this rev (trace carries ref + routing) |
| G-V04 / G-005 | (out-of-scope-but-fixed) | CLOSED in #128; verified live this rev (capacity classifier flipped exit-0 false-success) |
| G-V05 | (deferred) | Live-validated in Rev 3 (#130); container stopped per operator request after that pass — mocked path used here per task spec |

## 8. Minimal Fixes Applied

**None.** No code changes were required to drive any run in this rev.

## 9. Recommendation

> **Architecture validated enough to proceed.**

The post-extraction architecture has held through four validation revs.
All identified gaps closed. Trace artifacts are self-contained. Identity,
status, artifact, and provenance invariants hold across the full run
matrix. Capacity-exhaustion false-success class is now actively guarded
against on real-adapter paths.

Next work belongs to the Runtime Observability Hardening arc
(`.console/backlog.md`), which is operational/observational polish on
top of this validated architecture — not further boundary design.
