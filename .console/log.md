# Log

_Chronological continuity log. Decisions, stop points, what changed and why._
_Not a task tracker — that's backlog.md. Keep entries concise and dated._

## Recent Decisions

| Decision | Rationale | Date |
|----------|-----------|------|
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
