# Autonomy Hardening — 18 Full-Autonomy Gap Improvements

This document describes the 18 improvements implemented across two sessions to close the
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
