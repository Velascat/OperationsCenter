# Managed Repo Audit System — Final Verification, Gap Analysis, and Lockdown (Rev 8)

**Verification date:** 2026-04-26 (Rev 8 — post Rev 7 confirmation pass)
**Test suite:** 2733 passing, 4 skipped (live SwitchBoard only), 0 failures, 1 expected warning
**Scope:** Phases 0–12, Anti-Collapse Invariant, Gap Closure
**Status:** LOCKED

---

## Gap Analysis

### Summary

| Revision | New gaps | Severity breakdown | All closed? |
|----------|----------|--------------------|-------------|
| Rev 1 | 11 | 0C / 2H / 5M / 4L | ✅ Yes |
| Rev 2 | 6 | 0C / 1H / 1M / 4L | ✅ Yes |
| Rev 3 | 2 | 0C / 0H / 0M / 2L | ✅ Yes |
| Rev 4 | 2 | 0C / 0H / 1M / 1L | ✅ Yes |
| Rev 5 | 0 | — | ✅ N/A |
| Rev 6 | 2 | 0C / 0H / 0M / 2L | ✅ Yes |
| Rev 7 | 0 | — | ✅ N/A |
| Rev 8 | 0 | — | ✅ N/A |

**Cumulative: 23 gaps identified across 8 revisions. All 23 closed. 0 open.**

No critical, high, medium, or low gaps remain. No invariant violations found.

---

### Critical Gaps

None.

---

### High Priority Gaps

None.

---

### Medium Gaps

None.

---

### Low Gaps

None.

---

### Suggested Follow-Up Tasks (Non-Implementation)

1. **First live VideoFoundry audit run** — Phase 5 code is wired but has never been executed against a live VF audit. Run `operations-center-governance run` against a live VF instance to validate Phase 5 outputs conform to the contract.

2. **CI integration guide** — Document how to wire `operations-center-governance run` into a CI/CD pipeline as a pre-release gate.

3. **Consider top-level `repo_id`/`audit_type` in `MiniRegressionSuiteReport`** — Currently traceability from suite report back to repo runs through individual fixture pack entries. A top-level field would make filtering and governance correlation easier.

4. **Validate governance evidence on load** — The `_check_mini_regression_first()` policy accepts `related_suite_report_path` as operator attestation without file-existence checks. If stricter validation is desired in future, consider an optional `validate_evidence_paths: bool = False` flag in `GovernanceConfig`.

---

### Closed Gaps (Rev 6)

| ID | Description | Closure |
|----|-------------|---------|
| gap_r6_001 | `_check_mini_regression_first` attestation model undocumented | Added docstring paragraph to `audit_governance/policy.py` explicitly stating that `related_suite_report_path` is an operator attestation — file existence and content are intentionally not validated; path recorded in audit trail |
| gap_r6_002 | No JSON schemas for Phase 8/10/11 persisted reports | Generated `schemas/behavior_calibration/calibration_report.schema.json`, `schemas/slice_replay/slice_replay_report.schema.json`, `schemas/mini_regression/suite_report.schema.json` from `model_json_schema()` |

---

### Closed Gaps — Full History

<details>
<summary>Rev 1 (11 gaps, all closed)</summary>

| ID | Description | Closure |
|----|-------------|---------|
| gap_001 | Cross-process file locking for budget/cooldown | `file_locks.py` with `fcntl.flock`; atomic read-modify-write |
| gap_002 | Phase 5 fake-producer integration test | `test_producer_contract_flow.py` — 6 tests |
| gap_003 | Phase 9→10→11 chain integration test | `test_fixture_to_regression_chain.py` — 2 tests |
| gap_004 | Governance CLI had no CliRunner tests | `test_governance_cli.py` — 18 tests; `--state-dir` added |
| gap_005 | Suite limitations never populated | `runner.py` aggregates `replay_report.limitations` |
| gap_006 | Empty `known_repos` → warning (permissive) | Changed to `status="failed"` |
| gap_007 | Missing negative/failure tests | `TestNegativePaths`, `TestFileLocking`, `TestDispatchedRunId` |
| gap_008 | `make_suite_run_id()` collision risk | `uuid4().hex[:8]` suffix; uniqueness test |
| gap_009 | No JSON schema for fixture packs | `schemas/fixture_harvesting/fixture_pack.schema.json` |
| gap_010 | Documentation polish | Docstrings + architecture doc updates |
| gap_011 | `dispatched_run_id` not accessible | `@property dispatched_run_id` on `AuditGovernanceReport` |

