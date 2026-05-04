# Log

_Chronological continuity log. Decisions, stop points, what changed and why._
_Not a task tracker — that's backlog.md. Keep entries concise and dated._

## Stop Points

- Phase 6 — dispatch control crash-safety + dual-PID tracking (2026-05-04, branch `phase6-dispatch-control`): All 6 slices landed. (A) New `audit_dispatch/lock_store.py` with `PersistentLockStore` + `PersistentLockPayload`; atomic write-tempfile + `os.replace`; `fcntl.flock` sentinel via the existing `audit_governance/file_locks.locked_state_file` helper (lazy-imported to avoid `audit_governance` package-init cycle). Dual-PID payload baked in: `oc_pid` (supervisor) + `audit_pid` (subprocess) + `audit_pgid`. (B) `locks.py` refactored to delegate persistence to the store; `acquire_audit_lock(repo_id, *, run_id, audit_type, oc_pid, command, expected_run_status_path)` carries identity. `executor.execute()` accepts `on_spawn(pid, pgid)` callback wired to `lock.update_audit_pid` so the subprocess PID is patched into the lock immediately after Popen. `api.py` resolves the absolute expected output dir and threads it through. (C) Stale-reclaim policy: a lock is alive iff *any* recorded PID is alive (`os.kill(pid, 0)`). Fresh registry sweeps on first use to recover from OC crash. Corrupt lock files treated as stale (operator can `unlock --force`). (D) New CLI commands on `operations-center-audit`: `list-active` (Rich table or `--json`), `unlock --repo X [--force]`, `dispatch <repo> <type>` positional alias, `watch --repo X` (in-flight `run_status.json` polling). (E) `tests/unit/audit_dispatch/test_lock_store_concurrency.py` spawns two real subprocesses competing for the same repo lock; asserts exactly one acquires. (F) New `watcher.py` exposes `poll_run_status(expected_output_dir, run_id)` iterator yielding `RunStatusSnapshot` on each on-disk content change, terminating on `completed/failed/interrupted`; locates VF buckets by `run_id` substring match per the existing report-naming convention (no `watchdog` dep). Sentinel-glob bug caught in test: `_iter_lock_files` filters out `*.lock.lock`-style sentinels so sweep doesn't recursively wrap. Test counts: 22 lock_store + 18 locks + 18 audit-cli + 2 cross-process + 4 watcher tests; full unit suite 2041 pass (architecture_invariants pre-broken collection error pre-existing on main).

## Recent Decisions

