# Backlog

_Durable work inventory. Update after each meaningful chunk of progress._

## In Progress

_(none — system locked at Rev 10)_

## Up Next

- [x] **OpsCenter ↔ Custodian coverage bridge (2026-05-04, on main)**: Closes the dynamic-coverage loop. New `audit_governance/coverage_analysis.py` uses Phase 7 manifest index to find `coverage.json` from a dispatch result and subprocess-invokes `custodian audit --enable-coverage --coverage-json <path>` against the consuming repo. Findings attached to the governance report as `CoverageAuditSummary` (cv1/cv2/cv3 counts). Opt-in via `run_coverage_audit: bool` on `AuditGovernanceRequest` — default False. Schema bumped 1.1 → 1.2. 10 new tests; full unit suite 2094 pass.

- [x] **Phase 7 — multi-run historical artifact index + CLI (2026-05-04, on main)**: Single-manifest layer was already complete; this round added the missing multi-run layer. New `artifact_index/multi_run.py` with `discover_manifest_files`, `IndexedRun`, `MultiRunArtifactIndex` (federated `query()`, `find_run_by_prefix` git-style ambiguity error, `resolve(..., recheck_exists=True)` re-stat at lookup), `build_multi_run_index`. Failed-load handling: corrupt manifests become `IndexedRun(load_error=..., index=None)`. New `artifact_index/cli.py` with `index / index-show / get-artifact` (Rich + `--json`); mounted flat into `operations-center-audit`. Architecture-invariant test relaxed to exempt `multi_run.py` + `cli.py`. 41 new tests; full unit suite 2082 pass.

- [x] **Phase 6 — dispatch control crash-safety + dual-PID tracking (2026-05-04, branch phase6-dispatch-control)**: All 6 slices complete. New `audit_dispatch/lock_store.py` (PersistentLockStore + dual-PID payload, atomic writes, fcntl sentinel via audit_governance/file_locks); `locks.py` refactored as façade over the store with full-identity acquire signature; `executor.execute()` accepts `on_spawn(pid, pgid)` callback; `api.py` carries identity through; stale-PID reclaim + lazy first-use sweep; new CLI commands `list-active / unlock / dispatch / watch` on `operations-center-audit`; cross-process concurrency proof test; in-flight run_status watcher (polling, no watchdog dep). Sentinel-glob bug fixed (`_iter_lock_files` filters `.lock.lock` recursive sentinels). Tests: 64 new + all existing passing. Full unit suite 2041 pass.

- [ ] Phase 13 or next operator directive TBD

- [ ] **ER-000 — Phase 0 Golden Tests (own merge)**: Freeze current behavior before primitives land. Tests: (1) one-shot path OperatorConsole → OperationsCenter → SwitchBoard → result, plus backend-unavailable error result; (2) existing ExecutionRequest / ExecutionResult / LaneDecision examples validate; (3) boundary checks — no VideoFoundry runtime imports inside OperationsCenter, plus an allowlist/denylist test utility for SwitchBoard package (denylist may include forward-looking symbols like SwarmCoordinator, LifecycleRunner; explicitly forward-looking, will pass trivially today); (4) primary CLI smoke — command reaches execution path, output shape unchanged. DoD: tests exist and pass, no behavior change.

- [ ] **ER-001 — Repo Graph Primitive**: Minimal graph for repo identity + impact queries. Edges (v1): `depends_on_contracts_from`, `dispatches_to`, `routes_through`. Node metadata: `repo_id`, `canonical_name`, `legacy_names`, `local_path`, `github_url`, `runtime_role` (no `boundary_rules` — no consumer). Behavior: legacy → canonical resolution, upstream/downstream queries, affected-repos-from-contract-change, read-only context provider for OperationsCenter and SwitchBoard. Source of truth: checked-in YAML config. DoD: alias resolution works (`ControlPlane` → `OperationsCenter`, `FOB` → `OperatorConsole`), impact queries work, no execution behavior changed.

- [ ] **ER-002 — Run Memory Primitive**: Deterministic file-backed run memory index. Storage: JSONL, rebuildable. `RunMemoryRecord` fields: `record_id`, `run_id`, `request_id`, `result_id`, `repo_id`, `artifact_paths`, `contract_kinds` (free-form string list, NOT enum in v1), `status`, `summary`, `tags`, `created_at`, `source_type`. `record_id` is **deterministic from `result_id`** (e.g., stable hash) so rebuilds are idempotent. `source_type` v1 = `execution_result` only (no `representative_audit`/`slice_replay` until confirmed). Text query = substring across `summary`, `tags`, `artifact_paths`, `repo_id`, `run_id` — no fuzzy / no embeddings / no scoring. **Single write site**: OperationsCenter, after execution result is finalized, no duplication. **Rebuild source**: scan persisted `ExecutionResult` artifacts on disk, regenerate JSONL; no secondary sources in v1. DoD: success+failure indexed, deterministic queries, rebuild works.

- [ ] **ER-003 — Lifecycle Primitive**: Minimal lifecycle with concrete I/O. Stages: `plan`, `execute`, `verify`. Stage policy v1: `stop_on_first_failure`, `run_all_best_effort` (no `manual_gate_between_stages` — no mechanism). I/O: `plan` (in: request + repo graph context; out: `plan_summary`, `target_repos`, `steps`, `checks` for the verify stage); `execute` (in: plan output + request; out: result reference, status); `verify` (in: execution result + checks **emitted by the plan stage**; out: `verification_status`, `checks` list `[{check_id, passed, reason|null}]`, `failures` derived). Optional `lifecycle` field on `ExecutionRequest`. DoD: lifecycle is optional, transitions deterministic, every stage has explicit I/O, no prompt-label-only stages.

- [ ] **ER-004 — Swarm Primitive (DEFERRED)**: Not approved for implementation. Entry criteria (all required before kickoff): (1) real workflow fails without swarm, (2) one-shot insufficient, (3) lifecycle insufficient, (4) required roles defined, (5) merge behavior defined. If unmet → DO NOT IMPLEMENT.

## Done

- [x] Phase 0: Ground truth audit discovery
- [x] Phase 1: Managed repo config contract — 26 tests
- [x] Phase 2: Artifact contract definition — 119 tests
- [x] Phase 3: Audit toolset contract — 47 tests
- [x] Phase 4: Run identity / ENV injection — 52 tests
- [x] Phase 5: VideoFoundry artifact manifest writing — ManagedRunFinalizer wired in all 5 CLIs
- [x] Phase 6: Dispatch-orchestrated run control
- [x] Phase 7: Artifact index + retrieval
- [x] Phase 8: Behavior calibration
- [x] Phase 9: Fixture harvesting
- [x] Phase 10: Slice replay testing
- [x] Phase 11: Mini regression suite
- [x] Phase 12: Full audit governance
- [x] Rev 1–10 verification passes: all 23 lifetime gaps closed; 14/14 invariants; 2733 tests passing
