# Backlog

_Durable work inventory. Update after each meaningful chunk of progress._

## In Progress

_(none — system locked at Rev 10)_

## Up Next

- [x] **OpsCenter ↔ Custodian coverage bridge (2026-05-04, on main)**: Closes the dynamic-coverage loop. New `audit_governance/coverage_analysis.py` uses Phase 7 manifest index to find `coverage.json` from a dispatch result and subprocess-invokes `custodian audit --enable-coverage --coverage-json <path>` against the consuming repo. Findings attached to the governance report as `CoverageAuditSummary` (cv1/cv2/cv3 counts). Opt-in via `run_coverage_audit: bool` on `AuditGovernanceRequest` — default False. Schema bumped 1.1 → 1.2. 10 new tests; full unit suite 2094 pass.

- [x] **Phase 7 — multi-run historical artifact index + CLI (2026-05-04, on main)**: Single-manifest layer was already complete; this round added the missing multi-run layer. New `artifact_index/multi_run.py` with `discover_manifest_files`, `IndexedRun`, `MultiRunArtifactIndex` (federated `query()`, `find_run_by_prefix` git-style ambiguity error, `resolve(..., recheck_exists=True)` re-stat at lookup), `build_multi_run_index`. Failed-load handling: corrupt manifests become `IndexedRun(load_error=..., index=None)`. New `artifact_index/cli.py` with `index / index-show / get-artifact` (Rich + `--json`); mounted flat into `operations-center-audit`. Architecture-invariant test relaxed to exempt `multi_run.py` + `cli.py`. 41 new tests; full unit suite 2082 pass.

- [x] **Phase 6 — dispatch control crash-safety + dual-PID tracking (2026-05-04, branch phase6-dispatch-control)**: All 6 slices complete. New `audit_dispatch/lock_store.py` (PersistentLockStore + dual-PID payload, atomic writes, fcntl sentinel via audit_governance/file_locks); `locks.py` refactored as façade over the store with full-identity acquire signature; `executor.execute()` accepts `on_spawn(pid, pgid)` callback; `api.py` carries identity through; stale-PID reclaim + lazy first-use sweep; new CLI commands `list-active / unlock / dispatch / watch` on `operations-center-audit`; cross-process concurrency proof test; in-flight run_status watcher (polling, no watchdog dep). Sentinel-glob bug fixed (`_iter_lock_files` filters `.lock.lock` recursive sentinels). Tests: 64 new + all existing passing. Full unit suite 2041 pass.

- [x] **Archon real workflow integration (2026-05-07, on `feat/archon-real-workflow-integration`)**: HttpArchonAdapter now does real workflow dispatch end-to-end per `WorkStation/docs/architecture/adapters/archon-real-workflow-integration.md`. New `backends/archon/http_workflow.py::ArchonHttpWorkflowDispatcher` (health probe → POST conversation → POST workflow run → AsyncHttpRunner kickoff/poll → GET run-detail for events → status map → abandon/cancel). New http_client helpers: `archon_create_conversation`, `archon_get_run_by_worker`, `archon_get_run_detail`, `archon_abandon_run`, `archon_cancel_run`, `archon_list_workflows`. Two AsyncHttpRunner upgrades shipped in ExecutorRuntime to handle Archon's quirks: 200 + non-terminal status falls through to poll (Archon's POST /run returns 200 `{accepted,status:"started"}`, not 202); `http.poll_pending_codes` metadata tolerates 404s during by-worker pre-registration. Plus `ExecutorRuntime.is_registered(kind)` for clean idempotent registration. Factory auto-wires HttpArchonAdapter when `settings.archon.enabled=True`. Probe gained `--list-workflows`. 167 archon-package tests pass; full OC unit suite 2510 pass; ER suite 65 pass.

