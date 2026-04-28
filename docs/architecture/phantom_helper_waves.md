# Phantom-Helper Implementation Waves

Catalog of the genuine phantom helpers cited in
`docs/design/autonomy_gaps.md` (and surfaced by C8 in
`code_health_audit.md`). Each is a small Python helper that the design
doc references in an implementation context but no `def`/`class` exists
in `src/`.

We're working through these in **waves** across multiple sessions. Each
wave bundles 5–10 related helpers, gets implemented + tested + committed,
and the catalog is updated. Order is by *cost-to-implement × value*, with
small high-value first.

**Invariants every helper must respect** (see `docs/architecture/anti_collapse_invariant.md`):
- No new imports of `behavior_calibration` from runtime packages
- No mutation of frozen Pydantic contracts (`ExecutionRequest`, `ExecutionResult`, `TaskProposal`, `LaneDecision`)
- No new code making routing decisions outside SwitchBoard
- No blurring of the planning ↔ execution boundary

---

## Status

| Wave | State | Helpers | Tests | Audit delta |
|------|-------|---------|-------|-------------|
| 1 — small high-value helpers          | ✅ shipped | 5 | 15 | C8 57→53 |
| 2 — pre-execution validation          | ✅ shipped | 4 | 13 | C8 53→50 |
| 3 — post-merge regression detection   | ✅ shipped | 3 | 9  | C8 50→? |
| 4 — multi-step task planning          | ✅ shipped | 4 | 8  | — |
| 5 — kodo run quality + escalation     | ✅ shipped | 5 | 9  | — |
| 6 — priority + scheduling scans       | ✅ shipped | 4 | 8  | C8 →43 |

Total after all waves: **C8 57 → 43**, **62 new tests**, all 2943 pass, ruff clean.

---

## Wave 1 — small high-value helpers ✅ shipped

| Helper | Location | Purpose |
|--------|----------|---------|
| `_get_kodo_version` | `adapters/kodo/adapter.py` | Module-level shim around `KodoAdapter.get_version` for capture writers / observability |
| `_is_quota_exhausted_result` | `adapters/kodo/adapter.py` | Module-level shim around `is_quota_exhausted` for non-adapter callers |
| `_count_quality_suppressions` | `observer/collectors/quality_suppressions.py` | Counts `# noqa` / `# type: ignore` / `pytest.mark.skip` etc. added in a diff |
| `_check_pr_description_quality` | `adapters/pr_quality.py` | Heuristic score for PR body quality (length, sections, prose vs diff-only) |
| `_in_maintenance_window` | `maintenance_windows.py` | Extracted from `autonomy_cycle/main.py` so escalation / status pane can reuse it |

**Tests**: 15 new in `tests/test_wave1_helpers.py`. All 2896 pass.
**Audit delta**: C8 dropped 57 → 53 (some helpers resolve multiple citations; the remainder is overlap with same names cited in different sections).

---

## Wave 2 — pre-execution validation infrastructure (needs new feature)

These are all pieces of the same feature: a "pre-flight check" stage that
runs before kodo. Once the feature lands, ~8 phantoms resolve.

| Helper | What it does |
|--------|--------------|
| `validate_task_pre_execution` | (already in stale_handlers — but the underlying *feature* is missing) |
| `_check_execution_environment` | verify clone exists, deps satisfied, etc. before kodo |
| `_collect_open_pr_files` | enumerate files touched by other open PRs, pass to kodo as conflict context |
| `_has_conflict_with_active_task` | three-tier conflict detection (running, in-review, recently-merged) |
| `build_improve_triage_result` | structure improve-mode output for downstream consumers |

Cost: M (≤2h) — needs a new `validation` module and a hook in the coordinator.

---

## Wave 3 — post-merge regression detection (real product feature)

`detect_post_merge_regressions` is cited in 5 sections — the most
common phantom. The feature: after a merge, watch CI on `main`; when it
fails, attribute the regression to the merge commit and create a revert
or fix task.

| Helper | What it does |
|--------|--------------|
| `detect_post_merge_regressions` | scan recent merges + main branch CI, flag regressions |
| `create_revert_branch` | open a revert PR for an attributed regression |
| `_extract_evidence_file_tokens` | derive the suspect commits / files |
| `_check_cross_repo_impact` | when the regression touches `impact_report_paths`, warn neighbour repos |

Cost: L (project-sized) — needs CI history collector + state file for tracked merges.

---

## Wave 4 — multi-step task planning

| Helper | What it does |
|--------|--------------|
| `build_multi_step_plan` | split complex goal into Analyze → Implement → Verify chain |
| `_is_multi_step_task` | classify by title keywords + label |
| `_score_proposal_utility` | rank proposals; needed for picking the next step |
| `_requeue_as_goal` | demote a multi-step task back when partial work fails |

Cost: L — depends on dependency tracking (F5 in flow_audit), so probably bundles with that.

---

## Wave 5 — kodo run quality + escalation

| Helper | What it does |
|--------|--------------|
| `_comment_markdown` | format kodo quality alerts as Plane comments |
| `_extract_rejection_patterns` | mine recent rejections to seed kodo prompts |
| `_load_rejection_patterns_for_proposal` | inject rejection patterns into proposal text |
| `_escalate_to_human` | emit a structured escalation event |
| `_process_self_review` | reviewer-side self-review (Phase 1) helpers |

Cost: M — mostly small functions; needs a rejection-store schema first.

---

## Wave 6 — priority + scheduling scans

| Helper | What it does |
|--------|--------------|
| `handle_priority_rescore_scan` | re-rank Backlog tasks by recency + signal |
| `handle_awaiting_input_scan` | poll `awaiting_input` Plane state for unblocks |
| `signal_stale` | mark observation signals as stale after N days |
| `issue_urgency_score` | compute per-issue urgency for sort key |

Cost: M — adds two new periodic-scan watchers.

---

## How a wave runs

1. Open this doc, pick the next wave
2. Implement each helper in its natural module (per the location column)
3. Add tests under `tests/test_wave<N>_helpers.py`
4. Run `pytest`, `ruff`, the audit trinity
5. Update this doc — move the wave from "next" to "shipped" with a tick
6. Commit with a `feat(waves): wave N — <theme>` message

After Wave 6 (and a couple of follow-ups for the remaining 5–10 long-tail
phantoms), C8 should drop from ~53 to single digits.
