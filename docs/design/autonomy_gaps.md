# Autonomy Hardening — 36 Full-Autonomy Gap Improvements

This document describes the 36 improvements implemented across four sessions to close the
gaps toward fully autonomous operation. They are grouped by session, then by theme.

---

## Session 1 — 8 Proposal and Execution Improvements

### 1. Goal Coherence — `focus_areas` config

**Problem:** The proposer would create tasks about anything it found in the codebase, spreading
kodo's attention across many topics before finishing what mattered.

**Fix:** `focus_areas: [...]` in `config/control_plane.local.yaml` accepts a list of keywords.
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

### 2. Dependency Ordering — `depends_on:` parsing

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

### 3. Task Sizing Gate — split oversized findings

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

### 5. Self-Modification Controls

**Problem:** The system could propose and automatically execute changes to its own codebase
(ControlPlane itself), bypassing the human review that self-modification requires.

**Fix:** `self_repo_key: ControlPlane` in config identifies the installation's own repo.
- Proposals for self-repo tasks are always capped at Backlog.
- `select_watch_candidate` skips self-repo tasks unless they carry a `self-modify: approved` label.

**Config:**
```yaml
self_repo_key: ControlPlane
```

**Files:** `settings.py` (`Settings.self_repo_key`), `main.py` (`_is_self_repo`, `_self_modify_approved`, `select_watch_candidate`, `build_proposal_candidates`)

---

### 7. Better Failure Attribution

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

### 3. Watcher Heartbeat Monitoring

**Problem:** If a watcher process died silently, the autonomous loop stopped with no alert.

**Fix:** `write_heartbeat(status_dir, role, now)` writes `heartbeat_<role>.json` at the start of
every cycle. The `heartbeat-check` CLI subcommand reads all heartbeat files and exits non-zero
if any is older than 5 minutes.

```bash
python -m control_plane.entrypoints.worker.main heartbeat-check --log-dir logs/local/watch-all
```

Wire this into cron or a process monitor for unattended operation.

**Files:** `main.py` (`write_heartbeat`, `check_heartbeats`, `_HEARTBEAT_MAX_AGE_SECONDS`, `main()` subcommand)

---

### 4. Context Handoff for `context_limit` Tasks

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

### 7. Token/Credential Expiry Detection

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

### 9. Scheduled Tasks via Cron Expressions

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
    repo_key: ControlPlane
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

### 2. Pre-Execution Task Validation

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

### 4. Workspace Health Monitoring

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

### 5. Config Schema Drift Detection

