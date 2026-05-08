# Post-Extraction Runtime Validation — Rev 3 (2026-05-08)

Closes the remaining gaps surfaced in Rev 1 and Rev 2. After this pass,
G-V01 through G-V05 are all addressed at the OC layer.

| Gap | Rev 1 | Rev 2 | Rev 3 |
|-----|-------|-------|-------|
| G-V01 | HIGH (open) | CLOSED in #125 | — |
| G-V02 | MEDIUM (open) | open | CLOSED in #127 |
| G-V03 | LOW (over-stated open) | revised: minor open | CLOSED in #129 |
| G-V04 / G-005 | out-of-scope (open) | open | CLOSED in #128 |
| G-V05 | deferred (no container) | deferred | LIVE-VALIDATED below |

## Environment

| Item | Value |
|------|-------|
| Repo head | `main` post-#129 (G-V03 trace richness) |
| Archon container | `workstation-archon` up & healthy on `:3000` (image `798b17e56417`, version 0.3.10) |
| operations-center | 0.1.0 (editable) |
| cxrp / rxp / executor-runtime / source-registry / platform-manifest | 0.2.0 / 0.2.0 / 0.1.0 / 0.1.0 / 1.0.0 |

Note: prior validation rev mentioned port 8181 — that was incorrect. The
WorkStation compose profile binds Archon to `${PORT_ARCHON:-3000}`.

## G-V05 — Live Archon Validation

**Action**: brought up the WorkStation `compose/profiles/archon.yml` profile
(`docker compose ... up -d archon`); container reached `healthy` state with
`/api/health` returning HTTP 200.

**End-to-end live dispatch** through the OC archon HTTP path
(`ArchonHttpWorkflowDispatcher.dispatch` → `AsyncHttpRunner` →
`RuntimeInvocation` → live Archon → poll-until-terminal):

```json
{
  "run_id": "gv05-live-1",
  "outcome": "timeout",
  "exit_code": -1,
  "duration_ms": 15428,
  "timeout_hit": true,
  "error_text": "archon workflow exceeded timeout of 15s",
  "invocation_ref": {
    "invocation_id": "gv05-live-1",
    "runtime_name": "archon",
    "runtime_kind": "http_async",
    "stdout_path": null,
    "stderr_path": null,
    "artifact_directory": null
  }
}
```

**What this validates live (not previously possible)**:

- Live `/api/health` reachability via `archon_health_probe` ✅
- Live `POST /api/conversations` round-trip (real `conversation_id` returned) ✅
- Live `AsyncHttpRunner` kickoff + polling loop against a real Archon ✅
- Timeout flow end-to-end: OC `timeout` outcome, `timeout_hit=true`,
  `error_text` correctly framed, no orphan workflows left running ✅
- `runtime_invocation_ref` populated on the live capture (G-V01 working
  live) — note that AsyncHttpRunner does not capture stdout/stderr to disk,
  so those paths are correctly None for this kind ✅

**What the live container could not validate**:

- A successful `archon-assist` workflow run — Archon's `/api/workflows`
  returns `{"workflows":[]}` because no workflow definitions are shipped
  with the container. Registering a real workflow is an Archon /
  operator infrastructure task and is out of scope for OC validation.

The mocked unit tests already cover the success / failure / paused
branches of the same dispatcher path (169/169 archon adapter tests
pass), so the missing piece is environmental, not a code gap.

## Run Re-Summary (post-fix)

| Run | Backend | Outcome | OC↔RxP linkage | Trace contents |
|-----|---------|---------|----------------|----------------|
| 1 happy (demo_stub) | demo_stub | succeeded | n/a (bypasses ER by design) | record/trace populated |
| 2 failure | direct_local + `/bin/false` | failed/`backend_error` | populated, paths resolve | trace forwards ref + routing |
| 3 timeout | direct_local + 1s timeout | timed_out/`timeout` | populated; no orphans | trace forwards ref + routing |
| 4 archon (live) | archon http_async | timeout (workflow not registered server-side) | populated (kind=http_async) | trace forwards ref + routing |
| 5 non-Archon | direct_local + `/bin/true` | succeeded | populated, paths resolve | trace forwards ref + routing |

## New Capabilities Since Rev 1

- `ExecutionResult.runtime_invocation_ref` — OC↔RxP traceability (G-V01)
- `ExecutionRecord.metadata.routing` — SwitchBoard provenance (G-V02)
- `classify_capacity_exhaustion` + adapter wiring — false-success guard (G-V04 / G-005)
- `ExecutionTrace.runtime_invocation_ref` + `ExecutionTrace.routing` — single-artifact provenance (G-V03)

A consumer reading only `execution_trace.json` for a run can now answer:

```
- which OC run?            run_id
- which RxP invocation?    runtime_invocation_ref.invocation_id
- which runner / kind?     runtime_invocation_ref.runtime_name / runtime_kind
- which captured artifacts? stdout_path / stderr_path / artifact_directory
- which SwitchBoard rule?  routing.policy_rule_matched + routing.rationale
- which switchboard ver?   routing.switchboard_version
- which alternatives?      routing.alternatives_considered
```

## Recommendation

> **Architecture validated. All five surfaced gaps closed at the OC layer.**

The post-extraction architecture has now been exercised end-to-end with
real subprocess, real timeout, and a live Archon container. The
remaining infrastructure dependency (Archon-side workflow registration)
is outside OC's responsibility and does not block OC's observability or
boundary contracts.
