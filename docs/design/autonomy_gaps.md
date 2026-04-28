# Autonomy Hardening — 46 Full-Autonomy Gap Improvements

> **Audit note (2026-04-28):** Headings tagged `*[deferred, reviewed YYYY-MM-DD]*`
> describe features where no cited symbol exists in `src/`. They were
> mechanically tagged based on the C8 phantom-symbol detector
> (`docs/architecture/code_health_audit.md`); the tag suppresses the
> detector and signals this section is design-only, not status-of-shipped.
> Sections without the tag have at least one cited symbol present in `src/`,
> meaning the feature is partially or fully implemented — those need
> individual attention to either reconcile or finish.

## Architecture name map

This doc was written against an older code shape where each watcher role
had a top-level `handle_<role>_task()` function and a single
`run_watch_loop`. The runtime moved to a `board_worker` + `pr_review_watcher`
+ `pipeline_trigger` architecture; many of the function names cited
throughout this doc no longer exist under those names. They're
**architecture drift**, not unimplemented features. The C8 audit
explicitly skips them via its `stale_handlers` set.

| Old name (cited in this doc) | New location |
|------------------------------|--------------|
| `handle_goal_task`, `handle_test_task`, `handle_improve_task` | `entrypoints/board_worker/main.py` `_process_issue` (role-dispatched) |
| `handle_propose_cycle` | `entrypoints/autonomy_cycle/main.py` propose stage + `proposer/candidate_integration.py` |
| `handle_blocked_triage` | `entrypoints/spec_director/phase_orchestrator.py` `_handle_blocked` |
| `run_watch_loop` | bash supervisor (`scripts/operations-center.sh start_watch_role`) + Python watchers |
| `handle_review_revision_scan` | `entrypoints/pr_review_watcher/main.py` Phase 1/2 state machine |
| `classify_execution_result` | `execution/coordinator.py` outcome handling + `contracts/execution.py` `ExecutionResult` |
| `select_watch_candidate` | `entrypoints/board_worker/main.py` `_claim_next` |
| `build_proposal_candidates` | `proposer/candidate_integration.py` `apply_candidates` |
| `validate_task_pre_execution` | not implemented — see C-K4/C-K5 in `audit_triage_plan.md` |
| various `handle_*_scan` periodic scans | maintenance CLIs under `entrypoints/maintenance/` |

When reading sections below, treat phantom function names as describing
**intent**: "the autonomy loop should detect X and create a Y task." The
*location* of that detection is now distributed across the components
above rather than concentrated in a single function.

This document describes the 46 improvements implemented across five sessions to close the
gaps toward fully autonomous operation. They are grouped by session, then by theme.

---

## Session 1 — 8 Proposal and Execution Improvements

### 1. Goal Coherence — `focus_areas` config  *[deferred, reviewed 2026-04-28 — phantom symbols, no implementation in src/]*

**Problem:** The proposer would create tasks about anything it found in the codebase, spreading
kodo's attention across many topics before finishing what mattered.

**Fix:** `focus_areas: [...]` in `config/operations_center.local.yaml` accepts a list of keywords.
Proposals whose title or goal text does not match any keyword are demoted to Backlog with
reduced confidence. Matching proposals are kept at their natural state.

**Config:**
```yaml
focus_areas:
  - test coverage
  - type safety
  - error handling
```

**Files:** `config/settings.py` (`Settings.focus_areas`), `main.py` (`_proposal_matches_focus_areas`, `build_proposal_candidates`)

---

### 2. Dependency Ordering — `depends_on:` parsing  *[deferred, reviewed 2026-04-28 — phantom symbols, no implementation in src/]*

**Problem:** Tasks with explicit dependencies would be picked up before their dependencies
completed, causing wasted executions or conflicts.

**Fix:** A `depends_on: <uuid>` line in a task's description is parsed by
`parse_task_dependencies`. `task_dependencies_met` checks whether each UUID is in Done state.
`select_watch_candidate` skips tasks with unmet dependencies; the next candidate is tried instead.

**Task description syntax:**
```
## Constraints
- depends_on: aabbccdd-1234-5678-0000-000000000001
```

**Files:** `main.py` (`parse_task_dependencies`, `task_dependencies_met`, `select_watch_candidate`)

---

### 3. Task Sizing Gate — split oversized findings  *[deferred, reviewed 2026-04-28 — phantom symbols, no implementation in src/]*

**Problem:** Decompose tasks targeting files with thousands of lines would exhaust kodo's context
window before completing, failing with `context_limit`.

**Fix:** At proposal time, findings matching `"Decompose <file> (<N>L, ...)"` where N > 800 are
split into 2–4 bounded part-tasks: "Decompose file.py — part 1 of 3", etc. Each part targets a
subset of functions. Kodo never receives the full file at once.

**Files:** `main.py` (`_split_oversized_finding`, `MAX_TASK_LINES_FOR_DIRECT_EXECUTION`)

---

### 4 + 6. Post-Merge CI Feedback and Conflict Detection

**Problem:** After a PR was merged, CI failures would go unnoticed. Running tasks might also
conflict with each other by touching the same files.

**Fix (post-merge regression):** Every 10 improve cycles, `detect_post_merge_regressions` scans
Done tasks with a recorded PR URL, fetches CI check-run status from GitHub, and creates
high-priority `[Improve] Post-merge regression detected` tasks for any that failed after merge.

**Fix (conflict detection):** Three-tier check in `_has_conflict_with_active_task`:
1. Artifact `changed_files` for Review/retry-Running tasks (authoritative)
2. Open PR file list via `GET /repos/{owner}/{repo}/pulls/{n}/files` for first-run Running tasks
3. Title token basename matching as a lightweight fallback

**Files:** `main.py` (`detect_post_merge_regressions`, `_has_conflict_with_active_task`, `_collect_open_pr_files`), `github_pr.py` (`list_pr_files`)

---

### 5. Self-Modification Controls  *[deferred, reviewed 2026-04-28 — phantom symbols, no implementation in src/]*

**Problem:** The system could propose and automatically execute changes to its own codebase
(OperationsCenter itself), bypassing the human review that self-modification requires.

**Fix:** `self_repo_key: OperationsCenter` in config identifies the installation's own repo.
- Proposals for self-repo tasks are always capped at Backlog.
- `select_watch_candidate` skips self-repo tasks unless they carry a `self-modify: approved` label.

**Config:**
```yaml
self_repo_key: OperationsCenter
```

**Files:** `settings.py` (`Settings.self_repo_key`), `main.py` (`_is_self_repo`, `_self_modify_approved`, `select_watch_candidate`, `build_proposal_candidates`)

---

### 7. Better Failure Attribution  *[deferred, reviewed 2026-04-28 — phantom symbols, no implementation in src/]*

**Problem:** Tasks failing due to context window exhaustion or missing dependencies were
classified as `validation_failure`, producing follow-up tasks with the wrong remediation.

**Fix:** `classify_execution_result` now checks for `context_limit` and `dependency_missing`
patterns in stderr **before** falling back to `validation_failure`. Each gets a tailored
follow-up goal in `build_improve_triage_result`.

| Classification | Trigger pattern | Follow-up goal |
|---------------|----------------|----------------|
| `context_limit` | "context window", "token limit", etc. | Break into smaller bounded scope |
| `dependency_missing` | `ModuleNotFoundError`, `command not found`, etc. | Install the dependency in bootstrap |
| `flaky_test` | Known-flaky command (see Session 2, Item 5) | Stabilize the test |

**Files:** `main.py` (`classify_execution_result`, `blocked_classification_token`, `classify_blocked_issue`, `build_improve_triage_result`)

---

### 8. Satiation Signal — proposal cycle tracking

**Problem:** The proposer would keep proposing even when the board was stable and there was
nothing new to find, creating dedup noise and board clutter.

**Fix:** `record_proposal_cycle(created, deduped, skipped, now)` records each cycle's outcome.
`is_proposal_satiated` returns True when the last 5 cycles produced zero new tasks and the
dedup+skipped fraction exceeded 90%. The proposer short-circuits with `decision: satiated` until
external state changes.

**Files:** `usage_store.py` (`record_proposal_cycle`, `is_proposal_satiated`), `main.py` (`handle_propose_cycle`)

---

## Session 2 — 10 Full-Autonomy Gap Improvements

### 1. Human Escalation Channel

**Problem:** When the system was stuck (same failure repeating), it would silently stay blocked
on the board. A human had to check manually.

**Fix:** After `handle_blocked_triage`, `record_blocked_triage` writes an event. `should_escalate`
checks whether the same classification has appeared ≥ `block_threshold` times in 24 hours and
no escalation has been sent within `cooldown_seconds`. If both conditions are met,
`post_escalation` sends an HTTP POST to `escalation.webhook_url`.

**Config:**
```yaml
escalation:
  webhook_url: https://hooks.slack.com/...
  block_threshold: 5
  cooldown_seconds: 3600
```

**Files:** `adapters/escalation.py` (new), `settings.py` (`EscalationSettings`), `usage_store.py` (`record_blocked_triage`, `should_escalate`, `record_escalation`), `main.py` (`handle_blocked_triage`)

---

### 2. Merge Conflict Detection and Rebase

**Problem:** Open PRs with merge conflicts would silently become unmergeable. Nobody would notice
until a human checked GitHub directly.

**Fix:** `handle_merge_conflict_scan` runs every 5 improve cycles. For each open PR with
`mergeable == false`, it attempts `git rebase origin/<base>` in the local checkout. On success,
the rebased branch is force-pushed. On failure, a `[Rebase] <PR title>` task is created.

**Files:** `github_pr.py` (`get_mergeable`), `git/client.py` (existing `rebase_onto_origin`, `push_branch_force`), `main.py` (`handle_merge_conflict_scan`)

---

### 3. Watcher Heartbeat Monitoring  *[deferred, reviewed 2026-04-28 — phantom symbols, no implementation in src/]*

**Problem:** If a watcher process died silently, the autonomous loop stopped with no alert.

**Fix:** `write_heartbeat(status_dir, role, now)` writes `heartbeat_<role>.json` at the start of
every cycle. The `heartbeat-check` CLI subcommand reads all heartbeat files and exits non-zero
if any is older than 5 minutes.

```bash
python -m operations_center.entrypoints.worker.main heartbeat-check --log-dir logs/local/watch-all
```

Wire this into cron or a process monitor for unattended operation.

**Files:** `main.py` (`write_heartbeat`, `check_heartbeats`, `_HEARTBEAT_MAX_AGE_SECONDS`, `main()` subcommand)

---

### 4. Context Handoff for `context_limit` Tasks  *[deferred, reviewed 2026-04-28 — phantom symbols, no implementation in src/]*

**Problem:** When kodo exhausted its context window on a large task, the follow-up task started
from scratch, hitting the same limit on the same first half of the work.

**Fix:** `_record_execution_artifact` now saves `summary` in the artifact. In
`build_improve_triage_result`, when classification is `context_limit`, the prior artifact's
`summary` is read and appended to the follow-up `goal_text` as a `prior_progress:` block
(truncated to 800 chars).

**Files:** `main.py` (`_record_execution_artifact`, `build_improve_triage_result`)

---

### 5. Flaky Test Detection

**Problem:** Intermittently-failing tests would exhaust retry caps and produce `validation_failure`
blocked tasks. The real fix (stabilise the test) was never proposed.

**Fix:** `record_validation_outcome(command, passed, now)` is called after every goal/test task
execution. `is_command_flaky(command)` returns True when ≥30% of the last 10 runs failed.
`classify_execution_result` returns `flaky_test` when the failing command is known-flaky.
`build_improve_triage_result` produces a stabilisation goal for `flaky_test`.

**Files:** `usage_store.py` (`record_validation_outcome`, `is_command_flaky`), `main.py` (`classify_execution_result`, `handle_goal_task`, `handle_test_task`, `build_improve_triage_result`)

---

### 6. PR Review Revision Cycle

**Problem:** When a human reviewer left `CHANGES_REQUESTED` on a PR, the system had no way
to notice and act on it. PRs stalled in "In Review" indefinitely.

**Fix:** `handle_review_revision_scan` runs every 3 improve cycles. For tasks in an "In Review"
state with a recorded PR URL, `list_pr_reviews` checks for `CHANGES_REQUESTED`. If found, a
`[Revise] <title> — address review feedback` task is created with the review comment text
embedded as context.

Bot logins (`reviewer.bot_logins`) and allowed-reviewer filters (`reviewer.allowed_reviewer_logins`) are applied before acting.

**Files:** `github_pr.py` (`list_pr_reviews`, `pr_has_changes_requested`), `main.py` (`handle_review_revision_scan`)

