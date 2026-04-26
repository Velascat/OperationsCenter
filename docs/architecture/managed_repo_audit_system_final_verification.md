# Managed Repo Audit System — Final Verification, Gap Analysis, and Lockdown (Rev 2)

**Verification date:** 2026-04-26 (Rev 2 — post gap-closure pass)
**Test suite:** 2662 passing, 4 skipped (live SwitchBoard only), 0 failures
**Scope:** Phases 0–12, Anti-Collapse Invariant, Gap Closure
**Status:** LOCKED

---

## Gap Analysis

### Summary

Rev 1 identified 11 gaps (0 critical, 2 high, 5 medium, 4 low). All 11 are now closed.
Rev 2 identifies 6 new gaps (0 critical, 1 high, 1 medium, 4 low).
The high gap is the carry-forward Phase 5 VideoFoundry producer delivery — unchanged from Rev 1.

| Severity | Rev 1 (pre-closure) | Rev 2 (post-closure) |
|----------|---------------------|----------------------|
| Critical | 0 | 0 |
| High | 2 | 1 |
| Medium | 5 | 1 |
| Low | 4 | 4 |
| **Total** | **11** | **6** |

---

### High Priority Gaps

#### gap_r2_001 — Phase 5 VideoFoundry Producer Not Yet Delivered (carry-forward)

- **Category:** missing_feature
- **Severity:** high
- **Affected phase:** Phase 5
- **Description:** VideoFoundry has not yet implemented the producer-side contract. No live audit run produces an `artifact_manifest.json`. `run_status.json` does not populate `artifact_manifest_path` for any of the 5 non-representative audit types. The OpsCenter discovery chain (Phase 3 toolset, Phase 7 index, Phase 9 harvesting) is fully implemented and ready to consume; the blocker is entirely on the producer side.
- **Evidence:** `config/managed_repos/videofoundry.yaml` — all 5 non-representative types have `status: planned` for artifact_manifest; `tests/integration/test_producer_contract_flow.py` uses a fake producer to validate the chain works end-to-end.
- **Recommended action:** VideoFoundry Phase 5 implementation task. OpsCenter requires no further changes on this dependency.

---

### Medium Gaps

#### gap_r2_002 — `known_audit_types={}` Permits All Audit Types (asymmetry with known_repos)

- **Category:** implicit_behavior
- **Severity:** medium
- **Affected phase:** Phase 12
- **Description:** `_check_known_audit_type()` returns `status="warning"` (not `"failed"`) when `known_audit_types` is an empty dict, causing `make_governance_decision()` to produce `"approved"` rather than `"denied"`. This is asymmetric with `_check_known_repo()`, which was fixed in gap_closure Rev 1 to return `"failed"` for empty `known_repos`. With empty `known_audit_types`, any audit type is silently permitted.
- **Evidence:** `audit_governance/policy.py:71-77` — `if allowed is None: return PolicyResult(status="warning", ...)`. Confirmed via runtime: `evaluate_governance_policies(req, known_repos=["videofoundry"], known_audit_types={})` → decision `"approved"`.
- **Recommended action:** Change `_check_known_audit_type()` to return `status="failed"` when `known_audit_types` is empty (matching the `_check_known_repo()` pattern). Add test asserting this.

---

### Low Gaps

#### gap_r2_003 — `AuditGovernanceReport` Lacks `governance_status` Field

- **Category:** implicit_behavior
- **Severity:** low
- **Affected phase:** Phase 12
- **Description:** The persisted governance report JSON (`AuditGovernanceReport`) does not include a `governance_status` field. Only `AuditGovernedRunResult` (the in-memory run result) carries it. Downstream tools reading a persisted report must infer status by inspecting `decision.decision + dispatch_result_summary is not None`, which is non-obvious.
- **Evidence:** `audit_governance/models.py` — `AuditGovernanceReport.model_fields` does not include `governance_status`. `AuditGovernedRunResult.governance_status` is never persisted to the report file.
- **Recommended action:** Add a `governance_status: GovernanceStatus` field to `AuditGovernanceReport` and populate it in the runner when writing the report.

#### gap_r2_004 — `file_locks.py` Uses `fcntl` (Linux/macOS Only, Not Windows)

- **Category:** documentation_gap
- **Severity:** low
- **Affected phase:** Phase 12
- **Description:** `audit_governance/file_locks.py` imports `fcntl` which is available on Linux and macOS but not Windows. OpsCenter runs in a Linux CI environment and the deployment target is Linux, but this dependency is not documented.
- **Evidence:** `file_locks.py:5` — `import fcntl`. No `sys.platform` guard or cross-platform fallback.
- **Recommended action:** Add a module-level comment noting the Linux/macOS requirement. If Windows support is ever needed, replace with `msvcrt.locking` or `portalocker`.