| Decision | Rationale | Date |
|----------|-----------|------|
| Phase 6 dual-PID lock (oc_pid + audit_pid) | Single-PID design left orphaned audit subprocesses invisible to liveness checks (OC dies, audit lives → lock looks reclaimable but artifact writes still happening). Dual-PID treats lock as alive iff any recorded PID is alive — orphaned audit holds the lock until it exits, which is correct. | 2026-05-04 |
| Persistent lock at state/audit_dispatch/locks/{repo_id}.lock | Matches existing OC `state/{subsystem}/...` convention. JSON payload, atomic os.replace. fcntl.flock sentinel via existing audit_governance file_locks helper — no new dep, cross-process exclusion proven by test_lock_store_concurrency.py. | 2026-05-04 |
| Polling over watchdog for in-flight run_status observation | watchdog is not a current OC runtime dep. Polling at 2s default interval is adequate for human-observable lifecycle transitions (running→completed). No new runtime dep. | 2026-05-04 |
| C41 json.dumps ensure_ascii=False | 131 json.dumps calls across 43 files now include ensure_ascii=False; prevents silent Unicode escaping in logs and payloads | 2026-05-03 |
| T4 orphan fixtures deleted | Custodian T4 detector flagged default_proposal(), default_decision() (policy/conftest.py) and index_from_example_failed() (behavior_calibration/conftest.py) as never requested; all removed; 279 tests pass | 2026-05-02 |
| `artifact_manifest_path` is `Optional[str]` in model | VF doesn't write it yet; `is_compliant` enforces it without rejecting legacy files | 2026-04-26 |
| `IN_PROGRESS_LEGACY = "in_progress"` in RunStatus | VF emits `in_progress`; contract canonicalizes to `running`; legacy value accepted but non-compliant | 2026-04-26 |
| Generic enums vs VF profile enums are explicitly separated via GENERIC_ENUMS / VIDEOFOUNDRY_PROFILE_ENUMS tuples | Allows AST-based boundary test and cross-repo reuse without coupling | 2026-04-26 |
| `excluded_paths` separate from `artifacts` | Coverage.ini, .coverage.*, sitecustomize.py are infra noise, not audit artifacts | 2026-04-26 |
| `repo_singleton` location type for architecture_invariants | The file overwrites itself on every run; `valid_for=[latest_snapshot]`, `limitations=[repo_singleton_overwritten]` | 2026-04-26 |
| Phase 2 gate before Phase 5 | Can't implement VF-side writing until contract is locked; 119 tests are the gate | 2026-04-26 |
| OC2/OC5/OC9 removed from detectors.py; OC3+OC8 kept | OC2 → native C1 + exclude_paths.C1; OC5 → native T3 + t3_env_gate_hints (aider, switchboard, OPERATIONS_CENTER_, shutil.which, pkg_path, not present); OC9 → native K2. OC3 (orphaned entrypoints cross-file analysis) and OC8 (K1 + field-def name:Type pattern) are genuinely OC-specific — kept as plugins. | 2026-05-03 |
| OC1/OC4/OC6/OC7 removed from `_custodian/detectors.py` | Superseded by native U1-U3/RUFF/F3; `subprocess` import removed | 2026-05-02 |
| C13 (raw os.environ outside config layer) added to Custodian | Absorbs VF3 custom detector; configured via `audit.c13_allowed_paths` | 2026-05-02 |
| C1 now deferred-aware | Skips lines tagged `[deferred, reviewed]`; absorbs OC2 intent | 2026-05-02 |
| T3 (unconditional pytest.skip) added to Custodian | Absorbs OC5; configurable env-gate hints; default fixture-conditional hints included | 2026-05-02 |
| K1/K2 (doc phantom symbols, doc value drift) added to Custodian | Absorbs OC8/OC9; skips plans/, specs/, changelog dirs by default | 2026-05-02 |
| P1 (hollow return bodies) added to Custodian | Returns only `[]`/`{}`/`None` with no other logic | 2026-05-02 |
| VF3 removed from VF `_custodian/detectors.py` | Superseded by native C13 with `c13_allowed_paths` config | 2026-05-02 |
| N2 fixed 23 invisible test helper functions: renamed with _ prefix | make_insight/artifact, make_snapshot, write_config/insight/decision_inputs, init_git_repo, commit_file, make_decision_artifact, make_input (×4), proposal_decision — all renamed to _name | 2026-05-02 |
| CLAUDE.md: simplify console update instruction | "Before each commit" → "After meaningful progress" — same intent, clearer phrasing | 2026-05-02 |
| audit_architecture.md updated: C1-C8 reference → current detector classes | doc was stale from Phase 0; now references C/D/F/K/S/A/H/T/G/N/U/P classes and correct OC plugin subset (OC2-OC9 active subset, AI3-AI4) | 2026-05-02 |
| K3 fix: explain.py docstring `policy:` → `_policy:` | K3 detector caught genuine param drift — the parameter is named `_policy` in the signature but the docstring said `policy` | 2026-05-02 |
| Custodian 574 tests passing; all repos clean | VF: A1(1 real architectural debt); all others: 0 | 2026-05-02 |
| .console/ migrated to standard naming | active-task/directives/mission-log/objectives → task/guidelines/log/backlog | 2026-05-02 |

## Stop Points

- Phase 2 complete: 119 tests passing, all contract models, examples, schemas, profile, and docs written
- Phase 3-12 complete: 2062 tests passing at Rev 1 lockdown
- Rev 1 gap closure (commit 6000a84): all 11 gaps closed, 2662 tests
- Rev 2 gap closure + Phase 5 verification + full-system integration test (commit 218fb35): 2684 tests passing
- Rev 3 gap closure (schema_version bumped to 1.1, governance_report.schema.json added)
- Rev 4 gap closure (commit aeddb55): governance bypass documented, 49 new CLI tests — 2733 tests passing
- Rev 5 final verification (commit 6f33b47): 0 new gaps; all 21 lifetime gaps closed; all 13 invariants ✅ PASS; system declared locked
- Rev 6 gap closure (commit 18d90c5): attestation docstring + 3 JSON schemas — all 23 lifetime gaps closed
- Rev 7 final verification (commit 5ae2b28): 0 new gaps; 14/14 checks clean; system fully locked
- Rev 8 final verification (commit f84596e): 0 new gaps; 14/14 checks clean; second consecutive clean pass
- Rev 9 final verification (commit c7fd2aa): 0 new gaps; 14/14 checks clean; third consecutive clean pass
- Rev 10 final verification (commit a622f71): 0 new gaps; 14/14 checks clean; fourth consecutive clean pass

## Notes

- Phase 2 test suite: `pytest tests/unit/audit_contracts/ -v` → 119 passed in 0.50s
- Phase 1 test suite: `pytest tests/unit/managed_repos/ -v` → 26 passed
- stack_authoring output_dir is `tools/audit/report/authoring` not `stack_authoring` (Phase 0 quirk, documented)