</details>

<details>
<summary>Rev 2 (6 gaps, all closed)</summary>

| ID | Description | Closure |
|----|-------------|---------|
| gap_r2_001 | Phase 5 VideoFoundry producer not delivered | All 6 audit CLIs wired (`ManagedRunFinalizer` / `_RunStatusFinalizer`) |
| gap_r2_002 | Empty `known_audit_types` → warning (permissive) | Changed to `status="failed"` |
| gap_r2_003 | `AuditGovernanceReport` lacks `governance_status` | Field added; runner populates all 4 code paths |
| gap_r2_004 | `file_locks.py` Linux/macOS requirement undocumented | Module docstring updated |
| gap_r2_005 | No CLI tests for `operations-center-regression` | `test_regression_cli.py` — 15 tests |
| gap_r2_006 | Replay `partial` → suite `passed` undocumented | `TestReplayPartialSemantics` — 3 tests |

</details>

<details>
<summary>Rev 3 (2 gaps, all closed)</summary>

| ID | Description | Closure |
|----|-------------|---------|
| gap_r3_001 | `AuditGovernanceReport.schema_version` not bumped after adding `governance_status` | Bumped to `"1.1"`; `UserWarning` on v1.0 load; changelog in `reports.py` docstring |
| gap_r3_002 | No external JSON schema for governance report | `schemas/governance/governance_report.schema.json` generated |

</details>

<details>
<summary>Rev 4 (2 gaps, all closed)</summary>

| ID | Description | Closure |
|----|-------------|---------|
| gap_r4_001 | `operations-center-audit` CLI governance bypass undocumented | `WARNING — GOVERNANCE BYPASS` block added to CLI docstring and arch doc |
| gap_r4_002 | Five CLI entrypoints lacked CliRunner tests | 49 new CliRunner tests added across 5 test files |

</details>

---

## Phase Completion Summary

| Phase | Name | Status | Tests |
|-------|------|--------|-------|
| Phase 0 | Ground Truth Discovery | ✅ Complete | 0 (docs only) |
| Phase 1 | Managed Repo Contract | ✅ Complete | 119 |
| Phase 2 | Artifact Contract Definition | ✅ Complete | incl. Phase 1+3 |
| Phase 3 | Audit Toolset Contract | ✅ Complete | 47 |
| Phase 4 | Run Identity / ENV Injection | ✅ Complete | 52 |
| Phase 5 | VideoFoundry Producer Contract | ✅ Complete | 6 (integration) |
| Phase 6 | Dispatch-Orchestrated Run Control | ✅ Complete | 94 |
| Phase 7 | Artifact Index + Retrieval | ✅ Complete | 78 |
| Phase 8 | Behavior Calibration | ✅ Complete | 102 |
| Anti-Collapse | Artifacts / Findings / Recommendations | ✅ Complete | incl. Phase 8 |
| Phase 9 | Fixture Harvesting | ✅ Complete | 78 |
| Phase 10 | Slice Replay Testing | ✅ Complete | 46 |
| Phase 11 | Mini Regression Suite | ✅ Complete | 58 (43 unit + 15 CLI) |
| Phase 12 | Full Audit Governance | ✅ Complete | 109 (91 unit + 18 CLI) |
| **CLI Tests** | All 7 entrypoints | ✅ Complete | **82** |
| **Integration** | Chain + Producer + Full-system | ✅ Complete | 18 passing (4 skipped live) |
| **Total** | | | **2733 passing** |

---

## System Boundary Summary

