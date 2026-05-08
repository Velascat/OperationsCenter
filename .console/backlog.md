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
- [ ] **File upstream PR for Archon PATCH-001 (no branch — operational gate, 2026-05-08)**: Tracked at `patches/archon/PATCH-001.yaml` (`pushed: false`). Wait until OC has dispatched **≥10 real Archon workflows with override applied** AND captured at least one trace where the override produced a different SDK call than the workflow YAML default (e.g. workflow says `model: sonnet`, override says `model: opus`, claude SDK gets `opus`). Pitch needs to be reframed for upstream Archon users (external orchestrators, multi-tenant deployments, A/B model testing per request) — OC's per-request RuntimeBinding isn't a coleam00/Archon concern on its own. When the gate trips: file against coleam00/Archon, set `patches/archon/PATCH-001.yaml::pushed: true` and `pushed_pr_url`. Until then, gated on `archon.enabled: true` somewhere non-test + a registered codebase + an LLM key in the container.


- [x] **3-layer manifest primitive — operationally complete (2026-05-08, R1–R4 across PM/VF/Warehouse/OC)**: All 14 DoD items met. R1 schema CI + validate CLI, R2 operator runbooks + example.yaml block, R3 path resolution + slug auto-resolve + `effective` CLI, R4 graph-doctor + integration smoke. PM tagged through v0.5.0. Operator now sees blast-radius warnings on every contract-touching dispatch; failures degrade gracefully; pattern is discoverable from main.

- [ ] **R7 — Edge type expansion (next, post-manifest-primitive)**: Audit + design landed (see log entry "R5/R6/R7 audit + design"). Phases:
  - **R7.1**: `RepoGraph.who_dispatches_to(repo_id) -> list[RepoNode]` query. Promotes the existing `dispatches_to` edge from informational to first-class queryable. No schema change. ~half day.
  - **R7.2**: New `RepoEdgeType.BUNDLES_ASSETS_FROM` + `RepoGraph.who_consumes_assets_of(repo_id) -> list[RepoNode]` query. PM minor bump v0.6.0. JSON schema + tests + docs. ~half day.
  - **R7.3**: Author the VF→Warehouse `bundles_assets_from` edge in VF's project manifest (real consumer use case that justified the type). ~half day.
  - **Stop point**: Live with R7.1+R7.2+R7.3 for a few days before R6. Validates that "edge vocabulary determines what the graph can mean" carries through to real queries operators ask.
  - **Deferred edge types**: `monitors_health_of` (wait for WS health graph), `forks_from` (wait for SourceRegistry cross-graph queries), `triggers_revalidation_of` (likely subsumed by R5's propagation infra using `depends_on_contracts_from`).

- [ ] **R6 — Multi-project composition via shell repo (after R7 settles)**: Shell-repo pattern (Shape A) — auditable architecture > runtime trivia. PM `_resolve_includes()` recursive loader with cycle detection + collision rules (duplicate repo_id = hard fail; cycles = hard fail; visibility never widens; cross-suite edges allowed). Phases:
  - **R6.1**: PM `_resolve_includes` recursion + cycle detection + collision rules + 12-15 tests (`composition.py` extension). ~1 day.
  - **R6.2**: PM schema update — optional `includes: list[{name, project_manifest_path}]` on project schema. Bump PM v0.7.0 (after R7.2's v0.6.0). ~half day.
  - **R6.3**: Authoring docs update + reference example. Don't ship a real `VideoFoundrySuite/` repo until VF + Warehouse logically want to be one suite. ~half day.
  - **Future (NOT v1)**: `suite_id` for stable identities independent of repo path. Wait until VF Suite / AI Platform Suite / Customer Suite all want shared identity.

- [ ] **R5 — Cross-repo task chaining (after R6 settles, biggest scope)**: Glue between existing systems — `compute_contract_impact()` + `scheduled_tasks/runner.py` pattern + `PlaneClient.create_issue()` + `cross_repo_impact._check_cross_repo_impact()` (S7-6, already shipped). Default policy: Backlog status, not auto-execute — prevents recursive AI task storms / notification spam / propagation loops / implicit trust escalation. **Mandatory observability**: every automated propagation action emits a structured artifact/report from day one. Phases:
  - **R5.1**: `propagation/` package — `policy.py`, `registry.py`, `dedup.py`, `propagator.py`, `links.py`. Library only, no entrypoint. Tests for each piece. ~2 days.
  - **R5.2**: `operations-center-propagate` entrypoint — manual mode (`--target CxRP --since <commit>`). Integration test with real PlaneClient stub. ~1 day.
  - **R5.3**: `Settings.contract_change_propagation` block + per-repo `propagation_template` overrides + operator docs. ~1 day.
  - **R5.4**: Post-merge hook reference workflow on contract repos (CxRP/RxP/PlatformManifest). Dogfood on one real change. ~1 day.
  - **R5.5**: `propagation_links` CLI to inspect parent-child chains. ~half day.
  - **Trust boundary**: every consumer task lands in Backlog with `revalidation: pending-review` label. Operator promotes after triage. Per-pair opt-in `auto_promote_to_ready: true` once trust accrues.
  - **Idempotency**: dedup key = `(target_repo_id, consumer_repo_id, target_version_or_sha)`. Default 24h window.
  - **Parent-child links**: structured `<!-- propagation:source -->` block in every Plane task body for traceability + dedup anchors + graph lineage + operator context. No DB needed.

- [ ] Phase 13 or next operator directive TBD (after R5/R6/R7 land)

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