**Problem:** No mechanism existed to detect when a deployed `config/control_plane.local.yaml`
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
python -m control_plane.entrypoints.worker.main spend-report --window-days 7
```

**Config:**
```yaml
cost_per_execution_usd: 0.15   # operator estimate; 0.0 disables recording
```

**Files:** `settings.py` (`Settings.cost_per_execution_usd`), `usage_store.py` (`record_execution_cost`, `get_spend_report`), `main.py` (`spend-report` subcommand, cost recording in `handle_goal_task` and `handle_test_task`)

---

### 7. Parallel Execution Within Lanes

**Problem:** Each watcher lane handled one task at a time. With 10 lint_fix tasks ready,
they executed one per poll cycle with sleeping intervals between them. This was the primary
throughput bottleneck when heading toward higher-autonomy Phase 7 operation.

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
./scripts/control-plane.sh watch --role goal --parallel-slots 3
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

### S5-3. Per-Task-Kind Running TTL in Reconcile

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

### S5-6. Task Urgency Scoring

**Problem:** `select_watch_candidate` sorted Ready-for-AI tasks only by priority label (high/medium/low/unset). A post-merge regression fix and a background lint cleanup in the same priority tier were picked in arbitrary board order. No mechanism existed to prefer time-sensitive work over maintenance work.

**Fix:** `issue_urgency_score(issue)` computes a composite integer score combining:
- **Priority label weight** — high=30, medium=20, low=10, unset=15
- **Title prefix boost** — `[Regression]`/`post-merge regression`=+25, `[Fix]`/`[Rebase]`=+15, `[Revise]`/`[Verify]`=+8, `[Workspace]`/`[Step 1`=+5
- **Task age** — capped at +3 (one point per day in Ready state, max 3 days)

`select_watch_candidate` now sorts by `issue_urgency_score(issue)` descending instead of `issue_priority` ascending.

**Files:** `entrypoints/worker/main.py` (`issue_urgency_score`, `select_watch_candidate`)

---

### S5-7. Board Saturation Backpressure

**Problem:** The satiation signal (S1-8) detected when proposals weren't being consumed over multiple cycles, but couldn't directly observe the current board queue depth. A burst `autonomy-cycle --execute` run could flood the board with 30+ tasks when the watcher queue was already backed up, far exceeding what the goal/test lanes could drain in a reasonable time.

**Fix:**
- `MAX_QUEUED_AUTONOMY_TASKS = 15` constant in `main.py` (configurable via `CONTROL_PLANE_MAX_QUEUED_AUTONOMY_TASKS` env var)
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

### S4-1. Watcher Auto-Restart

**Problem:** `watch-all` launched five watchers via `setsid` with no supervisor. A crash (OOM, unhandled exception) left the process dead until the operator manually ran `watch-all` again. Heartbeat detection surfaced the failure but nothing healed it.

**Fix:** `start_watch_role` now wraps each python process in a bash restart loop. On non-zero exit (crash) it logs a `watcher_restart` event and relaunches after 5 seconds. A SIGTERM trap ensures that `stop_watch_role` terminates both the wrapper bash and the running python child cleanly. Exit code 0 (intentional stop — e.g. credential failure at startup) breaks the loop.

**Files:** `scripts/control-plane.sh` (`start_watch_role`)

---

### S4-2. Task Staleness Invalidation

**Problem:** Autonomy-proposed tasks could sit in Backlog for weeks after the underlying signal was resolved (lint fixed by a PR, type errors removed by a refactor). On execution, Kodo would find nothing to do and the task would fail as a no-op, wasting budget.

**Fix:** `handle_stale_autonomy_task_scan` runs every 30 improve cycles. It scans Backlog tasks with a `source: autonomy` label older than `_STALE_AUTONOMY_TASK_DAYS = 21` days and cancels them with a `_STALE_AUTONOMY_CANCEL_MARKER` comment. The marker prevents the feedback loop from treating these as human rejections. If the signal reappears, the proposer will recreate the task.

**Files:** `main.py` (`handle_stale_autonomy_task_scan`, `_STALE_AUTONOMY_CANCEL_MARKER`, `_STALE_AUTONOMY_TASK_DAYS`, `_STALE_AUTONOMY_SCAN_CYCLE_INTERVAL`)

---

### S4-3. Success-Rate Circuit Breaker

**Problem:** The execution budget was count-based only. When something systemic broke (bad kodo version, auth regression), the system would burn the full hourly or daily budget executing tasks that all failed — providing no value and consuming all capacity before the operator could investigate.

**Fix:** `record_execution_outcome(*, task_id, role, succeeded, now)` is called after each `handle_goal_task` and `handle_test_task` completes. `budget_decision` checks the last `_CB_WINDOW = 5` execution outcomes. If `≥ _CB_THRESHOLD = 0.8` (80%) of them failed, the budget decision returns `reason="circuit_breaker_open"` and no further tasks run until the rate improves or the operator investigates. Both thresholds are tunable via `CONTROL_PLANE_CIRCUIT_BREAKER_THRESHOLD` and `CONTROL_PLANE_CIRCUIT_BREAKER_WINDOW` env vars.

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

### S4-6. Connection Error Exponential Backoff

**Problem:** Transient network errors (Plane API down, DNS failure, connection refused) hit the bare `except Exception` handler in `run_watch_loop`, which logged `watch_error` and immediately re-slept for `poll_interval_seconds`. During a 10-minute Plane outage, the watcher would log an error every 30–60 seconds — 10–20 noise events before recovery.

**Fix:** `run_watch_loop` tracks `_consecutive_errors`. Each non-429 exception increments it. The backoff is `min(poll_interval * 2^(n-1), 300)` seconds — so errors 1, 2, 3, 4+ sleep for 30s, 60s, 120s, 300s with a 5-minute cap. The counter resets to 0 on any successful cycle (`else:` clause on the `try` block).

**Files:** `main.py` (`_consecutive_errors` in `run_watch_loop`, exponential backoff in `except Exception`)

---

### S4-7. Human Rejection Signal Capture

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