```
VideoFoundry (managed repo)              OperationsCenter
─────────────────────────────────────   ────────────────────────────────────────────────
Executes audits                          Defines all contracts and schemas
Produces artifacts                       Configures managed repos (YAML)
Writes run_status.json         ──────►   Reads run_status.json (Phase 3 discovery)
Writes artifact_manifest.json  ──────►   Reads + validates manifest (Phase 7)
                                         Generates AUDIT_RUN_ID (Phase 4)
                               ◄──────   Injects AUDIT_RUN_ID via ENV (Phase 6)
                               ◄──────   Dispatches external commands (Phase 6)
                                         Indexes artifacts (Phase 7)
                                         Analyzes behavior (Phase 8)
                                         Harvests fixtures (Phase 9)
                                         Replays slices (Phase 10)
                                         Runs mini regression suites (Phase 11)
                                         Governs full audits (Phase 12)
```

**Boundary rule:** OpsCenter never imports VideoFoundry code. All coordination is through files and subprocess invocation only.

**Note on `VideoFoundry*` names in OpsCenter vocabulary:** `VideoFoundryArtifactKind`, `VideoFoundryAuditType`, `VideoFoundrySourceStage` are OpsCenter-owned contract types defined in `audit_contracts/vocabulary.py` and `audit_contracts/profiles/videofoundry.py`. These are not imports of the VideoFoundry Python package — they are OpsCenter's own definitions of vocabulary for the VF producer profile.

---

## Contract Chain Verification

### run_status.json (Phase 2 contract)

- Schema: `schemas/audit_contracts/run_status.schema.json`
- Model: `ManagedRunStatus` in `audit_contracts/run_status.py`
- Schema/model field alignment: **0 delta** (verified by `symmetric_difference` check)
- Required fields: `schema_version`, `contract_name`, `producer`, `run_id`, `repo_id`, `audit_type`, `status`, `artifact_manifest_path`
- Validated by: 119 contract tests + 10 integration tests

### artifact_manifest.json (Phase 2 contract)

- Schema: `schemas/audit_contracts/artifact_manifest.schema.json`
- Model: `ManagedArtifactManifest` in `audit_contracts/artifact_manifest.py`
- Schema/model field alignment: **0 delta** (verified by `symmetric_difference` check)
- Validated by: 119 contract tests

### artifact_manifest.json → index (Phase 7)

- Loader: `load_artifact_manifest()` — sole entry point; no directory scanning
- Index builder: `build_artifact_index()` — resolves paths, handles repo singletons
- Validated by: 78 index tests + 10 integration tests

### fixture_pack (Phase 9)

- Schema: `schemas/fixture_harvesting/fixture_pack.schema.json`
- Provenance: `source_repo_id`, `source_run_id`, `source_audit_type`, `source_manifest_path`, `source_index_summary`
- Validated by: 78 harvesting tests

### governance_report (Phase 12)

- Schema: `schemas/governance/governance_report.schema.json` (schema_version 1.1)
- Model: `AuditGovernanceReport` in `audit_governance/models.py`
- Fields include: `governance_status` (added v1.1), `dispatched_run_id` (property)
- `load_governance_report()` emits `UserWarning` on v1.0 reports
- Validated by: 109 governance tests + 4 full-system integration tests

### Internal pipeline reports (Phases 8 / 10 / 11)

Schema files added in Rev 6 (gap_r6_002 closure):

- `BehaviorCalibrationReport` → `schemas/behavior_calibration/calibration_report.schema.json` (11 top-level properties)
- `SliceReplayReport` → `schemas/slice_replay/slice_replay_report.schema.json` (14 top-level properties)
- `MiniRegressionSuiteReport` → `schemas/mini_regression/suite_report.schema.json` (13 top-level properties)

---

## Runtime Chain Verification

### Dispatch → Discovery Chain