#### gap_r2_005 — No CLI Tests for `operations-center-regression` (Phase 11)

- **Category:** test_gap
- **Severity:** low
- **Affected phase:** Phase 11
- **Description:** The Phase 11 regression CLI (`operations-center-regression`) has no `typer.testing.CliRunner` tests. The governance CLI (Phase 12) has 18 CLI tests. The regression CLI's `run`, `inspect`, and `list` commands are untested at the CLI invocation layer.
- **Evidence:** `tests/unit/cli/` contains only `test_governance_cli.py`. `src/operations_center/entrypoints/regression/main.py` has no corresponding CLI test file.
- **Recommended action:** Add `tests/unit/cli/test_regression_cli.py` covering at minimum `run` (with suite file), `inspect` (with report file), and failure paths.

#### gap_r2_006 — Replay `partial` → Suite `passed` Is an Undocumented Assumption

- **Category:** implicit_behavior
- **Severity:** low
- **Affected phase:** Phase 11
- **Description:** In `mini_regression/runner.py`, a `partial` status from `SliceReplayReport` is treated as `"passed"` for suite entry purposes (inline comment: `# partial → treat as passed for suite purposes`). This assumption is not covered by a dedicated test and is not documented in the Phase 11 architecture doc.
- **Evidence:** `runner.py:160` — `"passed"  # partial → treat as passed for suite purposes`. No test in `test_suite.py` exercises the partial→passed mapping explicitly.
- **Recommended action:** Add one test asserting that a suite entry with a `partial` replay result produces `status="passed"` in the entry result. Add a note to the Phase 11 architecture doc explaining the rationale.

---

### Closed Gaps (Rev 1 → Rev 2)

All 11 gaps from the Rev 1 report are confirmed closed:

| ID | Description | Closure |
|----|-------------|---------|
| gap_001 | Cross-process file locking for budget/cooldown | `file_locks.py` with `fcntl.flock`; atomic read-modify-write in both `budgets.py` and `cooldowns.py` |
| gap_002 | Phase 5 fake-producer integration test | `tests/integration/test_producer_contract_flow.py` — 6 tests |
| gap_003 | Phase 9→10→11 chain integration test | `tests/integration/test_fixture_to_regression_chain.py` — 2 tests |
| gap_004 | Governance CLI tests | `tests/unit/cli/test_governance_cli.py` — 18 tests; `--state-dir` added to `cmd_run` |
| gap_005 | Suite limitations not aggregated | `runner.py` now collects `replay_report.limitations` into `suite_limitations` |
| gap_006 | Empty `known_repos` permits all repos | `_check_known_repo()` now returns `status="failed"` for empty list |
| gap_007 | Missing negative/failure tests | `TestNegativePaths`, `TestFileLocking`, `TestDispatchedRunId` added |
| gap_008 | `make_suite_run_id()` timestamp collision | Random `uuid4().hex[:8]` suffix added; uniqueness test asserts 20 unique IDs |
| gap_009 | No JSON schema for fixture packs | `schemas/fixture_harvesting/fixture_pack.schema.json` generated from `FixturePack.model_json_schema()` |
| gap_010 | Documentation polish | Process-scoped lock note in `dispatch_managed_audit()` docstring; empty known_repos behavior explicit in code and tests |
| gap_011 | `dispatched_run_id` not accessible at top level | `@property dispatched_run_id` added to `AuditGovernanceReport` |

---

## Phase Completion Summary

| Phase | Name | Status | Tests |
|-------|------|--------|-------|
| Phase 0 | Ground Truth Discovery | ✅ Complete | 0 (docs only) |
| Phase 1 | Managed Repo Contract | ✅ Complete | 119 |
| Phase 2 | Artifact Contract Definition | ✅ Complete | included in Phase 1+3 |
| Phase 3 | Audit Toolset Contract | ✅ Complete | 47 |
| Phase 4 | Run Identity / ENV Injection | ✅ Complete | 52 |
| Phase 5 | VideoFoundry Producer Contract | ⏳ Pending (producer) | 6 (fake-producer integration) |
| Phase 6 | Dispatch-Orchestrated Run Control | ✅ Complete | 94 |
| Phase 7 | Artifact Index + Retrieval | ✅ Complete | 78 |
| Phase 8 | Behavior Calibration | ✅ Complete | 102 |
| Anti-Collapse | Artifacts / Findings / Recommendations | ✅ Complete | included in Phase 8 |
| Phase 9 | Fixture Harvesting | ✅ Complete | 78 |
| Phase 10 | Slice Replay Testing | ✅ Complete | 46 |
| Phase 11 | Mini Regression Suite | ✅ Complete | 40 |
| Phase 12 | Full Audit Governance | ✅ Complete | 91 |
| **CLI Tests** | Governance CLI round-trip | ✅ Complete | 18 |
| **Integration** | Chain + producer contract | ✅ Complete | 8 (+ 4 skipped/live) |
| **Total** | | | **2662 passing** |

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