---

### 7. Token/Credential Expiry Detection  *[deferred, reviewed 2026-04-28 — phantom symbols, no implementation in src/]*

**Problem:** Expired API tokens caused every execution to fail with auth errors. The system would
run for hours burning retries before a human noticed.

**Fix:** `validate_credentials` is called on the first watcher cycle (`cycle == 1`). It calls
`GET /user` on GitHub and `GET /api/v1/workspaces/<slug>/` on Plane. A 401/403 logs a clear
error, writes an escalation event to the usage store, and returns False — causing the watcher to
abort immediately rather than running with broken credentials.

Network timeouts are logged as warnings but do not abort (connectivity issue ≠ invalid token).

**Files:** `main.py` (`validate_credentials`, `run_watch_loop`)

---

### 8. Success/Failure Learning per Proposal Category

**Problem:** The proposer created tasks without regard to whether that category of task had been
succeeding or failing historically.

**Fix:** `record_proposal_outcome(category, succeeded, now)` is called after each goal task
completes. `proposal_success_rate(category)` returns the success rate over the last 20 outcomes
(neutral 0.5 when fewer than 3 samples exist). In `build_proposal_candidates`:
- Rate > 70% → boost to Ready for AI
- Rate < 30% → demote to Backlog

**Files:** `usage_store.py` (`record_proposal_outcome`, `proposal_success_rate`), `main.py` (`handle_goal_task`, `build_proposal_candidates`)

---

### 9. Scheduled Tasks via Cron Expressions  *[deferred, reviewed 2026-04-28 — phantom symbols, no implementation in src/]*

**Problem:** Maintenance work (dependency audits, weekly checks) had to be created manually. There
was no time-triggered task creation.

**Fix:** `scheduled_tasks:` in config accepts a list of `{cron, title, goal, repo_key, kind}`
entries. At the start of each propose cycle, `_scheduled_tasks_due` checks which entries have
fired within the last 120 seconds and are not already on the board. Due tasks are created
immediately as Ready for AI.

Requires the optional `croniter` Python package.

**Config:**
```yaml
scheduled_tasks:
  - cron: "0 9 * * 1"   # Monday 09:00 UTC
    title: Weekly dependency audit
    goal: Check for outdated dependencies and create upgrade tasks.
    repo_key: OperationsCenter
    kind: goal
```

**Files:** `settings.py` (`ScheduledTask`), `main.py` (`_scheduled_tasks_due`, `handle_propose_cycle`)

---

### 10. Stale PR TTL

**Problem:** PRs that sat open for days without activity accumulated merge conflicts and became
unmergeable silently. Humans had to close them manually.

**Fix:** `handle_stale_pr_scan` runs every 20 improve cycles. PRs older than `stale_pr_days`
(default 7) are processed:
1. A rebase is attempted. If it succeeds, the PR is left open.
2. If the rebase fails (or no local path exists), a `_STALE_PR_COMMENT_MARKER` comment is posted,
   the PR is closed, and the originating Plane task is transitioned to Backlog.

Comment idempotence: before closing, the scan checks for an existing stale-PR comment to avoid
double-acting.

**Config:**
```yaml
stale_pr_days: 7   # default
```

**Files:** `github_pr.py` (`close_pr`), `settings.py` (`Settings.stale_pr_days`), `main.py` (`handle_stale_pr_scan`)

---

---

## Session 3 — 8 Reliability and Throughput Improvements

### 1. GitHub API Rate-Limit Handling

**Problem:** The GitHub adapter made bare `httpx.get/post/...` calls with no rate-limit
awareness. Under load with multiple repos, 429 responses silently dropped entire scan
cycles — the improve watcher looked alive while blind to CI failures, merge conflicts,
and review feedback.

**Fix:** All GitHub API calls now go through `GitHubPRClient._request()`.  On 429, it
reads the `Retry-After` header (defaulting to 60 s) and retries up to 3 times before
propagating the error. When `X-RateLimit-Remaining` drops below 10, a `github_rate_limit_low`
warning is logged so operators have advance notice before hard throttling.

**Files:** `github_pr.py` (`_request`, replaces all direct `httpx.*` call sites)

---

### 2. Pre-Execution Task Validation  *[deferred, reviewed 2026-04-28 — phantom symbols, no implementation in src/]*

**Problem:** Vague or un-actionable tasks were sent to Kodo, failed, became blocked, and
triggered an improve triage cycle — costing 2–3 full execution passes to discover the
original task was malformed. The improve worker's triage vocabulary named the failure
modes (`scope_policy`, `parse_config`) but only saw them after the fact.

**Fix:** `validate_task_pre_execution()` runs on the goal watcher immediately before a
task is claimed. It checks:
1. Goal text is non-empty and within a useful length range (30–8000 chars).
2. Goal text does not contain a vague catch-all phrase (`fix everything`, etc.).
3. If the goal mentions source files by extension, at least one must exist in a configured `local_path`.