```
prepare_managed_audit_invocation()   [Phase 4: run_id generation + AUDIT_RUN_ID injection]
    → subprocess                     [Phase 6: managed audit process]
    → discover_post_execution()      [Phase 6: lifecycle, reads run_status.json]
    → artifact_manifest_path         [Phase 3: resolution from run_status]
    → load_artifact_manifest()       [Phase 7: manifest loader]
    → build_artifact_index()         [Phase 7: index builder]
```

Verified: 94 dispatch tests + integration chain.

### Fast Feedback Ladder

```
harvest_fixtures()           [Phase 9 — from manifest/index, no dispatch]
    → fixture_pack
    → run_slice_replay()     [Phase 10 — local, deterministic, no dispatch]
        → SliceReplayReport
    → run_mini_regression_suite()  [Phase 11 — orchestrates Phase 10, no dispatch]
        → MiniRegressionSuiteReport
```

Import boundary: none of these layers imports `audit_dispatch`. Verified by AST tests in 15 test modules.

### Full System Pipeline (End-to-End)

```
Phase 5: VF writes run_status.json + artifact_manifest.json
Phase 6: OpsCenter governance → approved → dispatch → VF subprocess
Phase 7: load_run_status_entrypoint() → load_artifact_manifest() → build_artifact_index()
Phase 8: analyze_artifacts() [advisory, read-only]
Phase 9: harvest_fixtures() → FixturePack
Phase 10: run_slice_replay() → SliceReplayReport
Phase 11: run_mini_regression_suite() → MiniRegressionSuiteReport
```

End-to-end verified by: `tests/integration/test_full_system_real_flow.py` (4 tests).

---

## Fast Feedback Ladder Verification

| Layer | Dispatch-free? | Local? | Deterministic? |
|-------|---------------|--------|----------------|
| Phase 9 (fixture harvest) | ✅ Yes | ✅ Yes | ✅ Yes (selector.py: deterministic ordering) |
| Phase 10 (slice replay) | ✅ Yes | ✅ Yes | ✅ Yes |
| Phase 11 (mini regression) | ✅ Yes | ✅ Yes | ✅ Yes |

Verified by AST checks: `audit_dispatch` not imported in `slice_replay`, `mini_regression`, or `fixture_harvesting`.

---

## Governance Verification

### Policy Evaluation (8 checks, evaluated in order)

1. `manual_request_required` — `requested_by` must be non-empty
2. `known_repo_required` — repo in `known_repos` (empty list → `failed`)
3. `known_audit_type_required` — audit_type configured (empty dict → `failed`)
4. `cooldown_policy` — minimum gap between runs enforced; atomic file-locked read
5. `budget_policy` — per-period run limit enforced; atomic file-locked increment
6. `mini_regression_first_policy` — Phase 11 evidence required for low/normal urgency; path is operator attestation (documented in policy.py docstring)
7. `urgent_override_policy` — high/urgent always requires manual approval
8. `recent_success_policy` — advisory; recent run within cooldown window

### Budget / Cooldown State Integrity

- Both `increment_budget_after_dispatch()` and `update_cooldown_after_dispatch()` call `locked_state_file()` for atomic reads and writes
- Budget period rollover: `now > state.period_end` triggers fresh period (strict greater-than — runs at exactly `period_end` count within the current period)
- Cooldown: `elapsed < cooldown_seconds` — consistent with `AuditCooldownState.is_in_cooldown()`

### Governance Bypass (Documented Escape Hatch)

`operations-center-audit run` calls `dispatch_managed_audit()` directly, bypassing all 8 policy checks. This is a documented, intentional Phase 6 escape hatch with `WARNING — GOVERNANCE BYPASS` blocks in both:
- `src/operations_center/entrypoints/audit/main.py` module docstring
- `docs/architecture/managed_repo_audit_dispatch.md`

### Governance Report Schema Migration

- **v1.0** (pre-Rev 3): no `governance_status` field; `load_governance_report()` emits `UserWarning`
- **v1.1** (Rev 3+): `governance_status` persisted by runner in all 4 decision paths

### Runner Decision Path Coverage (all 5 `governance_status` values)