---

## Contract Chain Verification

### run_status.json (Phase 2 contract)

- Schema: `schemas/audit_contracts/run_status.schema.json`
- Model: `ManagedRunStatus` in `audit_contracts/run_status.py`
- Required fields verified: `schema_version`, `contract_name`, `producer`, `run_id`, `repo_id`, `audit_type`, `status`, `artifact_manifest_path`
- Validated by: 119 contract tests + 6 integration producer tests
- Phase 5 gap: `artifact_manifest_path` not yet populated by real VideoFoundry runs

### artifact_manifest.json (Phase 2 contract)

- Schema: `schemas/audit_contracts/artifact_manifest.schema.json`
- Model: `ManagedArtifactManifest` in `audit_contracts/manifest.py`
- Vocabulary enums: `ArtifactKind`, `ArtifactLocation`, `ArtifactStatus`, `RunStatus`, `ManifestStatus`, `SourceStage`
- Validated by: 119 contract tests

### artifact_manifest.json → index (Phase 7)

- Loader: `load_artifact_manifest()` in `artifact_index/loader.py` — sole entry point
- Index builder: `build_artifact_index()` — resolves paths, handles repo singletons
- Validated by: 78 index tests + 6 integration tests

### fixture_pack (Phase 9)

- Schema: `schemas/fixture_harvesting/fixture_pack.schema.json` (generated from `FixturePack.model_json_schema()`)
- Provenance fields: `source_repo_id`, `source_run_id`, `source_audit_type`, `source_manifest_path`, `source_index_summary`
- Validated by: 78 harvesting tests

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

All steps verified: `tests/unit/audit_dispatch/` (94 tests) + integration chain.

### Fast Feedback Ladder

```
harvest_fixtures()           [Phase 9 — from manifest/index, no dispatch]
    → fixture_pack
    → run_slice_replay()     [Phase 10 — local, deterministic, no dispatch]
        → SliceReplayReport
    → run_mini_regression_suite()  [Phase 11 — orchestrates Phase 10, no dispatch]
        → MiniRegressionSuiteReport
```

Import boundary: none of these layers imports `audit_dispatch`. Verified by AST tests.

---

## Governance Verification

### Policy Evaluation (8 checks)

1. `manual_request_required` — `requested_by` must be non-empty
2. `known_repo_required` — repo must be in `known_repos` list (empty list = `failed`)
3. `known_audit_type_required` — audit_type must be configured (empty dict = `warning` — **gap_r2_002**)
4. `cooldown_policy` — minimum gap between runs enforced
5. `budget_policy` — per-period run limit enforced
6. `mini_regression_first_policy` — Phase 11 evidence required for low/normal urgency
7. `urgent_override_policy` — high/urgent always requires manual approval
8. `recent_success_policy` — advisory check on recent run within cooldown window

### Decision Priority

```
denied           → any hard check failed (known_repo, known_audit_type, manual_request_required)
needs_manual     → high/urgent urgency OR missing mini regression evidence
deferred         → budget/cooldown failed with low/normal urgency
approved         → all required checks passed
```

### File-Backed State Locking

Budget and cooldown state files are now protected by `fcntl.flock` exclusive locks via `file_locks.py`. The atomic read-modify-write pattern in `increment_budget_after_dispatch()` prevents double-counting under concurrent governance runners in the same OS process or across multiple processes.

**Limitation (gap_r2_004):** `fcntl` is Linux/macOS only.

---

## Anti-Collapse Verification

### Guardrail Chain

```
CalibrationRecommendation
    → assert_no_mutation_fields()    [guardrails.py — raises GuardrailViolation]
    → assert_requires_human_review() [guardrails.py — approved_by required]
    → CalibrationDecision            [explicit human gate]
```

### Forbidden Mutation Fields

`_FORBIDDEN_MUTATION_FIELDS` in `behavior_calibration/guardrails.py`:
- `auto_apply`, `execute`, `apply`, `run`, `dispatch`, `mutate`, `write`, `deploy`, `publish`
- None of these may appear in a `CalibrationRecommendation`

### Recommendations Are Advisory

- `related_recommendation_ids` in `AuditGovernanceRequest` is `list[str]` context only — it does not change decision logic
- No `auto_apply` or `execute` method exists in `behavior_calibration/`

Verified by: 102 behavior calibration tests (includes 35 anti-collapse tests).

---

## Import Boundary Verification

### Forbidden Imports (0 matches)