On failure, the task is moved to Backlog with a comment explaining which check failed.
Empty-goal tasks pass through (conservative — we cannot positively determine they're bad).

**Files:** `main.py` (`validate_task_pre_execution`, called in `run_watch_loop` before claim)

---

### 3. Feedback Loop Automation

**Problem:** The `feedback` CLI entrypoint existed but was manual. When a human closed a
PR without merging, or a task moved to Done outside the watcher loop (manual merge,
operator action), `proposal_success_rate` didn't update. The per-category learning signal
drifted away from reality.

**Fix:** `handle_feedback_loop_scan()` runs every 15 improve cycles. For each Done issue
with a recorded `pull_request_url` artifact that has no `state/proposal_feedback/<id>.json`
file, it fetches the PR state from GitHub and writes the feedback record automatically.
It also updates `proposal_success_rate` via the usage store so the proposer learns from
these outcomes immediately.

**Files:** `main.py` (`handle_feedback_loop_scan`, `_FEEDBACK_DIR`, `_FEEDBACK_LOOP_CYCLE_INTERVAL`)

---

### 4. Workspace Health Monitoring  *[deferred, reviewed 2026-04-28 — phantom symbols, no implementation in src/]*

**Problem:** Bootstrap runs once at task start. If a package was yanked, a tool version
changed, or the venv was corrupted between task runs, every subsequent task failed with
`dependency_missing` or `infra_tooling`. The system created follow-up tasks asking a
human to "install the dependency in bootstrap," but it never tried to repair the environment
itself.

**Fix:** `handle_workspace_health_check()` runs every 25 improve cycles. For each repo
with a configured `local_path`, it runs `python -c "import sys; sys.exit(0)"` inside the
repo's venv. On failure it calls `RepoEnvironmentBootstrapper.prepare()` to attempt a
repair. If bootstrap also fails, a high-priority `[Workspace] Repair environment` goal
task is created so a human is alerted immediately.

**Files:** `main.py` (`handle_workspace_health_check`, `_WORKSPACE_HEALTH_CYCLE_INTERVAL`)

---

### 5. Config Schema Drift Detection  *[deferred, reviewed 2026-04-28 — phantom symbols, no implementation in src/]*

**Problem:** No mechanism existed to detect when a deployed `config/operations_center.local.yaml`
was missing keys added in newer versions. Features like `escalation`, `stale_pr_days`,
`scheduled_tasks`, and `self_repo_key` silently defaulted to off if the operator's config
predated them. Operators could run the system for weeks unaware that a feature was disabled.

**Fix:** New module `config/drift.py` provides `detect_config_drift(config_path, example_path)`
which compares top-level and one level of nested keys between the deployed config and the
bundled example. At watcher startup (cycle 1, primary slot only), missing keys are logged
as `config_drift_detected` warnings — one per missing key plus a summary. Dynamic sections
(`repos`, `scheduled_tasks`) are intentionally excluded from the nested check to avoid
false positives.

**Files:** `config/drift.py` (new), `main.py` (called at cycle 1 in `run_watch_loop`)

---

### 6. Cost/Spend Telemetry

**Problem:** The execution budget (hourly/daily task caps) prevented runaway task creation
but did not track monetary cost. Operators had no way to answer "how much did autonomous
operation cost this week?" or set a hard spend limit per repo. A runaway loop could burn
LLM credits with no alarm.

**Fix:** `UsageStore.record_execution_cost()` appends an `execution_cost` event after each
goal or test task. `get_spend_report(window_days=N)` aggregates those events into a
`{total_executions, total_estimated_usd, per_repo: {...}}` dict. The per-execution cost is
operator-supplied via `cost_per_execution_usd: 0.0` in config (zero by default = tracking
disabled). A `spend-report` CLI subcommand prints the report as JSON.

```bash
python -m operations_center.entrypoints.worker.main spend-report --window-days 7
```

**Config:**
```yaml
cost_per_execution_usd: 0.15   # operator estimate; 0.0 disables recording
```

**Files:** `settings.py` (`Settings.cost_per_execution_usd`), `usage_store.py` (`record_execution_cost`, `get_spend_report`), `main.py` (`spend-report` subcommand, cost recording in `handle_goal_task` and `handle_test_task`)

---

### 7. Parallel Execution Within Lanes  *[deferred, reviewed 2026-04-28 — phantom symbols, no implementation in src/]*

**Problem:** Each watcher lane handled one task at a time. With 10 lint_fix tasks ready,
they executed one per poll cycle with sleeping intervals between them. This was the primary
throughput bottleneck when heading toward higher-autonomy operation.

**Fix:** `run_parallel_watch_loop()` launches N threads, each running an independent
`run_watch_loop`. Slot 0 is the primary slot and owns all periodic scans (heartbeat,
improve sub-scans, config drift check, credential validation). Non-zero slots only execute
tasks. The Plane API's state machine (task transitions to Running) acts as the distributed
lock — two slots cannot claim the same task. Configurable via `--parallel-slots N` CLI
flag or `parallel_slots: N` in config (default 1).

**Config:**
```yaml
parallel_slots: 2   # default 1 (serial)
```

**CLI:**
```bash
./scripts/operations-center.sh watch --role goal --parallel-slots 3
```

**Files:** `settings.py` (`Settings.parallel_slots`), `main.py` (`run_parallel_watch_loop`, `_slot_id` param on `run_watch_loop`, `--parallel-slots` arg in `main()`)

---

### 8. Multi-Step Dependency Planning

**Problem:** Complex tasks (refactors, migrations, redesigns) were sent to Kodo in full.
They hit `context_limit`, triggered an improve cycle, produced a `prior_progress:` handoff
follow-up, and repeated — discovering the multi-step structure one failure at a time. The
cost was 3–5 execution passes for work that could have been scoped upfront.

**Fix:** `build_multi_step_plan()` is called at the top of `handle_goal_task` for tasks
identified as complex (title contains `refactor`, `migrate`, `redesign`, `modernize`,
`audit`, `overhaul`, `restructure`, `rewrite`, or the task carries a `plan: multi-step`
label). It creates three dependent tasks before any execution:

1. `[Step 1/3: Analyze] <title>` — scope investigation, no code changes, starts Ready for AI
2. `[Step 2/3: Implement] <title>` — depends_on step 1, starts Backlog
3. `[Step 3/3: Verify] <title>` — depends_on step 2, starts Backlog

The original task is moved to Backlog. Kodo never receives a scope it cannot complete in
one context window. If the plan has already been created (step titles already on the board),
the function is a no-op.

**Files:** `main.py` (`build_multi_step_plan`, `_is_multi_step_task`, `_MULTI_STEP_TITLE_KEYWORDS`, `_MULTI_STEP_LABEL`, called from `handle_goal_task`)

---

## Summary Table

| # | Name | Trigger | Where |
|---|------|---------|-------|
| S1-1 | Goal coherence | proposal time | `build_proposal_candidates` |
| S1-2 | Dependency ordering | watcher candidate selection | `select_watch_candidate` |
| S1-3 | Task sizing gate | proposal time | `build_proposal_candidates` |
| S1-4 | Post-merge CI feedback | every 10 improve cycles | `detect_post_merge_regressions` |
| S1-5 | Self-modification controls | watcher + proposal time | `select_watch_candidate`, `build_proposal_candidates` |
| S1-6 | Three-tier conflict detection | proposal time | `_has_conflict_with_active_task` |
| S1-7 | Better failure attribution | classify after execution | `classify_execution_result` |
| S1-8 | Satiation signal | propose cycle | `handle_propose_cycle` |
| S2-1 | Human escalation | after blocked triage | `handle_blocked_triage` |
| S2-2 | Merge conflict rebase | every 5 improve cycles | `handle_merge_conflict_scan` |
| S2-3 | Watcher heartbeat | every cycle | `run_watch_loop` |
| S2-4 | Context handoff | after execution | `build_improve_triage_result` |
| S2-5 | Flaky test detection | classify after execution | `classify_execution_result` |
| S2-6 | PR review revision | every 3 improve cycles | `handle_review_revision_scan` |
| S2-7 | Credential validation | cycle 1 startup | `run_watch_loop` |
| S2-8 | Success/failure learning | after execution + proposal | `handle_goal_task`, `build_proposal_candidates` |
| S2-9 | Scheduled tasks | every propose cycle | `handle_propose_cycle` |
| S2-10 | Stale PR TTL | every 20 improve cycles | `handle_stale_pr_scan` |
| S3-1 | GitHub rate-limit handling | every GitHub API call | `GitHubPRClient._request` |
| S3-2 | Pre-execution task validation | before task claim (goal lane) | `validate_task_pre_execution` |
| S3-3 | Feedback loop automation | every 15 improve cycles | `handle_feedback_loop_scan` |
| S3-4 | Workspace health monitoring | every 25 improve cycles | `handle_workspace_health_check` |
| S3-5 | Config schema drift detection | cycle 1 startup | `detect_config_drift`, `run_watch_loop` |
| S3-6 | Cost/spend telemetry | after each goal/test execution | `record_execution_cost`, `get_spend_report` |
| S3-7 | Parallel execution | watcher startup | `run_parallel_watch_loop` |
| S3-8 | Multi-step dependency planning | start of goal task execution | `build_multi_step_plan` |
| S4-1 | Watcher auto-restart | watcher process exit | `start_watch_role` (shell) |
| S4-2 | Task staleness invalidation | every 30 improve cycles | `handle_stale_autonomy_task_scan` |
| S4-3 | Success-rate circuit breaker | before each execution | `budget_decision`, `record_execution_outcome` |
| S4-4 | Parallel slot write safety | every usage store write | `UsageStore._exclusive`, atomic `save()` |
| S4-5 | Observer snapshot staleness | before generate-insights | `SnapshotLoader.latest_snapshot_age_hours` |
| S4-6 | Connection error backoff | transient API failures | `run_watch_loop` `_consecutive_errors` |
| S4-7 | Human rejection capture | feedback loop scan | `handle_feedback_loop_scan` (Part B) |
| S4-8 | Execution profiles per kind | before each kodo run | `Settings.kodo_profiles`, `KodoAdapter.build_command` |
| S4-9 | Dry-run quiet diagnosis | after autonomy-cycle report | `_write_quiet_diagnosis` |
| S4-10 | Long-lived deduplication | before each proposal | `ProposalRejectionStore`, `ProposerGuardrailAdapter` |
| S5-1 | Plane write retry | every Plane transition/comment/create call | `PlaneClient._request` |
| S5-2 | Kodo process tree cleanup | kodo timeout | `KodoAdapter._run_subprocess`, `os.killpg` |
| S5-3 | Per-task-kind running TTL | reconcile at watcher startup | `reconcile_stale_running_issues`, `_RUNNING_TTL_MINUTES` |
| S5-4 | Disk space guardrail | before usage store and cycle report writes | `_check_disk_space`, `UsageStore.save()` |
| S5-5 | Quota exhaustion detection | after each kodo execution | `KodoAdapter.is_quota_exhausted`, `record_kodo_quota_event` |
| S5-6 | Task urgency scoring | watcher candidate selection | `issue_urgency_score`, `select_watch_candidate` |
| S5-7 | Board saturation backpressure | before proposing (watcher + autonomy-cycle) | `handle_propose_cycle`, `autonomy_cycle/main.py` |
| S5-8 | Scope violation recording | after policy-retry violations | `record_scope_violation`, `service.py` |
| S5-9 | Improve → propose feedback | when escalation threshold fires | `handle_blocked_triage` |
| S5-10 | Kodo quality erosion detection | after each kodo execution with diff | `_count_quality_suppressions`, `record_quality_warning` |

---

## Session 5 — 10 Reliability and Observability Improvements

### S5-1. Plane Write Retry

**Problem:** `transition_issue` and `comment_issue` were fire-and-forget — a single 5xx or connection error left the task in the wrong state (e.g. still `Running` after kodo finished) with no recovery path. Over time this caused board state drift.

**Fix:** `PlaneClient._request` now retries up to 3 additional times (4 total) on:
- `httpx.ConnectError`, `httpx.TimeoutException`, `httpx.RemoteProtocolError` — connection-level failures (linear backoff)
- HTTP 502, 503, 504 — transient gateway/server errors (linear backoff)
- HTTP 429 already had retry logic; that is unchanged

Duplicate comment side-effects are acceptable — a missed transition is far more damaging.

**Files:** `adapters/plane/client.py` (`_request`)

---

### S5-2. Kodo Process Tree Cleanup on Timeout

**Problem:** `subprocess.run()` with `timeout` called `process.kill()` on the kodo wrapper process, but kodo may have spawned Claude sub-processes. Those orphaned processes continued consuming CPU and API quota indefinitely. Over days of operation they accumulated until they exhausted the process table or system memory.

**Fix:** `KodoAdapter._run_subprocess()` replaces the `subprocess.run()` call. It uses `subprocess.Popen(start_new_session=True)` to place kodo in its own process group. On `TimeoutExpired`, `os.killpg(os.getpgid(proc.pid), signal.SIGKILL)` kills the entire group — the wrapper and all children — before returning. Both `run()` and `_run_with_claude_fallback()` now delegate to `_run_subprocess`.

**Files:** `adapters/kodo/adapter.py` (`_run_subprocess`, `run`, `_run_with_claude_fallback`)

---

### S5-3. Per-Task-Kind Running TTL in Reconcile  *[deferred, reviewed 2026-04-28 — phantom symbols, no implementation in src/]*

**Problem:** `reconcile_stale_running_issues` used a single generic TTL for all task kinds. A goal task legitimately running a complex refactor (2+ hours) would be killed early; a test task stuck for 4 hours would wait too long before reclamation.

**Fix:** `_RUNNING_TTL_MINUTES` maps task kind to its expected maximum runtime:

| Kind | TTL |
|------|-----|
| `goal` | 120 min |
| `test` | 45 min |
| `improve` | 30 min |
| `fix_pr` | 45 min |
| (other) | 90 min |

Before acting on a Running task, `reconcile_stale_running_issues` checks `issue.updated_at`. If the task was updated within its TTL, it is skipped — it may still be legitimately running. Tasks whose `updated_at` is older than the TTL fall through to the existing re-queue/block logic.

**Files:** `entrypoints/worker/main.py` (`reconcile_stale_running_issues`, `_RUNNING_TTL_MINUTES`, `_RUNNING_TTL_DEFAULT_MINUTES`)

---

### S5-4. Disk Space Guardrail

**Problem:** The janitor cleans old artifacts, but runs as a pre-command wrapper rather than before each write. If disk filled between janitor runs (large snapshot, parallel slots writing simultaneously), `write_text` would raise `OSError` and crash the watcher. The crash triggered the S4-1 restart loop — which would immediately crash again on the next write attempt.

**Fix:** `_check_disk_space(path)` in `usage_store.py` checks `shutil.disk_usage()` before writing. Below 50 MB free it raises `OSError` with a descriptive message including a remediation hint. Below 200 MB it logs a `disk_space_low` structured warning (non-fatal). Called in `UsageStore.save()` and in `_write_cycle_report` in `autonomy_cycle/main.py`.

Both thresholds are constants (`_DISK_MIN_MB = 50`, `_DISK_WARN_MB = 200`).

**Files:** `execution/usage_store.py` (`_check_disk_space`, `_DISK_MIN_MB`, `_DISK_WARN_MB`, `save()`), `entrypoints/autonomy_cycle/main.py` (`_write_cycle_report`)

---

### S5-5. Kodo API Quota Exhaustion Detection

**Problem:** When kodo's upstream API hit a hard quota limit (billing exhaustion, not a transient rate limit), the system treated it as a generic task failure. The circuit breaker (S4-3) would open after 5 such failures, blocking all further tasks — the right symptom but with `reason="circuit_breaker_open"` rather than something diagnostic. The operator had to manually inspect kodo stderr to understand why.

**Fix:**
- `KodoAdapter._HARD_QUOTA_EXHAUSTED_SIGNALS` — phrases that indicate a billing-level limit (`insufficient_quota`, `you've exceeded your usage limit`, `upgrade your plan`, etc.)
- `KodoAdapter.is_quota_exhausted(result)` — returns True when the result contains these signals
- `UsageStore.record_kodo_quota_event(task_id, role, now)` — records a `kodo_quota_event` that does **not** feed the circuit breaker
- `_is_quota_exhausted_result(result)` in `main.py` — checks `execution_stderr_excerpt` for the same patterns
- In `handle_goal_task` and `handle_test_task`: when quota is exhausted, calls `record_kodo_quota_event` instead of `record_execution_outcome`. This prevents the circuit breaker from opening on an infrastructure failure rather than a task-quality failure.

**Files:** `adapters/kodo/adapter.py` (`_HARD_QUOTA_EXHAUSTED_SIGNALS`, `is_quota_exhausted`), `execution/usage_store.py` (`record_kodo_quota_event`), `entrypoints/worker/main.py` (`_QUOTA_EXHAUSTED_EXCERPT_SIGNALS`, `_is_quota_exhausted_result`, `handle_goal_task`, `handle_test_task`)

---

### S5-6. Task Urgency Scoring  *[deferred, reviewed 2026-04-28 — phantom symbols, no implementation in src/]*

**Problem:** `select_watch_candidate` sorted Ready-for-AI tasks only by priority label (high/medium/low/unset). A post-merge regression fix and a background lint cleanup in the same priority tier were picked in arbitrary board order. No mechanism existed to prefer time-sensitive work over maintenance work.

**Fix:** `issue_urgency_score(issue)` computes a composite integer score combining:
- **Priority label weight** — high=30, medium=20, low=10, unset=15
- **Title prefix boost** — `[Regression]`/`post-merge regression`=+25, `[Fix]`/`[Rebase]`=+15, `[Revise]`/`[Verify]`=+8, `[Workspace]`/`[Step 1`=+5
- **Task age** — capped at +3 (one point per day in Ready state, max 3 days)

`select_watch_candidate` now sorts by `issue_urgency_score(issue)` descending instead of `issue_priority` ascending.

**Files:** `entrypoints/worker/main.py` (`issue_urgency_score`, `select_watch_candidate`)

---

### S5-7. Board Saturation Backpressure  *[deferred, reviewed 2026-04-28 — phantom symbols, no implementation in src/]*

**Problem:** The satiation signal (S1-8) detected when proposals weren't being consumed over multiple cycles, but couldn't directly observe the current board queue depth. A burst `autonomy-cycle --execute` run could flood the board with 30+ tasks when the watcher queue was already backed up, far exceeding what the goal/test lanes could drain in a reasonable time.

**Fix:**
- `MAX_QUEUED_AUTONOMY_TASKS = 15` constant in `main.py` (configurable via `OPERATIONS_CENTER_MAX_QUEUED_AUTONOMY_TASKS` env var)
- In `handle_propose_cycle`: after the `board_congested` check, counts `source: autonomy` tasks in `Ready for AI` or `Backlog`. If ≥ threshold, returns `decision="board_saturated"` immediately
- In `autonomy_cycle/main.py`: performs the same count via `client.list_issues()` before calling `proposer_svc.run()`. Skips the propose stage and writes the cycle report with 0 created tasks

**Files:** `entrypoints/worker/main.py` (`MAX_QUEUED_AUTONOMY_TASKS`, `handle_propose_cycle`), `entrypoints/autonomy_cycle/main.py` (board saturation check before `proposer_svc.run()`)

---

### S5-8. Scope Violation Usage Store Recording

**Problem:** When kodo modified files outside `allowed_paths` (triggering the policy retry and eventual Blocked state), no persistent record was kept beyond the Plane comment and run artifact. The improve watcher and operators could not detect patterns — e.g. a task family that consistently escapes its allowed scope.

**Fix:** `UsageStore.record_scope_violation(task_id, repo_key, violated_files, now)` appends a `scope_violation` event to the usage store. Called from `service.py` after the policy-retry pass when `policy_violations` is non-empty (capped to 10 files per event to avoid bloat). These events are visible in the usage store JSON alongside execution and outcome events.

**Files:** `execution/usage_store.py` (`record_scope_violation`), `application/service.py` (call after policy violations)

---

### S5-9. Improve → Propose Systemic Feedback Channel

**Problem:** When the improve watcher detected a systemic failure pattern (same classification ≥N times in 24 hours), it sent a webhook escalation and stopped. The operator received a notification but had to manually create a root-cause investigation task. The system had no path to self-generate that task.

**Fix:** In `handle_blocked_triage`, when `should_escalate` fires and a webhook fires, the code also calls `client.create_issue` to create a single bounded `[Systemic] Investigate recurring <classification> failures` improve task. The task is:
- `task-kind: improve`, `source: improve`, `urgency: high`
- Goal: investigate and fix root cause (not scatter fixes across individual children)
- Constraint: produce a direct fix or a single bounded follow-up, not recursive children
- Deduped by title — if the task already exists on the board, no duplicate is created

**Files:** `entrypoints/worker/main.py` (`handle_blocked_triage`)

---

### S5-10. Kodo Quality Erosion Detection

**Problem:** The circuit breaker (S4-3) measures binary pass/fail. Kodo could consistently "succeed" (validation passes, PR opened) while adding `# noqa`, `# type: ignore`, or bare `pass` bodies that suppress the very errors the task was meant to fix. These pass validation and close the circuit breaker window as successes, but erode code quality over time.

**Fix:**
- `ExecutionService._count_quality_suppressions(diff_patch)` counts lines beginning with `+` in the unified diff that contain `# noqa`, `# type: ignore`/`# type:ignore`, or bare `pass`
- Runs after `diff_patch` is available; if total suppressions ≥ 3, calls `UsageStore.record_quality_warning(task_id, repo_key, suppression_counts, now)`
- `ExecutionResult.quality_suppression_counts` field carries counts to callers
- `_comment_markdown` appends a `quality_warning:` line to the Plane comment when counts are non-empty, flagging the PR for human review
- `record_quality_warning` stores `kodo_quality_warning` events (distinct from circuit-breaker events) for operator inspection

**Config:** Threshold is hardcoded at 3 total suppressions. Future: make it a settings field.

**Files:** `domain/models.py` (`ExecutionResult.quality_suppression_counts`), `application/service.py` (`_count_quality_suppressions`, quality analysis block, `_comment_markdown`), `execution/usage_store.py` (`record_quality_warning`)

---

## Session 4 — 10 Reliability and Learning Improvements

### S4-1. Watcher Auto-Restart  *[deferred, reviewed 2026-04-28 — phantom symbols, no implementation in src/]*

**Problem:** `watch-all` launched five watchers via `setsid` with no supervisor. A crash (OOM, unhandled exception) left the process dead until the operator manually ran `watch-all` again. Heartbeat detection surfaced the failure but nothing healed it.

**Fix:** `start_watch_role` now wraps each python process in a bash restart loop. On non-zero exit (crash) it logs a `watcher_restart` event and relaunches after 5 seconds. A SIGTERM trap ensures that `stop_watch_role` terminates both the wrapper bash and the running python child cleanly. Exit code 0 (intentional stop — e.g. credential failure at startup) breaks the loop.

**Files:** `scripts/operations-center.sh` (`start_watch_role`)

---

### S4-2. Task Staleness Invalidation  *[deferred, reviewed 2026-04-28 — phantom symbols, no implementation in src/]*

**Problem:** Autonomy-proposed tasks could sit in Backlog for weeks after the underlying signal was resolved (lint fixed by a PR, type errors removed by a refactor). On execution, Kodo would find nothing to do and the task would fail as a no-op, wasting budget.

**Fix:** `handle_stale_autonomy_task_scan` runs every 30 improve cycles. It scans Backlog tasks with a `source: autonomy` label older than `_STALE_AUTONOMY_TASK_DAYS = 21` days and cancels them with a `_STALE_AUTONOMY_CANCEL_MARKER` comment. The marker prevents the feedback loop from treating these as human rejections. If the signal reappears, the proposer will recreate the task.

**Files:** `main.py` (`handle_stale_autonomy_task_scan`, `_STALE_AUTONOMY_CANCEL_MARKER`, `_STALE_AUTONOMY_TASK_DAYS`, `_STALE_AUTONOMY_SCAN_CYCLE_INTERVAL`)

---

### S4-3. Success-Rate Circuit Breaker

**Problem:** The execution budget was count-based only. When something systemic broke (bad kodo version, auth regression), the system would burn the full hourly or daily budget executing tasks that all failed — providing no value and consuming all capacity before the operator could investigate.

**Fix:** `record_execution_outcome(*, task_id, role, succeeded, now)` is called after each `handle_goal_task` and `handle_test_task` completes. `budget_decision` checks the last `_CB_WINDOW = 5` execution outcomes. If `≥ _CB_THRESHOLD = 0.8` (80%) of them failed, the budget decision returns `reason="circuit_breaker_open"` and no further tasks run until the rate improves or the operator investigates. Both thresholds are tunable via `OPERATIONS_CENTER_CIRCUIT_BREAKER_THRESHOLD` and `OPERATIONS_CENTER_CIRCUIT_BREAKER_WINDOW` env vars.

**Files:** `usage_store.py` (`record_execution_outcome`, circuit-breaker check in `budget_decision`)

---

### S4-4. Parallel Slot Write Safety

**Problem:** `run_parallel_watch_loop` launches N threads sharing the same `UsageStore`. Each write method did `load() → modify → save()` with no coordination. Under `parallel_slots > 1`, two threads could read the same JSON, both modify it, and the later `write_text` would silently clobber the first writer's changes — corrupting execution counts, retry caps, and proposal history.

**Fix:** Two protections added to `UsageStore`:
1. **Per-path reentrant lock**: a module-level `dict[str, threading.RLock]` keyed by the resolved usage file path. All write methods (13 methods) acquire the lock via `_exclusive()` context manager before the load-modify-save triple.
2. **Atomic write**: `save()` writes to a `.tmp` file first, then calls `os.replace()` (which maps to `rename(2)` on Linux — atomic). Readers never see a partially-written file.

**Files:** `usage_store.py` (`_exclusive`, `_get_lock`, module-level `_path_locks`, atomic write in `save()`)

---

### S4-5. Observer Snapshot Staleness

**Problem:** The standalone `generate-insights` command reads the most recent observer snapshot from disk. If `observe-repo` hadn't run recently (or the janitor pruned it), insights were derived from stale or missing data — silently producing signals based on a week-old repo state.

**Fix:** `SnapshotLoader.latest_snapshot_age_hours()` returns how many hours ago the most recent snapshot was written. The `generate-insights` entrypoint calls this before proceeding and prints a prominent `[warn]` message if the snapshot is older than 2 hours, or if no snapshot exists at all.

**Files:** `insights/loader.py` (`latest_snapshot_age_hours`), `entrypoints/insights/main.py` (staleness check before `service.generate()`)

---

### S4-6. Connection Error Exponential Backoff  *[deferred, reviewed 2026-04-28 — phantom symbols, no implementation in src/]*

**Problem:** Transient network errors (Plane API down, DNS failure, connection refused) hit the bare `except Exception` handler in `run_watch_loop`, which logged `watch_error` and immediately re-slept for `poll_interval_seconds`. During a 10-minute Plane outage, the watcher would log an error every 30–60 seconds — 10–20 noise events before recovery.

**Fix:** `run_watch_loop` tracks `_consecutive_errors`. Each non-429 exception increments it. The backoff is `min(poll_interval * 2^(n-1), 300)` seconds — so errors 1, 2, 3, 4+ sleep for 30s, 60s, 120s, 300s with a 5-minute cap. The counter resets to 0 on any successful cycle (`else:` clause on the `try` block).

**Files:** `main.py` (`_consecutive_errors` in `run_watch_loop`, exponential backoff in `except Exception`)

---

### S4-7. Human Rejection Signal Capture  *[deferred, reviewed 2026-04-28 — phantom symbols, no implementation in src/]*

**Problem:** When a human manually cancelled an autonomy-proposed task, this "no" signal was lost. The feedback loop scan only processed Done tasks with GitHub PRs. The proposer's dedup window (7 days) would eventually expire and the same task would reappear.

**Fix:** `handle_feedback_loop_scan` now has a second pass (Part B) that scans Cancelled tasks with `source: autonomy` label. If the task was NOT cancelled by the stale-autonomy-scan (no `_STALE_AUTONOMY_CANCEL_MARKER` in comments), it is treated as a human rejection: an `abandoned` feedback record is written and the task's `candidate_dedup_key` is registered in `ProposalRejectionStore` for permanent suppression.

**Files:** `main.py` (`handle_feedback_loop_scan` Part B, `ProposalRejectionStore` import)

---

### S4-8. Execution Profiles Per Task Kind

**Problem:** Every Kodo invocation used the same `cycles`, `exchanges`, `effort`, and `timeout_seconds` regardless of task type. A quick lint fix and a complex module refactor received identical resource budgets — the lint fix wasted tokens; the refactor sometimes ran out.

**Fix:** `Settings.kodo_profiles: dict[str, KodoSettings]` accepts per-task-kind overrides keyed by `task.execution_mode` (e.g. `"goal"`, `"improve"`, `"test"`) or a special `"default"` key. `KodoAdapter.build_command` and `KodoAdapter.run` accept an optional `profile: KodoSettings | None` parameter. `ExecutionService.run_task` resolves the profile after fetching the task and passes it to all three `kodo.run` calls (initial, retry, policy retry).

**Config example:**
```yaml
kodo_profiles:
  lint_fix:
    cycles: 2
    exchanges: 10
    effort: low
  context_limit:
    cycles: 6
    exchanges: 40
    effort: high
```

**Files:** `settings.py` (`Settings.kodo_profiles`), `adapters/kodo/adapter.py` (`build_command`, `run`, `_run_with_claude_fallback`), `application/service.py` (profile resolution)

---

### S4-9. Dry-Run Quiet Diagnosis

**Problem:** The proposer could go silent (0 candidates for 5+ cycles) with no structured diagnosis. The operator had to manually read the last N cycle report JSON files and manually aggregate suppression reasons to understand why.

**Fix:** After writing each autonomy-cycle report, `_write_quiet_diagnosis()` reads the last 5 `cycle_*.json` reports. If all 5 have `candidates_emitted == 0`, it aggregates suppression reasons (counted across all cycles, sorted by frequency) and writes `logs/autonomy_cycle/quiet_diagnosis.json` with a human-readable `advice` field. The file is deleted when the proposer starts emitting again (candidates > 0). A `[warn]` line is also printed to stdout.

**Files:** `entrypoints/autonomy_cycle/main.py` (`_write_quiet_diagnosis`, called from `_write_cycle_report`)

---

### S4-10. Long-Lived Deduplication

**Problem:** The proposer's dedup window was 7 days (rolling prune). After a week, a once-abandoned or once-rejected proposal could reappear as if it were new. A proposal rejected twice in the same month would be proposed a third time the following month.

**Fix:** `ProposalRejectionStore` (new `proposer/rejection_store.py`) maintains a persistent JSON file at `state/proposal_rejections.json`. Records are keyed by `candidate_dedup_key` and store reason, task_id, task_title, and recorded_at. Records are indefinite (never pruned). `ProposerGuardrailAdapter.evaluate()` checks the rejection store first — before budget, cooldown, or open-task checks — and returns `reason="permanently_rejected_by_human"` for any rejected key. Rejection records are written by the human rejection capture (S4-7) when a Cancelled autonomy task is detected.

**Files:** `proposer/rejection_store.py` (new), `proposer/guardrail_adapter.py` (`_rejection_store` field, check in `evaluate`)

---

## Session 6 — 10 Autonomous Operation Controls

### S6-1. Maintenance Window Gate  *[deferred, reviewed 2026-04-28 — phantom symbols, no implementation in src/]*

**Problem:** The system had no way to pause autonomous execution during planned maintenance periods, deploy windows, or overnight freezes without stopping the entire watcher process.

**Fix:** `maintenance_windows:` in config accepts a list of `{start_hour, end_hour, days}` entries (UTC hours, weekday numbers). At the start of every watcher poll cycle, `_in_maintenance_window(settings, now)` is called. While a window is active, the cycle logs a `watch_maintenance_window` event and sleeps without polling or executing tasks. Wrap-midnight windows (e.g. 22:00–04:00) are supported. The autonomy-cycle entrypoint also checks the window before Stage 1.

**Config:**
```yaml
maintenance_windows:
  - start_hour: 2
    end_hour: 4
    days: [0, 1, 2, 3, 4]   # Mon–Fri 02:00–04:00 UTC
```

**Files:** `settings.py` (`MaintenanceWindow`), `entrypoints/worker/main.py` (`_in_maintenance_window`, gate in `run_watch_loop`), `entrypoints/autonomy_cycle/main.py` (gate in `main()`)

---

### S6-2. Per-Repo Daily Execution Cap

**Problem:** The global daily execution cap prevented the whole system from over-running, but one noisy repo could consume the entire quota, leaving other repos with nothing for the rest of the day.

**Fix:** `RepoSettings.max_daily_executions: int | None` (default: None = no per-repo limit). In `execution_gate_decision`, after the global budget passes, `UsageStore.budget_decision_for_repo(repo_key, max_daily, now)` counts that repo's `execution` events in the last 24 hours and returns `BudgetDecision(allowed=False)` if the cap is reached. Returns `skip_repo_budget` with the current count and limit.

**Config:**
```yaml
repos:
  high_volume_repo:
    max_daily_executions: 5
```

**Files:** `settings.py` (`RepoSettings.max_daily_executions`), `execution/usage_store.py` (`budget_decision_for_repo`), `entrypoints/worker/main.py` (`execution_gate_decision`)

---

### S6-3. Auto-Merge on CI Green for Autonomy PRs  *[deferred, reviewed 2026-04-28 — phantom symbols, no implementation in src/]*

**Problem:** Autonomy-sourced PRs required a human 👍 even when CI was fully green. For routine lint/type fixes with 100% acceptance history, the review gate was pure friction.

**Fix:** When `auto_merge_on_ci_green: true` is set on a repo and `reviewer.auto_merge_success_rate_threshold` (default 0.9) is satisfied, the review watcher merges autonomy PRs automatically once all CI checks pass. The merge only fires when: the task is labelled `source: autonomy`; the PR is open and not already merged; all CI checks passed; the system-wide success rate is above the threshold.

**Config:**
```yaml
repos:
  my_repo:
    auto_merge_on_ci_green: true
reviewer:
  auto_merge_success_rate_threshold: 0.9
```

**Files:** `settings.py` (`RepoSettings.auto_merge_on_ci_green`, `ReviewerSettings.auto_merge_success_rate_threshold`), `entrypoints/reviewer/main.py` (`_process_human_review`)

---

### S6-4. Failure Rate Degradation Detection

**Problem:** The circuit breaker (S4-3) only fires at 80% failure. A system degrading from 95% to 65% success — a meaningful signal — produced no warning until it crossed the hard threshold.

**Fix:** `UsageStore.check_failure_rate_degradation(window=30, warn_threshold=0.6, now)` computes the success rate over the last N execution outcomes. When the rate falls below `warn_threshold` (default 60%) it returns the rate (not None), otherwise None. Called every 5 primary-slot cycles in `run_watch_loop`. A `failure_rate_degradation` warning is logged with the current rate and a call to action before the circuit breaker opens.

**Files:** `execution/usage_store.py` (`check_failure_rate_degradation`), `entrypoints/worker/main.py` (check in `run_watch_loop`)

---

### S6-5. Execution Duration Baseline

**Problem:** A task running for 4 hours while the normal runtime was 20 minutes was invisible until the stale-running TTL fired and re-queued it. By then, Kodo may have been stuck in a loop for hours consuming API quota.

**Fix:** `UsageStore.record_execution_duration(task_id, role, duration_seconds, now)` is called after each goal/test execution with the wall-clock time. `median_execution_duration(role)` computes the median over the last 20 runs. When the current run takes >2× the median, a `duration_anomaly` warning is logged. Both methods are used in `handle_goal_task` and `handle_test_task`.

**Files:** `execution/usage_store.py` (`record_execution_duration`, `median_execution_duration`), `entrypoints/worker/main.py` (`handle_goal_task`, `handle_test_task`)

---

### S6-6. Pre-Execution Rejection Feedback  *[deferred, reviewed 2026-04-28 — phantom symbols, no implementation in src/]*

**Problem:** When `validate_task_pre_execution` rejected a task, the rejection was not reflected in the proposal success-rate store. Future cycles could keep proposing the same category of un-actionable tasks.

**Fix:** When pre-execution validation rejects a task, `UsageStore.record_proposal_outcome(category, succeeded=False, now)` is called before the task is moved to Backlog. The category is derived from the task's `task-kind:` label. This feeds the per-category success rate the same way a full execution failure would.

**Files:** `entrypoints/worker/main.py` (`run_watch_loop`, after validation rejection)

---

### S6-7. Safe Revert Detection for Post-Merge Regressions  *[deferred, reviewed 2026-04-28 — phantom symbols, no implementation in src/]*

**Problem:** `detect_post_merge_regressions` could flag a regression but had no way to know whether a revert was safe — the merged commit may have been built on by subsequent commits, making a naive revert destructive.

**Fix:** After detecting a failing CI check on a merged PR, `detect_post_merge_regressions` calls `get_branch_head(owner, repo, base_branch)` to check whether the merge commit SHA is still the latest commit on the base branch. If yes, the regression task's `recommended_action` is set to `revert`; if not (subsequent commits exist), it is set to `investigate`. The task description reflects the recommendation.

**Files:** `entrypoints/worker/main.py` (`detect_post_merge_regressions`)

---

### S6-8. Kodo Version Attribution in Execution Outcomes

**Problem:** When a kodo version upgrade caused widespread failures, it was impossible to distinguish "kodo bug" from "task quality issue" in the usage store. The circuit breaker would open, but the root cause remained opaque.

**Fix:** `_get_kodo_version(binary)` is cached per watcher startup (module-level dict). The result is passed as `kodo_version=` to `UsageStore.record_execution_outcome()`. When the kodo version transitions mid-window (old version → new version or vice versa), the circuit-breaker check skips outcomes from the old version in its sliding window to prevent a version upgrade from triggering a false positive.

**Files:** `entrypoints/worker/main.py` (`_get_kodo_version`, `_kodo_version_cache`, called from `handle_goal_task`/`handle_test_task`), `execution/usage_store.py` (`record_execution_outcome`, version-transition check in `budget_decision`)

---

### S6-9. Structured Audit Log Export

**Problem:** Operators had no structured way to answer "what did the system do this week?" without manually parsing JSON event files or reading Plane comment threads.

**Fix:** `UsageStore.audit_export(window_days, now)` maps `execution_outcome` events to human-friendly `{kind: "execution", task_id, outcome, succeeded, role, kodo_version, timestamp}` dicts. The `audit-export` CLI subcommand prints the full list as JSON.

```bash
python -m operations_center.entrypoints.worker.main audit-export --window-days 7
python -m operations_center.entrypoints.worker.main audit-export --window-days 30 > audit.json
```

**Files:** `execution/usage_store.py` (`audit_export`), `entrypoints/worker/main.py` (`audit-export` subcommand in `main()`)

---

### S6-10. Board Health Snapshot  *[deferred, reviewed 2026-04-28 — phantom symbols, no implementation in src/]*

**Problem:** Detecting board anomalies (tasks stuck in Running, a classification appearing on 10+ blocked tasks, an entire repo lane going quiet) required manual board inspection. No automated signal existed for systemic board health problems.

**Fix:** `board_health_check(issues, service)` detects three anomaly patterns:
1. **stuck_running** — ≥3 tasks in `Running` state simultaneously (watchers should never leave tasks Running).
2. **clustered_blocked_reason** — ≥5 blocked tasks with the same `blocked_classification` label (systemic failure pattern).
3. **quiet_repo_lane** — a configured repo has zero active tasks (Ready for AI or Running) while other repos do (may indicate a per-repo budget cap or config issue).

Called every 40 improve cycles in `run_watch_loop`. Also available as the `board-health` CLI subcommand.

```bash
python -m operations_center.entrypoints.worker.main board-health --config config/operations_center.local.yaml
```

**Files:** `entrypoints/worker/main.py` (`board_health_check`, `board-health` subcommand)

---

## Session 7 — 7 Full-Autonomy Infrastructure Gaps

### S7-1. Process Supervisor  *[deferred, reviewed 2026-04-28 — phantom symbols, no implementation in src/]*

**Problem:** The `watch-all` bash restart loop (S4-1) restarted crashed watchers, but had no independent watchdog that could survive if the shell session died, no structured restart-count tracking, and no manifest-driven process management.

**Fix:** New entrypoint `entrypoints/supervisor/main.py` reads a YAML manifest listing processes to manage. It spawns each as a subprocess, then every `check_interval` seconds (default 30):
1. Checks whether each process is still alive (`poll()` is not None).
2. Checks whether each process's heartbeat file is stale (> 5 minutes old).
3. On either condition: kills the existing process (if any) and restarts after `restart_backoff_seconds`.

Per-process `restart_max` limits the number of automatic restart attempts (default: unlimited). Writes `logs/local/supervisor.status.json` on every check iteration for external observability.

**Manifest format:**
```yaml
processes:
  - role: goal
    command: ["python", "-m", "operations_center.entrypoints.worker.main",
              "--config", "/path/to/config.yaml", "--watch", "--role", "goal",
              "--status-dir", "logs/local"]
    restart_backoff_seconds: 10
  - role: improve
    command: [...]
```

**Files:** `entrypoints/supervisor/__init__.py`, `entrypoints/supervisor/main.py` (new)

---

### S7-2. Credential Rotation Detection  *[deferred, reviewed 2026-04-28 — phantom symbols, no implementation in src/]*

**Problem:** `validate_credentials` (S2-7) detected invalid tokens (401/403) but had no awareness of upcoming expiry. A GitHub fine-grained PAT expiring at midnight would not be caught until it actually expired, halting all execution.

**Fix:** After a successful GitHub `/user` check, the response's `x-token-expiration` header is read (present on fine-grained PATs). When expiry is within `escalation.credential_expiry_warn_days` days (default 7), a `credential_expiry_soon` warning is logged. When ≤1 day remains, an error is logged and a `credential_github_expiring` escalation event is recorded, which can trigger the escalation webhook on the next threshold check.

**Config:**
```yaml
escalation:
  credential_expiry_warn_days: 7  # 0 = disabled
```

**Files:** `settings.py` (`EscalationSettings.credential_expiry_warn_days`), `entrypoints/worker/main.py` (`validate_credentials`)

---

### S7-3. Transcript Failure Classification  *[deferred, reviewed 2026-04-28 — phantom symbols, no implementation in src/]*

**Problem:** `classify_execution_result` had a coarse `infra_tooling` catch-all for anything that wasn't a context limit, missing dependency, or validation failure. This meant process timeouts, model API errors, and OOM kills all produced the same classification and the same (wrong) follow-up recommendation.

**Fix:** Three new classifications added before `infra_tooling`, checked in priority order:

| Classification | Trigger patterns |
|---------------|-----------------|
| `oom` | "out of memory", "cannot allocate memory", "killed", "oom" |
| `timeout` | "timed out", "timeout", "operation timed out", "deadline exceeded" |
| `model_error` | "internal server error", "service unavailable", "overloaded", "bad gateway", "rate_limit_error" |

A fourth classification, `tool_failure`, covers bash/git/file tool errors distinct from auth failures. The `infra_tooling` bucket no longer captures timeouts.

**Files:** `entrypoints/worker/main.py` (`classify_execution_result`)

---

### S7-4. Self-Healing for Repeatedly Blocked Tasks

**Problem:** A task could cycle through `Blocked → triage → new follow-up → Blocked` indefinitely. Each cycle created another follow-up task without ever detecting the loop pattern. The board would accumulate chains of blocked tasks with no systemic intervention.

**Fix:** `UsageStore.consecutive_blocks_for_task(task_id, now)` counts backwards through events for that task_id: increments for each `blocked_triage` event, stops and resets when a successful `execution_outcome` is found. In `handle_blocked_triage`, after recording the triage event, if the consecutive count reaches `CONSECUTIVE_BLOCK_COOLDOWN_THRESHOLD = 3`, a self-healing comment is posted on the task and a `self_healing_repeated_block` warning is logged. The comment recommends human review and notes that autonomous retries are paused.

**Files:** `execution/usage_store.py` (`consecutive_blocks_for_task`), `entrypoints/worker/main.py` (`CONSECUTIVE_BLOCK_COOLDOWN_THRESHOLD`, `handle_blocked_triage`)

---

### S7-5. Dependency Update Loop  *[deferred, reviewed 2026-04-28 — phantom symbols, no implementation in src/]*

**Problem:** Outdated package dependencies were only detected via the manual `dependency-check` CLI or when a task failed with a missing API. There was no autonomous path to create bounded update tasks for major-version bumps.

**Fix:** `handle_dependency_update_scan()` runs every 50 improve cycles (new `_DEPENDENCY_UPDATE_SCAN_CYCLE_INTERVAL`). For each repo with `local_path` configured:
1. Runs `pip list --outdated --format=json` inside the repo's venv (falls back to system Python if no venv found).
2. For each package with a **major-version bump** (current major < latest major), creates a bounded Plane task in Backlog.
3. Tasks are deduplicated against existing board tasks by title.
4. At most `_MAX_DEPENDENCY_UPDATE_TASKS_PER_SCAN = 2` tasks are created per scan to avoid board floods.

**Files:** `entrypoints/worker/main.py` (`handle_dependency_update_scan`, `_DEPENDENCY_UPDATE_SCAN_CYCLE_INTERVAL`, `_MAX_DEPENDENCY_UPDATE_TASKS_PER_SCAN`, wired into `run_watch_loop`)

---

### S7-6. Cross-Repo Impact Analysis  *[deferred, reviewed 2026-04-28 — phantom symbols, no implementation in src/]*

**Problem:** When a task modified a shared interface (a public API module, a protocol definition, a shared utility), the system had no awareness that sibling repos might depend on it. Breaking changes could be merged without any indication that other repos needed updating.

**Fix:** New `RepoSettings.impact_report_paths: list[str]` field declares paths in a repo that are shared interfaces. After each successful goal execution, `_check_cross_repo_impact(changed_files, service)` checks whether any changed file starts with a declared `impact_report_paths` prefix from any repo in settings. When a match is found, a `[Goal] Cross-repo impact detected` comment is posted on the task listing the affected repo and path, and a `cross_repo_impact_detected` warning is logged.

**Config:**
```yaml
repos:
  shared_lib:
    impact_report_paths:
      - src/api/
      - proto/
```

**Files:** `settings.py` (`RepoSettings.impact_report_paths`), `entrypoints/worker/main.py` (`_check_cross_repo_impact`, called from `handle_goal_task`)

---

### S7-7. Human Escalation Wiring

**Problem:** Escalation (S2-1) fired only when the same blocked-task classification crossed a threshold. Two other critical failure modes had no escalation path: (1) the circuit breaker tripping on a systemic failure, (2) the autonomy proposer going silent for many consecutive cycles.

**Fix (circuit breaker):** In `run_watch_loop`, every 5 cycles on the primary slot, after the failure-rate degradation check, `budget_decision()` is checked for `reason="circuit_breaker_open"`. When the circuit breaker has tripped and an escalation webhook is configured, `should_escalate(classification="circuit_breaker_tripped", threshold=1, ...)` fires and sends a POST. A `circuit_breaker_escalation_sent` error event is logged.

**Fix (quiet proposer):** In `_write_quiet_diagnosis()`, after writing `quiet_diagnosis.json`, when a webhook is configured and `should_escalate(classification="proposer_quiet", threshold=1, ...)` fires, `post_escalation` sends a POST with `count=N` (number of quiet cycles). The escalation is cooldown-guarded to avoid repeated POSTs on consecutive quiet cycles.

**Files:** `entrypoints/worker/main.py` (circuit-breaker escalation in `run_watch_loop`), `entrypoints/autonomy_cycle/main.py` (`_write_quiet_diagnosis`, `_write_cycle_report` — escalation kwargs propagated)

---

## Summary Table (continued)

| # | Name | Trigger | Where |
|---|------|---------|-------|
| S6-1 | Maintenance window gate | every poll cycle | `_in_maintenance_window`, `run_watch_loop` |
| S6-2 | Per-repo daily execution cap | before execution | `budget_decision_for_repo`, `execution_gate_decision` |
| S6-3 | Auto-merge on CI green | review watcher | `_process_human_review` |
| S6-4 | Failure rate degradation | every 5 cycles | `check_failure_rate_degradation`, `run_watch_loop` |
| S6-5 | Execution duration baseline | after each execution | `record_execution_duration`, `handle_goal_task` |
| S6-6 | Pre-exec rejection feedback | pre-execution validation | `run_watch_loop`, `record_proposal_outcome` |
| S6-7 | Safe revert detection | every 10 improve cycles | `detect_post_merge_regressions` |
| S6-8 | Kodo version attribution | after each execution | `_get_kodo_version`, `record_execution_outcome` |
| S6-9 | Structured audit log export | CLI subcommand | `audit_export`, `audit-export` |
| S6-10 | Board health snapshot | every 40 improve cycles + CLI | `board_health_check`, `board-health` |
| S7-1 | Process supervisor | continuous | `entrypoints/supervisor/main.py` |
| S7-2 | Credential rotation detection | cycle 1 startup | `validate_credentials` |
| S7-3 | Transcript failure classification | classify after execution | `classify_execution_result` |
| S7-4 | Self-healing repeated blocks | after blocked triage | `consecutive_blocks_for_task`, `handle_blocked_triage` |
| S7-5 | Dependency update loop | every 50 improve cycles | `handle_dependency_update_scan` |
| S7-6 | Cross-repo impact analysis | after successful goal | `_check_cross_repo_impact`, `handle_goal_task` |
| S7-7 | Human escalation wiring | every 5 cycles + quiet diagnosis | `run_watch_loop`, `_write_quiet_diagnosis` |

---

## Session 8 — 10 execution depth and calibration improvements

These improvements completed execution feedback depth work, added confidence calibration, quality trend tracking, runtime error ingestion, and several robustness fixes.

### S8-1. Feedback Loop Config Wiring  *[deferred, reviewed 2026-04-28 — phantom symbols, no implementation in src/]*

**Problem:** The feedback loop scan used a hardcoded `stale_autonomy_backlog_days` value and ignored the config field, so operator tuning had no effect.

**Fix:** `handle_stale_autonomy_task_scan()` now reads `stale_autonomy_backlog_days` from `service.settings` (falling back to the internal default) on every call. The `run_watch_loop` passes the config-derived value down.

**Files:** `config/settings.py` (`stale_autonomy_backlog_days: int = 30`), `entrypoints/worker/main.py` (wiring in `run_watch_loop`)

---

### S8-2. ExecutionOutcomeDeriver  *[deferred, reviewed 2026-04-28 — phantom symbols, no implementation in src/]*

**Problem:** The execution feedback depth feature was deferred — a `# Deferred: ExecutionOutcomeDeriver` comment existed in `autonomy_cycle/main.py` but no deriver was implemented.

**Fix:** New `ExecutionOutcomeDeriver` in `insights/derivers/execution_outcome.py`. Reads retained `control_outcome.json` and `stderr.txt` artifacts from `tools/report/kodo_plane/`. Classifies three failure modes:
- `timeout_pattern` — ≥2 timeout failures across retained runs
- `test_regression` — test-output pattern found in stderr of a validation failure
- `validation_loop` — same `task_id` fails validation ≥3 times

Each classification emits a corresponding insight under the `execution_outcome/` namespace.

**Files:** `insights/derivers/execution_outcome.py` (NEW), `entrypoints/autonomy_cycle/main.py` (registered in `build_insight_service()`)

---

### S8-3. Stale Pruning + Semantic Deduplication  *[deferred, reviewed 2026-04-28 — phantom symbols, no implementation in src/]*

**Problem (a):** Stale autonomy backlog pruning ignored the config field (see S8-1).

**Problem (b):** The existing exact-title deduplication missed near-duplicate proposals whose titles differed by small wording changes (e.g. `Fix lint errors in auth.py` vs. `Resolve linting in auth.py`).

**Fix (b):** New `_semantic_title_similarity(a, b) -> float` using Jaccard similarity on word tokens (≥3 characters). `[...]` prefix markers are stripped before comparison. Threshold `_SEMANTIC_DEDUP_THRESHOLD = 0.5`. Applied in `create_proposed_task_if_missing()` after the exact-title check but before any Plane API call.

**Files:** `entrypoints/worker/main.py` (`_semantic_title_similarity`, `_SEMANTIC_DEDUP_THRESHOLD`, applied in `create_proposed_task_if_missing`)

---

### S8-4. Goal Decomposition (Multi-Step Planning)  *[deferred, reviewed 2026-04-28 — phantom symbols, no implementation in src/]*

**Problem:** `build_multi_step_plan()` was already implemented but not covered by tests and the wiring was not verified end-to-end.

**Status:** Confirmed working. Decomposes tasks with titles containing `migrate`, `refactor`, `redesign`, etc. into Analyze → Implement → Verify subtasks with `depends_on:` chains.

**Files:** `entrypoints/worker/main.py` (`build_multi_step_plan`)

---

### S8-5. Automatic Revert Branch on Post-Merge Regression

**Problem:** `detect_post_merge_regressions()` detected `recommended_action: revert` cases but only logged the finding — no automated revert branch or PR was created.

**Fix:** New `GitClient.revert_commit(repo_path, commit_sha, *, new_branch) -> bool` and `ExecutionService.create_revert_branch(*, clone_url, base_branch, merge_sha, revert_branch) -> bool`. When `detect_post_merge_regressions()` finds a safe-revert case (merge commit still at HEAD), it calls `service.create_revert_branch()` and then `gh.create_pr()` to open an auto-revert PR. The PR is flagged `[Revert]` for human review.

**Files:** `adapters/git/client.py` (`revert_commit`), `application/service.py` (`create_revert_branch`), `entrypoints/worker/main.py` (`detect_post_merge_regressions` — revert branch + PR creation)

---

### S8-6. Branch Divergence Detection in Review Loop  *[deferred, reviewed 2026-04-28 — phantom symbols, no implementation in src/]*

**Problem:** The reviewer watcher only attempted a rebase reactively (when it received a `CHANGES_REQUESTED` review). A PR that fell behind `main` due to other merges would sit stuck until a human noticed.

**Fix:** In `_process_human_review()`, before the normal review pass, the PR's `mergeable_state` is fetched. When it is `"behind"` and no rebase has been attempted in this state file, `_try_auto_rebase()` is called proactively. The `auto_rebase_attempted` flag prevents repeated attempts on the same PR.

**Files:** `entrypoints/reviewer/main.py` (`_process_human_review` — proactive divergence check)

---

### S8-7. Quality Trend Tracking

**Problem:** The system could tell that lint errors exist today, but had no objective function to know whether quality was improving or degrading over time. Without this, the system could loop on tasks that make no net progress.

**Fix:** New `QualityTrendDeriver` in `insights/derivers/quality_trend.py`. Requires ≥3 observer snapshots. Computes lint and type error deltas (oldest → newest). Emits insights with a 10% change threshold:
- `quality_trend/lint_improving`, `quality_trend/lint_degrading`
- `quality_trend/type_improving`, `quality_trend/type_degrading`
- `quality_trend/stagnant` when metrics exist but show <10% change in either direction

**Files:** `insights/derivers/quality_trend.py` (NEW), `entrypoints/autonomy_cycle/main.py` (registered in `build_insight_service()`)

---

### S8-8. Runtime Error Ingestion

**Problem:** The system had no way to learn about runtime errors. Production errors that triggered alerting pipelines were invisible; Kodo would fix what it could see in static analysis but never what manifested at runtime.

**Fix:** New `entrypoints/error_ingest/main.py`:
- **Webhook receiver:** `run_webhook_server(plane_client, *, port, default_repo_key)` starts a `ThreadingHTTPServer` on `POST /ingest`; accepts JSON `{text, repo_key?}`; creates Plane tasks.
- **Log tail watcher:** `_tail_log_file(...)` follows a file for lines matching a regex; deduplicates via `state/error_ingest_dedup.json` with a configurable time window.
- Dedup key is a stable hash of `(repo_key, text[:200])`.

**Config:**
```yaml
error_ingest:
  webhook_port: 9000
  default_repo_key: myrepo
  log_sources:
    - path: /var/log/myapp/app.log
      repo_key: myrepo
      pattern: "(ERROR|CRITICAL)"
      dedup_window_seconds: 3600
```

**Files:** `config/settings.py` (`ErrorIngestSettings`, `ErrorIngestLogSource`), `entrypoints/error_ingest/main.py` (NEW)

---

### S8-9. Explicit Approval Control  *[deferred, reviewed 2026-04-28 — phantom symbols, no implementation in src/]*

**Problem:** Repos that require human sign-off before any merge had no mechanism to prevent the reviewer watcher from timing out and auto-merging. The `auto_merge_on_ci_green` flag controlled automatic CI-green merges but not the 1-day timeout-merge path.

**Fix:** New `RepoSettings.require_explicit_approval: bool = False`. When `True`, the reviewer watcher never executes timeout-based merges. Instead, it posts a reminder comment on the PR (at most once per day) asking for explicit human approval. The comment carries `<!-- operations-center:bot -->` so it is not re-processed.

**Config:**
```yaml
repos:
  production_repo:
    require_explicit_approval: true
```

**Files:** `config/settings.py` (`RepoSettings.require_explicit_approval`), `entrypoints/reviewer/main.py` (`_process_human_review` — timeout-merge blocked, reminder posted)

---

### S8-10. Confidence Calibration Store

**Problem:** The system assigned `confidence: high/medium/low` labels to proposals, but never tracked whether those labels were accurate. A family systematically over-confident would be hard to detect without a calibration record.

**Fix:** New `ConfidenceCalibrationStore` in `tuning/calibration.py`. Backed by `state/calibration_store.json`. API:
- `record(family, confidence, outcome)` — records one feedback event
- `calibration_for(family, confidence) -> float | None` — returns acceptance rate (requires `_MIN_SAMPLE_SIZE = 5`)
- `report() -> list[CalibrationRecord]` — all calibrated family/confidence pairs

Expected acceptance rates: `high=0.8`, `medium=0.5`, `low=0.3`. `calibration_ratio = acceptance_rate / expected_rate`. Ratio < 0.6 flagged as over-confident (⚠); ≥ 0.9 is well-calibrated (✓).

Wired into `feedback record` CLI (optional `--family` / `--confidence` args) and `tune-autonomy` output (calibration table printed after the standard tuning report).

**Files:** `tuning/calibration.py` (NEW), `entrypoints/feedback/main.py` (`--family`, `--confidence` args + `record()` call), `entrypoints/tuning/main.py` (calibration table output)

---

## Summary Table (continued)

| # | Name | Trigger | Where |
|---|------|---------|-------|
| S8-1 | Feedback loop config wiring | every 15 improve cycles | `handle_feedback_loop_scan`, `run_watch_loop` |
| S8-2 | ExecutionOutcomeDeriver | autonomy-cycle insights stage | `execution_outcome.py`, `build_insight_service` |
| S8-3 | Semantic deduplication | before task creation | `_semantic_title_similarity`, `create_proposed_task_if_missing` |
| S8-4 | Goal decomposition (multi-step) | on complex task detection | `build_multi_step_plan` |
| S8-5 | Auto revert branch on regression | every 10 improve cycles | `detect_post_merge_regressions`, `create_revert_branch` |
| S8-6 | Proactive branch divergence check | reviewer loop per PR | `_process_human_review` |
| S8-7 | Quality trend tracking | autonomy-cycle insights stage | `quality_trend.py`, `build_insight_service` |
| S8-8 | Runtime error ingestion | continuous webhook + log tail | `entrypoints/error_ingest/main.py` |
| S8-9 | Explicit approval control | reviewer timeout-merge path | `RepoSettings.require_explicit_approval`, `_process_human_review` |
| S8-10 | Confidence calibration store | tune-autonomy + feedback record | `tuning/calibration.py`, `tune-autonomy` output |

---

## Session 9 — 10 structural and observability improvements

### S9-1. Event-Driven Pipeline Trigger  *[deferred, reviewed 2026-04-28 — phantom symbols, no implementation in src/]*

**Problem:** The full pipeline (`observe → insights → decide → propose`) runs on a schedule or manually. When something important happens — a CI failure, a runtime error ingested, a new git push — the system cannot react immediately.

**Fix:** New `entrypoints/pipeline_trigger/main.py`. Watches three trigger sources:
1. `.git/FETCH_HEAD` mtime in each configured repo's `local_path` (new push/fetch)
2. `state/error_ingest_dedup.json` mtime (new errors ingested)
3. `tools/report/kodo_plane/` child count (new execution artifacts)

When any source changes, fires `autonomy-cycle --config <config> [--execute]` as a subprocess. Debounce: minimum 5 minutes between triggered runs (configurable). State persisted in `state/pipeline_trigger_state.json`.

**Usage:**
```bash
python -m operations_center.entrypoints.pipeline_trigger.main \
    --config config/operations_center.local.yaml \
    --execute \
    --min-interval 300
```

**Files:** `entrypoints/pipeline_trigger/main.py` (NEW)

---

### S9-2. Execution Environment Pre-Flight Probe  *[deferred, reviewed 2026-04-28 — phantom symbols, no implementation in src/]*

**Problem:** Before claiming a task, the system validates scope and goal quality, but not whether the tools needed to execute actually exist. A `type_fix` task can be claimed and fail immediately with `dependency_missing` because `ty` isn't installed.

**Fix:** New `_check_execution_environment(service, family) -> list[str]` using `shutil.which()` for PATH lookup, falling back to checking the first configured repo's `.venv/bin/`. Per-family tool requirements:

| Family | Required tools (any one in group) |
|--------|-----------------------------------|
| `lint_fix` | `ruff` |
| `type_fix` | `ty` or `mypy` |
| `test_fix` | `pytest` |
| `coverage_gap` | `pytest` or `coverage` |

Warnings are logged but execution is not blocked — soft signal only, since tool availability can't always be determined (e.g. tools installed in CI, not locally).

**Files:** `entrypoints/worker/main.py` (`_check_execution_environment`, `_KIND_REQUIRED_TOOLS`, wired into `validate_task_pre_execution`)

---

### S9-3. No-Op Loop Detection

**Problem:** When the same signal keeps firing and the same family keeps creating tasks that either get abandoned or re-create the same problem, the system has no way to recognize it's cycling without net progress.

**Fix:** New `NoOpLoopDeriver` in `insights/derivers/noop_loop.py`. Reads proposer result artifacts and proposal feedback files from the last 30 days. For each family: if proposed ≥3 times (`min_proposals=3`) with zero merged outcomes in that window, emits `noop_loop/family_cycling` with evidence including proposal count and merge count.

**Files:** `insights/derivers/noop_loop.py` (NEW), `entrypoints/autonomy_cycle/main.py` (registered in `build_insight_service()`)

---

### S9-4. Per-Repo × Family Calibration

**Problem:** The calibration store tracks global acceptance rates per family. A `type_fix` task in a strictly-typed repo is a very different proposition than the same family in a legacy codebase — global calibration masks repo-specific patterns.

**Fix:** `ConfidenceCalibrationStore.record()`, `calibration_for()`, and `report()` all accept an optional `repo_key` parameter. Events now carry a `repo_key` field. `calibration_for(family, confidence, repo_key="myrepo")` returns the repo-specific acceptance rate. `report(per_repo=True)` groups by (repo_key, family, confidence) and returns `CalibrationRecord` with `repo_key` field set.

**Files:** `tuning/calibration.py` (extended `record`, `calibration_for`, `report`; `CalibrationRecord.repo_key` field added)

---

### S9-5. Rejection Reason Extraction  *[deferred, reviewed 2026-04-28 — phantom symbols, no implementation in src/]*

**Problem:** When a human rejects a PR, the outcome is recorded as `abandoned` but the comment explaining *why* is discarded. If the same rejection pattern recurs, the system never learns to add that constraint to future proposals.

**Fix:** New `_extract_rejection_patterns(comments, *, family, repo_key) -> list[str]` scans PR review comments against 8 pattern categories:

| Pattern | Keywords detected |
|---------|-------------------|
| `missing_tests` | missing test, needs test, add test |
| `naming_convention` | naming convention, variable name, rename |
| `missing_docstrings` | missing docstring, needs docstring |
| `coverage_gap` | coverage, uncovered, untested branch |
| `code_style` | style, formatting, whitespace |
| `scope_too_large` | too large, too many files, split |
| `missing_type_annotations` | type annotation, type hint |
| `breaking_change` | breaking change, backwards compat |

`_record_rejection_patterns()` persists counts to `state/rejection_patterns.json` keyed by `{repo_key}:{family}`. `load_rejection_patterns()` returns the top-3 by frequency. Wired into `_escalate_to_human()` so every escalation automatically extracts patterns.

**Files:** `entrypoints/reviewer/main.py` (`_extract_rejection_patterns`, `_record_rejection_patterns`, `load_rejection_patterns`, `_REJECTION_PATTERNS_PATH`, wired in `_escalate_to_human`)

---

### S9-6. Budget Allocation by Acceptance Rate  *[deferred, reviewed 2026-04-28 — phantom symbols, no implementation in src/]*

**Problem:** The daily execution budget is distributed first-come-first-served. High-acceptance-rate families compete for the same budget slots as low-acceptance-rate families. There's no proportional weighting.

**Fix:** In `execution_gate_decision()`, after the standard budget check, calibration is read for the task's source_family and confidence. When `calibration_ratio < 0.5` (over-confident family performing at less than half its expected acceptance rate), `record_execution()` is called twice — once for the task, once for a `calibration_penalty` marker. This consumes double the daily budget credit, effectively halving the execution rate for that family without fully blocking it.

**Files:** `entrypoints/worker/main.py` (budget penalty block in `execution_gate_decision`)

---

### S9-7. Test Coverage Gap Detection  *[deferred, reviewed 2026-04-28 — phantom symbols, no implementation in src/]*

**Problem:** The system detects test regressions and test signal failures, but has no visibility into which code paths have *no tests at all*. Coverage gaps can't be proposed as tasks because the signal doesn't exist.

**Fix:** Three new components:

**`CoverageSignalCollector`** (`observer/collectors/coverage_signal.py`) — reads (in priority order):
1. `coverage.xml` (Cobertura XML): file-level coverage percentages
2. `pytest-coverage.txt` / `coverage.txt`: total line only
3. `htmlcov/index.html`: total from HTML header

Returns `CoverageSignal(status="measured", total_coverage_pct=..., uncovered_file_count=..., top_uncovered=[...])` or `status="unavailable"` when no report is found. Never runs coverage tools.

**`CoverageGapDeriver`** (`insights/derivers/coverage_gap.py`) — emits:
- `coverage_gap/low_overall` when `total_coverage_pct < 60%`
- `coverage_gap/uncovered_files` when ≥3 files are below 80% threshold

**`CoverageGapRule`** (`decision/rules/coverage_gap.py`) — proposes `[Improve] Add tests for N under-covered file(s)` tasks.

**Model:** `CoverageSignal` and `UncoveredFile` added to `observer/models.py`; `coverage_signal` field added to `RepoSignalsSnapshot`.

**Files:** `observer/models.py`, `observer/collectors/coverage_signal.py` (NEW), `observer/service.py`, `insights/derivers/coverage_gap.py` (NEW), `decision/rules/coverage_gap.py` (NEW), `entrypoints/autonomy_cycle/main.py`

---

### S9-8. PR Description Quality Check

**Problem:** Kodo writes PR descriptions, but the reviewer watcher doesn't verify they contain useful information. Empty or one-line descriptions lead to human reviewers ignoring PRs.

**Fix:** New `_check_pr_description_quality(gh, owner, repo, pr_number, *, task_description, marker, logger)`. Runs once per PR (guarded by `state["description_checked"]`) before `_process_self_review` calls `run_self_review_pass()`. When the PR description body is fewer than 80 characters, fetches the Plane task description and patches the PR body via `gh.update_pr_description()` with an auto-generated description noting the source.

New `GitHubPRClient.update_pr_description()` uses `PATCH /repos/{owner}/{repo}/pulls/{number}`.

**Files:** `adapters/github_pr.py` (`update_pr_description`), `entrypoints/reviewer/main.py` (`_check_pr_description_quality`, wired into `_process_self_review`)

---

### S9-9. Evidence-Enriched Conflict Avoidance  *[deferred, reviewed 2026-04-28 — phantom symbols, no implementation in src/]*

**Problem:** The three-tier conflict detection uses filename tokens from the proposal title (low fidelity). Proposal evidence_lines often contain actual file paths, giving a more accurate picture of what a task would touch.

**Fix:** New `_extract_evidence_file_tokens(evidence_lines) -> set[str]` extracts file path basenames from evidence_lines using regex. In the proposal loop, both the title-based conflict check AND an evidence-based conflict check are run before creating a task:

```python
_conflict_title_check = _has_conflict_with_active_task(proposal.title, ...)
_conflict_evidence_check = _has_conflict_with_active_task(" ".join(evidence_files), ...)
if _conflict_title_check or _conflict_evidence_check:
    suppress
```

The log event `propose_conflict_skipped` now includes `evidence_files_checked` count.

**Files:** `entrypoints/worker/main.py` (`_extract_evidence_file_tokens`, extended conflict check in proposal loop)

---

### S9-10. Theme Aggregation Deriver

**Problem:** The system emits individual lint candidates per insight. If the same file appears in top lint violations across 5 consecutive snapshots, the system proposes 5 individual `lint_fix` tasks for it rather than recognizing the structural pattern.

**Fix:** New `ThemeAggregationDeriver` in `insights/derivers/theme_aggregation.py`. Requires ≥3 snapshots (`_MIN_SNAPSHOTS`). Counts how many snapshots each file appears in top lint violations / top type errors. Files appearing in ≥3 snapshots (`_MIN_SNAPSHOT_APPEARANCES`) emit:
- `theme/lint_cluster` — with `file`, `snapshot_appearances`, `snapshots_analyzed` evidence
- `theme/type_cluster` — same structure

New `LintClusterRule` in `decision/rules/lint_cluster.py` turns these into `[Refactor] Systematic lint cleanup: <file>` proposals with `family="lint_cluster"`, confidence="high". At most `_MAX_CLUSTER_FILES = 3` insights per run.

`lint_cluster` and `coverage_gap` added to `ALL_FAMILIES` in decision service.

**Files:** `insights/derivers/theme_aggregation.py` (NEW), `decision/rules/lint_cluster.py` (NEW), `decision/service.py` (`ALL_FAMILIES`, `_build_rules`), `entrypoints/autonomy_cycle/main.py`

---

## Summary Table (continued)

| # | Name | Trigger | Where |
|---|------|---------|-------|
| S9-1 | Event-driven pipeline trigger | on repo/ingest/CI change | `entrypoints/pipeline_trigger/main.py` |
| S9-2 | Execution env pre-flight probe | before task claim | `_check_execution_environment`, `validate_task_pre_execution` |
| S9-3 | No-op loop detection | autonomy-cycle insights stage | `noop_loop.py`, `build_insight_service` |
| S9-4 | Per-repo × family calibration | feedback record + tune-autonomy | `ConfidenceCalibrationStore.record/report` |
| S9-5 | Rejection reason extraction | on PR escalation to human | `_extract_rejection_patterns`, `_escalate_to_human` |
| S9-6 | Budget allocation by acceptance rate | before execution | `execution_gate_decision` calibration penalty |
| S9-7 | Test coverage gap detection | autonomy-cycle observe + insights | `coverage_signal.py`, `coverage_gap.py`, `CoverageGapRule` |
| S9-8 | PR description quality check | before self-review | `_check_pr_description_quality`, `update_pr_description` |
| S9-9 | Evidence-enriched conflict avoidance | before task creation | `_extract_evidence_file_tokens`, proposal loop |
| S9-10 | Theme aggregation | autonomy-cycle insights + decision | `theme_aggregation.py`, `LintClusterRule` |

---

## Session 10 — 10 Learning, Feedback, and Intelligence Improvements

### S10-1: Rejection Patterns Injected into Kodo Prompts  *[deferred, reviewed 2026-04-28 — phantom symbols, no implementation in src/]*

**Problem:** The proposer generated task descriptions without knowledge of what human reviewers had previously flagged. When a reviewer rejected a PR for "missing tests", the next proposal for the same family would not mention this concern, leading to repeat rejections.

**Fix:** `build_proposal_description()` in `worker/main.py` now calls `_load_rejection_patterns_for_proposal(family, repo_key)` which reads `state/rejection_patterns.json` (maintained by the reviewer watcher). When top-3 patterns exist, a **## Prior Rejection Patterns** section is appended to the task description, instructing Kodo to proactively address them.

**Files:** `entrypoints/worker/main.py` (`_load_rejection_patterns_for_proposal`, `_REJECTION_PATTERNS_PATH`, `build_proposal_description`)

---

### S10-2: Question-Asking Mid-Execution (`awaiting_input`)  *[deferred, reviewed 2026-04-28 — phantom symbols, no implementation in src/]*

**Problem:** Kodo would silently block or produce low-quality output when it lacked critical information (e.g., which database schema version to target). There was no mechanism to ask the operator a question and resume with the answer.

**Fix:**
- New `<!-- cp:question: ... -->` marker in Kodo stdout/summary: `extract_cp_question()` detects it; `classify_execution_result()` returns `"awaiting_input"`.
- `classify_blocked_issue()` also detects the marker in existing comments.
- `build_improve_triage_result()` handles `awaiting_input`: surfaces the question text in the human-attention comment.
- New `handle_awaiting_input_scan()` (every 8 improve cycles): finds Blocked tasks with this classification, detects human replies posted after the triage comment, injects the answer into the task description, and transitions back to Ready for AI.

**Files:** `entrypoints/worker/main.py` (`extract_cp_question`, `_CP_QUESTION_RE`, `classify_execution_result`, `classify_blocked_issue`, `build_improve_triage_result`, `handle_awaiting_input_scan`, `_AWAITING_INPUT_SCAN_CYCLE_INTERVAL`, watch loop)

---

### S10-3: Reviewer → Goal Re-Run Escalation  *[deferred, reviewed 2026-04-28 — phantom symbols, no implementation in src/]*

**Problem:** When a human reviewer left a comment that Kodo could not address (zero changed files), the system would post "made no changes" indefinitely, burning loop count without progress.

**Fix:** New `_requeue_as_goal()` in `reviewer/main.py`. When zero-change revision attempts reach `REQUEUE_AS_GOAL_ZERO_CHANGE_THRESHOLD` (2), the function:
1. Closes the PR with an explanatory comment.
2. Creates a fresh goal task with the review feedback injected into the goal description.
3. Marks the original task Done and removes the PR review state file.

A warning is shown to the reviewer after each zero-change attempt indicating how many are left before requeue.

**Files:** `entrypoints/reviewer/main.py` (`_requeue_as_goal`, `REQUEUE_AS_GOAL_ZERO_CHANGE_THRESHOLD`, `_process_human_review`)

---

### S10-4: Campaign/Project Tracking  *[deferred, reviewed 2026-04-28 — phantom symbols, no implementation in src/]*

**Problem:** Multi-step plans created by `build_multi_step_plan()` had no aggregate progress view. Operators had to cross-reference three individual Plane tasks to understand where a campaign stood.

**Fix:** New `CampaignStore` in `execution/campaign_store.py`. When `build_multi_step_plan()` creates step tasks, it registers a campaign record with the source task ID, title, and step task IDs. The store tracks `done_step_ids`, `cancelled_step_ids`, `status` (in_progress / partial / completed / cancelled), and progress_pct.

New `entrypoints/campaign_status/main.py` CLI:
```
python -m operations_center.entrypoints.campaign_status.main [--status ...] [--json]
```

**Files:** `execution/campaign_store.py` (NEW), `entrypoints/campaign_status/main.py` (NEW), `entrypoints/worker/main.py` (campaign registration in `build_multi_step_plan`)

---

### S10-5: Calibration Time Decay

**Problem:** `ConfidenceCalibrationStore` accumulated events indefinitely. Old data from a period when a family performed poorly would dilute recent signal, making calibration data stale and misleading.

**Fix:**
- `calibration_for()` and `report()` now accept `window_days=90` (default). Events older than the window are excluded from acceptance-rate calculations.
- New `cleanup_old_events(window_days=90)`: removes events older than the window from disk; returns count removed.
- `_cutoff(window_days)` helper computes the ISO timestamp cutoff.

**Files:** `tuning/calibration.py` (`calibration_for`, `report`, `cleanup_old_events`, `_cutoff`)

---

### S10-6: Task Complexity Estimate at Proposal Time  *[deferred, reviewed 2026-04-28 — phantom symbols, no implementation in src/]*

**Problem:** The proposer would create high-priority proposals involving 10+ files that kodo could not complete in a single run, wasting execution budget.

**Fix:** New `_estimate_task_complexity(proposal)` function in `worker/main.py`. Returns `"low"` / `"medium"` / `"high"` based on the count of distinct file paths in `evidence_lines` + goal text. `build_proposal_candidates()` applies a **complexity gate**: proposals estimated as `"high"` are moved to Backlog with confidence reduced from high → medium. This prevents flooding the board with unexecutable tasks.

Thresholds: ≥8 files → high; 3–7 → medium; <3 → low.

**Files:** `entrypoints/worker/main.py` (`_estimate_task_complexity`, `_COMPLEXITY_FILE_THRESHOLD_HIGH/MEDIUM`, `build_proposal_candidates` complexity gate)

---

### S10-7: Utility Function for Proposal Ranking  *[deferred, reviewed 2026-04-28 — phantom symbols, no implementation in src/]*

**Problem:** Within a cycle, proposals were created in arbitrary iteration order. The highest-value proposal might miss the cycle cap while lower-value ones were created first.

**Fix:** New `_score_proposal_utility(proposal)` function. Score = confidence weight + calibration bonus (0–0.4) + state bonus (0.2 for Ready for AI) − scope penalty (0.05 per file beyond 2, capped at 0.3). `handle_propose_cycle()` sorts proposals by descending utility score before the cycle-cap loop, ensuring the best proposals are always created first.

**Files:** `entrypoints/worker/main.py` (`_score_proposal_utility`, `handle_propose_cycle` sort)

---

### S10-8: Real-Time CI Webhook  *[deferred, reviewed 2026-04-28 — phantom symbols, no implementation in src/]*

**Problem:** OperationsCenter polled for CI status every N seconds. Between CI completing and the next poll, the reviewer watcher was idle even though action could have been taken immediately.

**Fix:** New `entrypoints/ci_webhook/main.py` HTTP server for GitHub `check_run` webhook events:
- Listens on `127.0.0.1:8765` (configurable via env vars).
- Validates `X-Hub-Signature-256` HMAC using `OPERATIONS_CENTER_WEBHOOK_SECRET`.
- On `check_run.completed` with a relevant conclusion: writes a JSON trigger file to `state/ci_webhook_triggers/` (or runs a custom command via `OPERATIONS_CENTER_WEBHOOK_TRIGGER`).
- Trigger files are picked up by the reviewer watcher on the next cycle.

**Files:** `entrypoints/ci_webhook/main.py` (NEW), `entrypoints/ci_webhook/__init__.py` (NEW)

---

### S10-9: Cross-Repo Synthesis Deriver  *[deferred, reviewed 2026-04-28 — phantom symbols, no implementation in src/]*

**Problem:** When lint errors, security vulnerabilities, or architecture drift appeared simultaneously in multiple repos, the system proposed per-repo fix tasks with no visibility into the systemic pattern.

**Fix:** New `CrossRepoSynthesisDeriver` in `insights/derivers/cross_repo_synthesis.py`. On each autonomy-cycle insights run, it reads the latest `repo_insights.json` artifact from every repo in `tools/report/operations_center/insights/`, computes insight-kind overlap, and emits `cross_repo/pattern_detected` for any kind shared by ≥2 repos. The evidence includes `shared_insight_kind`, `repo_count`, and `repos`.

Registered last in `build_insight_service()` so all per-repo derivers have already fired.

**Files:** `insights/derivers/cross_repo_synthesis.py` (NEW), `entrypoints/autonomy_cycle/main.py` (registration)

---

### S10-10: Task Priority Re-Evaluation Scan

**Problem:** Autonomy backlog tasks accumulated indefinitely. A task proposed when lint errors were high would stay in Backlog even after a manual cleanup removed all violations. Conversely, a task for a high-acceptance family would sit at the same priority as one for a low-acceptance family.

**Fix:** New `handle_priority_rescore_scan()` (every 45 improve cycles). For each Backlog task with `source: autonomy` label:
- **Demote** (add `signal_stale` label): when `calibration_for` or `proposal_success_rate` < 0.2 (signal has become unreliable).
- **Promote** (add `priority: high` label): when acceptance rate ≥ 0.7 and the task isn't already high priority.
Each change adds a `[Improve] Priority rescore` comment explaining the reasoning.

**Files:** `entrypoints/worker/main.py` (`handle_priority_rescore_scan`, `_PRIORITY_RESCORE_CYCLE_INTERVAL`, watch loop)

---

## Summary Table — Session 10

| # | Name | Trigger | Where |
|---|------|---------|-------|
| S10-1 | Rejection patterns in prompts | at proposal creation | `build_proposal_description`, `_load_rejection_patterns_for_proposal` |
| S10-2 | awaiting_input question-asking | during execution / improve scan | `classify_execution_result`, `handle_awaiting_input_scan` |
| S10-3 | Reviewer → goal requeue | after repeated zero-change revisions | `_requeue_as_goal`, `_process_human_review` |
| S10-4 | Campaign tracking | on multi-step plan creation | `CampaignStore`, `campaign-status` CLI |
| S10-5 | Calibration time decay | on calibration query + cleanup | `calibration_for(window_days)`, `cleanup_old_events` |
| S10-6 | Complexity gate at proposal time | `build_proposal_candidates` | `_estimate_task_complexity` |
| S10-7 | Utility-ranked proposals | before cycle-cap loop | `_score_proposal_utility`, `handle_propose_cycle` |
| S10-8 | CI webhook receiver | on GitHub check_run event | `entrypoints/ci_webhook/main.py` |
| S10-9 | Cross-repo synthesis | autonomy-cycle insights stage | `CrossRepoSynthesisDeriver` |
| S10-10 | Priority rescore scan | every 45 improve cycles | `handle_priority_rescore_scan` |