| Path | `governance_status` | Evidence |
|------|---------------------|----------|
| `requires_manual_approval` (no prior approval) | `needs_manual_approval` | runner.py:173,181 |
| Non-approved decision (denied/deferred) | `denied` or `deferred` via `status_map` | runner.py:192,211,228,237 |
| Dispatch raises exception | `dispatch_failed` | runner.py:268,278 |
| Dispatch succeeds | `approved_and_dispatched` | runner.py:311,322 |

---

## Anti-Collapse Verification

### Guardrail Chain

```
CalibrationRecommendation
    → assert_no_mutation_fields()     [raises GuardrailViolation on forbidden fields]
    → enforce_requires_human_review() [approved_by required]
    → CalibrationDecision             [explicit human gate]
```

### Forbidden Mutation Fields

`_FORBIDDEN_MUTATION_FIELDS`: `auto_apply`, `execute`, `apply`, `run`, `dispatch`, `mutate`, `write`, `deploy`, `publish`. Presence in a `CalibrationRecommendation` raises `GuardrailViolation`.

Verified by: 102 behavior calibration tests (includes 35 anti-collapse tests).

---

## Import Boundary Verification

### Forbidden Imports (0 matches)

```
^from videofoundry   → 0 matches in src/operations_center/
^import videofoundry → 0 matches in src/operations_center/
```

Checked with exact line-start anchors to distinguish the VideoFoundry external package from OpsCenter's own `VideoFoundry*` vocabulary types.

### `VideoFoundry*` Vocabulary in OpsCenter

`VideoFoundryArtifactKind`, `VideoFoundryAuditType`, `VideoFoundrySourceStage` are OpsCenter-defined enums in `audit_contracts/vocabulary.py` and `audit_contracts/profiles/videofoundry.py`. They are referenced by OpsCenter modules at import time. This is not a boundary violation — these are OpsCenter's own canonical definitions for the VF producer contract, not imports of the VideoFoundry Python package.

### Unidirectional Import Graph

```
audit_governance   → audit_dispatch
mini_regression    → slice_replay
slice_replay       → fixture_harvesting
fixture_harvesting → artifact_index
artifact_index     → audit_contracts
behavior_calibration → artifact_index
```

No reverse edges. AST boundary checks present in 15 test modules (unit + integration).

### Chain Isolation (AST-verified)

- `slice_replay` does not import `audit_dispatch` ✅
- `mini_regression` does not import `audit_dispatch` ✅
- `fixture_harvesting` does not import `audit_dispatch` ✅
- `audit_governance` does not import `fixture_harvesting` ✅
- `audit_governance` does not import `slice_replay` ✅

---

## Discovery / No-Scanning Verification

The only permitted path to artifact data:

```
run_status.json → artifact_manifest_path → artifact_manifest.json → artifact_index
```

- No directory scanning in `artifact_index/`
- No path inference anywhere in the pipeline
- No fallback crawling in `audit_toolset/discovery.py`
- `ArtifactManifestPathMissingError` raised when `artifact_manifest_path` is absent
- `load_artifact_manifest()` is the sole entry point for all artifact consumers

Verified by: 47 toolset tests + 10 integration tests.

---

## Provenance Verification

Every `FixturePack` carries:
- `source_repo_id`, `source_run_id`, `source_audit_type`, `source_manifest_path`, `source_index_summary`

Every `FixtureArtifact` carries:
- `source_artifact_id`, `checksum` (SHA-256), `copied` (bool)

`load_fixture_pack()` validates all `copied=True` artifacts exist on disk before returning.

Verified by: 78 harvesting tests (Invariant 9 coverage).

---

## Global Invariants — All Verified