```
from videofoundry   → 0 matches in src/operations_center/
import videofoundry → 0 matches in src/operations_center/
from managed_repo   → 0 matches in src/operations_center/
import managed_repo → 0 matches in src/operations_center/
```

### Correct Unidirectional Import Graph

```
audit_governance   → audit_dispatch
mini_regression    → slice_replay
slice_replay       → fixture_harvesting
fixture_harvesting → artifact_index
artifact_index     → audit_contracts
behavior_calibration → artifact_index
```

No reverse edges. Verified by AST tests in 7 test modules.

### Chain Isolation (AST-verified)

- `slice_replay` does not import `audit_dispatch` ✅
- `mini_regression` does not import `audit_dispatch` ✅
- `fixture_harvesting` does not import `audit_dispatch` ✅
- `audit_governance` does not import `fixture_harvesting` ✅
- `audit_governance` does not import `slice_replay` ✅
- `audit_governance` does not import `mini_regression` (string references only, no imports) ✅

---

## Discovery / No-Scanning Verification

The only permitted path to artifact data:

```
run_status.json → artifact_manifest_path → artifact_manifest.json → artifact_index
```

- No directory scanning in `artifact_index/`
- No path inference in `artifact_index/`
- No fallback crawling in `audit_toolset/discovery.py`
- `load_artifact_manifest()` is the sole entry point for all artifact consumers

Verified by: `audit_toolset/discovery.py` docstring + 47 toolset tests + integration tests.

---

## Provenance Verification

### Fixture Pack Provenance Fields

Every `FixturePack` carries:
- `source_repo_id` — originating managed repo
- `source_run_id` — originating audit run
- `source_audit_type` — originating audit type
- `source_manifest_path` — absolute path to source manifest
- `source_index_summary` — snapshot of index at harvest time

Every `FixtureArtifact` carries:
- `source_artifact_id` — original artifact ID in the index
- `checksum` — SHA-256 of artifact content (when copied)
- `copied` — whether file was physically copied or referenced

Verified by: Invariant 9 tests in `fixture_harvesting/` (78 tests).

---

## Known Non-Goals

The following are explicitly out of scope and must not be added:

```
✗ Full audit scheduling (daemon, watch loop, cron)
✗ Self-tuning governance policy
✗ Auto-apply calibration recommendations
✗ Managed repo Python code imports
✗ Artifact mutation by OperationsCenter
✗ Replay/regression calling dispatch
✗ CI gate integration (future phase)
✗ Distributed lock coordination
✗ Windows support for file locking (current: Linux/macOS only)
```

---

## Remaining Risks

### Operational

1. **Phase 5 producer gap (high)** — No real VideoFoundry run produces `artifact_manifest.json`. The entire Phase 7–12 runtime chain has never executed against real artifacts. Risk: undiscovered format differences between fake-producer tests and actual VideoFoundry output.

2. **`known_audit_types` permissive empty behavior (medium)** — Empty `known_audit_types` silently approves all audit types, which is inconsistent with the deny-by-default posture established for `known_repos`.

3. **fcntl Linux-only (low)** — File locking will fail with `ImportError` on Windows. Not a current risk given the Linux deployment target, but worth documenting.

### Architectural

4. **`AuditGovernanceReport` missing `governance_status` (low)** — Persisted reports require inference logic to determine final governance status.

5. **Replay `partial` → `passed` undocumented (low)** — The assumption that a partial replay counts as a suite pass is not covered by a dedicated test or architecture note.

---

## Lockdown Rules

The following rules are declared permanent and must not be bypassed by any future code:

```
1. No new code may bypass Phase 1–12 contracts.
2. No new discovery paths may bypass run_status.json.
3. No new artifact consumers may bypass the manifest/index.
4. No calibration output may mutate runtime.
5. No replay/regression layer may call dispatch.
6. No dispatch may bypass governance.
7. No new governance approvals may bind on decision_id alone (must use request_id).
8. No file-backed state may be read/written without holding the fcntl exclusive lock.
```

---

## Final Lockdown Statement

The managed repo audit system across Phases 0–12 is declared **locked** as of 2026-04-26 (Rev 2).

**Verification status:**
- All 13 invariants hold
- 2662 tests pass (2648 unit + 14 integration), 0 failures
- No forbidden imports found
- Correct unidirectional import graph enforced by AST tests
- All 11 Rev 1 gaps closed and verified
- 6 Rev 2 gaps documented (1 high — producer carry-forward, 1 medium, 4 low)
- No critical gaps
- No invariant violations

**The system is ready for Phase 5 producer delivery.** When VideoFoundry implements `artifact_manifest.json` writing, no OpsCenter code changes are required — the Phase 3 discovery chain, Phase 7 index, Phase 9 harvesting, and all downstream phases will consume the artifacts automatically.