- [x] **EffectiveRepoGraph + contract impact wired into production (2026-05-08, on `feat/wire-effective-repo-graph`, PR #90)**: `PlatformManifestSettings` block on `Settings` (enabled/project_slug/project_manifest_path/local_manifest_path); `build_effective_repo_graph_from_settings()` resolves project (explicit → `topology/project_manifest.yaml` convention) + local (explicit → WS `discover_local_manifest()`) and degrades to None on any error. Coordinator gains `_log_contract_impact()` hook called once after policy approval, before adapter dispatch — emits `contract change in <X> affects N consumer(s) [public=P private=Q]: ...` at INFO + merges a `contract_impact` dict (target/affected_count/public_affected/private_affected) into observability metadata. Wired into `entrypoints/execute/main.py`. 16 new tests (7 settings→factory, 7 coordinator hook, 2 partition); full unit suite 2518 pass; ruff + ty clean.

- [x] **VideoFoundry project manifest authored (2026-05-08, VF PR #892)**: `topology/project_manifest.yaml` declares VF as private managed-repo with `OperationsCenter dispatches_to VideoFoundry`. `topology/local_manifest.example.yaml` template; live `topology/local_manifest.yaml` gitignored. Validates clean through PM `load_effective_graph` (10 nodes / 13 edges; VF surfaces with source=project, visibility=private, local annotations applied).

- [x] **Warehouse project manifest authored (2026-05-08, Warehouse PR #1)**: Same shape as VF — private managed-repo node + `OperationsCenter dispatches_to Warehouse` edge.

- [ ] **3-layer manifest rollout next steps (no branch yet)**:
  - Author project manifests for the remaining managed/private repos (NetworkRuntime if it becomes part of OC's dispatch path; future project shells)
  - Add a `Settings.platform_manifest.project_slug` default convention or auto-discover from `Settings.self_repo_key` so operators don't have to set it
  - Author per-machine `~/.config/workstation/manifests/<slug>.local.yaml` for the dev box and verify WS discovery picks it up under a real OC run
  - Surface `contract_impact` in the operator-facing CLI output (today it lands in observability metadata only) — pre-dispatch confirmation prompt or post-run summary
  - Wire the impact summary into `tools/audit/report` artifacts so historical runs preserve blast radius

- [ ] **3-layer manifest — cross-repo task chaining (DEFERRED, design-not-yet)**: Use the impact set to auto-queue downstream re-validation tasks when a contract repo (CxRP/RxP/PlatformManifest) is touched. Requires (1) a scheduler/queue that consumes ContractImpactSummary, (2) policy on which kinds of changes auto-trigger downstream tasks vs require operator confirmation, (3) per-consumer task templates. Gated until operator demand is real.

- [ ] Phase 13 or next operator directive TBD

- [x] **ER-000 — Phase 0 Golden Tests (2026-05-06, on `main`)**: 15 tests in `tests/unit/er000_phase0_golden/test_golden.py`. Pinned: one-shot wire imports + Pydantic constructors + backend-unavailable result shape; LaneDecision/ExecutionRequest/ExecutionResult validators reject malformed input; OC's audit_contracts examples still validate; no `videofoundry` imports anywhere in `src/operations_center/`; SwitchBoard package free of orchestration symbols via new `tools/boundary/switchboard_denylist.py` (forward-looking denylist includes `SwarmCoordinator`, `LifecycleRunner`, `RunMemoryIndexWriter`, etc. — passes trivially today, fails closed if those land in SB); `operations-center-audit --help` reaches Typer app via CliRunner.

- [x] **ER-001 — Repo Graph Primitive (2026-05-06, on `main`)**: 21 tests. New `operations_center.repo_graph` package: `models.py` (RepoNode/RepoEdge/RepoGraph + RepoEdgeType enum: `depends_on_contracts_from`, `dispatches_to`, `routes_through`), `loader.py` (YAML loader, fail-fast on duplicate ids/alias collisions/unknown edge types/unknown nodes), `cli.py` (`operations-center-repo-graph list/resolve/upstream/downstream/impact`). Live config at `config/repo_graph.yaml`: 7 repos (OperatorConsole/OperationsCenter/SwitchBoard/WorkStation/CxRP/VideoFoundry/Warehouse) with legacy aliases (ControlPlane→OperationsCenter, FOB→OperatorConsole, ExecutionContractProtocol→CxRP) and 5 edges. Impact query on CxRP returns OperationsCenter+SwitchBoard+OperatorConsole.

- [x] **ER-002 — Run Memory Primitive (2026-05-06, on `main`)**: 23 tests. New `operations_center.run_memory` package: `models.py` (`RunMemoryRecord` w/ frozen-string `contract_kinds`, `SourceType` enum = `execution_result` only, `RunMemoryQuery` w/ AND-combined filters), `index.py` (`deterministic_record_id` = sha256(result_id)[:16] → idempotent rebuilds; `RunMemoryIndexWriter` append-only; `RunMemoryQueryService` with substring-only text search across summary/tags/artifact_paths/repo_id/run_id; `record_execution_result` = single write site for OperationsCenter post-finalize; `rebuild_index_from_artifacts` scans on-disk `execution_result*.json` only — single v1 source). CLI: `operations-center-run-memory query/rebuild`. No vector DB, no embeddings, no scoring.

- [x] **ER-003 — Lifecycle Primitive (2026-05-06, on `main`)**: 13 tests, no live LLM. New `operations_center.lifecycle` package: `models.py` (`TaskLifecycleStage`={plan,execute,verify}, `LifecycleStagePolicy`={stop_on_first_failure,run_all_best_effort} — no `manual_gate_between_stages`; `Check`/`CheckResult`; `PlanOutput` includes `checks: list[Check]` consumed verbatim by verify; `VerifyOutput` with `checks: list[CheckResult]` and derived `failures`; `LifecycleMetadata` + `LifecycleOutcome`), `runner.py` (`LifecycleRunner` driving stages with `StageHandlers` Protocol; missing-from-verify check_ids implicitly fail). Contract additions on `ExecutionRequest.lifecycle: Optional[LifecycleMetadata] = None` and `ExecutionResult.lifecycle_outcome: Optional[LifecycleOutcome] = None` — both optional, one-shot path unchanged.

- [x] **ER-001/002/003 production wiring (2026-05-06, on `main`)**: 10 new wiring tests + 2108 of the wider unit suite green. `ExecutionCoordinator.__init__` now accepts `run_memory_index_dir: Path | None = None` and `repo_graph: RepoGraph | None = None`; both default to None so existing callers are unchanged. After observe(), coordinator calls `record_execution_result` (advisory — failures swallowed) on success, failure, and policy-blocked paths so memory captures everything; tags = `(task_type, lane, backend)`. When `request.lifecycle is not None`, coordinator wraps the dispatch in a default plan/execute/verify cycle (`_attach_lifecycle_outcome`): plan emits a single `execution_succeeded` check, execute mirrors the actual dispatch (no re-dispatch), verify reads `result.success`. Outcome attaches via `result.model_copy(update={"lifecycle_outcome": ...})`. `ExecutionRuntimeContext.lifecycle: LifecycleMetadata | None` added; builder threads it into `ExecutionRequest.lifecycle`. Repo graph: new `load_default_repo_graph()` in `repo_graph/loader.py` provides cached singleton-style access to `config/repo_graph.yaml`; coordinator passes the graph to `LifecycleRunner.run(repo_graph_context=...)` so the plan stage can resolve repo identity.

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