| # | Invariant | Status | Evidence |
|---|-----------|--------|----------|
| 1 | No managed repo imports | ✅ PASS | 0 `^from videofoundry` / `^import videofoundry` matches; AST checks in 15 modules; `VideoFoundry*` enums are OpsCenter-owned types |
| 2 | Contract ownership | ✅ PASS | All schemas and models in `src/operations_center/`; 7 schema files across 6 subdirectories |
| 3 | Discovery chain | ✅ PASS | `audit_toolset/discovery.py`; no scanning; 47+10 tests |
| 4 | Manifest as source of truth | ✅ PASS | `load_artifact_manifest()` sole entry point |
| 5 | Run identity authority | ✅ PASS | `run_identity/generator.py`; `secrets.token_hex(4)`+timestamp |
| 6 | One audit per repo | ✅ PASS | `audit_dispatch/locks.py`; `RepoLockAlreadyHeldError` |
| 7 | No auto-collapse | ✅ PASS | `behavior_calibration/guardrails.py`; forbidden field check |
| 8 | Recommendations are advisory | ✅ PASS | No `auto_apply`/`execute`; `CalibrationDecision.approved_by` required |
| 9 | Fixture packs preserve provenance | ✅ PASS | 5 pack + 3 artifact provenance fields; disk validation |
| 10 | Replay is local and deterministic | ✅ PASS | No dispatch import; no subprocess; 46 tests |
| 11 | Mini regression does not escalate | ✅ PASS | No dispatch import; 58 tests |
| 12 | Full audit governance gates heavy runs | ✅ PASS | `run_governed_audit()` enforces all 8 policies; bypass documented as intentional escape hatch |
| 13 | Mini regression first | ✅ PASS | `_check_mini_regression_first()`; attestation model documented in policy.py docstring; all branches tested |

---

## Known Non-Goals

```
✗ Full audit scheduling (daemon, watch loop, cron)
✗ Self-tuning governance policy
✗ Auto-apply calibration recommendations
✗ Managed repo Python code imports
✗ Artifact mutation by OperationsCenter
✗ Replay/regression calling dispatch
✗ CI gate integration (future phase)
✗ Distributed lock coordination
✗ Windows support for file locking (Linux/macOS only)
✗ File-existence validation of governance evidence paths (attestation model by design)
```

---

## Remaining Risks

1. **First live VideoFoundry run (low)** — Phase 5 code is wired but has never executed against a real live audit. Fake-producer integration tests validate the contract. Format differences remain a theoretical risk until first live run completes.

2. **fcntl Linux-only (low)** — Documented; not a current deployment risk.

3. **Governance evidence attestation (low, documented)** — `_check_mini_regression_first()` accepts a non-empty path string as evidence without file-existence validation. The attestation model is documented in the policy function's docstring and in this report.

---

## Lockdown Rules

The following rules are declared permanent:

```
1. No new code may bypass Phase 1–12 contracts.
2. No new discovery paths may bypass run_status.json.
3. No new artifact consumers may bypass the manifest/index.
4. No calibration output may mutate runtime.
5. No replay/regression layer may call dispatch.
6. No dispatch may bypass governance.
7. No new governance approvals may bind on decision_id alone (must use request_id).
8. No file-backed state may be read/written without holding the fcntl exclusive lock.
9. No audit type may be approved if known_audit_types is empty (deny by default).
10. No governance_report.json may omit governance_status (schema v1.1 enforces this).
```

---

## Final Lockdown Statement

The managed repo audit system across Phases 0–12 is declared **locked** as of 2026-04-26 (Rev 8).

**Verification status:**
- All 13 invariants hold (all ✅ PASS)
- 2733 tests pass (unit + integration + CLI), 0 failures, 4 skipped (live service only), 1 expected warning
- No forbidden imports found in any source module
- Correct unidirectional import graph enforced by AST checks in 15 test modules
- All 23 lifetime gaps closed; 0 open
- No critical, high, medium, or low gaps remain
- No invariant violations
- All persisted artifacts have JSON schemas (7 schema files across 6 subdirectories)
- run_status.json and artifact_manifest.json schemas verified 0-delta against Pydantic models

**The system is architecturally complete and gap-free.** Eight verification passes across this codebase have found and closed 23 gaps; both Rev 7 and Rev 8 passes found none. The gap detection pass ran 14 checks, all clean. The 3 suggested follow-up tasks are enhancements, not correctness issues.
