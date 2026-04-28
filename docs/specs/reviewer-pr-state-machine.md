# Spec: Reviewer PR State Machine

## Goal

Build `src/operations_center/entrypoints/pr_review_watcher/main.py` — a
long-running watcher that manages open PRs created by the `goal` lane when
`await_review: true` is set for a repo.

The `reviewer.main` entrypoint currently idles (pending this implementation).
This spec replaces that placeholder with the full two-phase state machine
described in `docs/design/lifecycle.md`.

## Definition of Done

- `pr_review_watcher/main.py` accepts `--config`, `--watch`,
  `--poll-interval-seconds`, `--status-dir` (same contract as other watchers)
- State machine per open PR persisted in `state/pr_reviews/<task_id>.json`
- Phase 1 (self-review) implemented and passing tests
- Phase 2 (human review) implemented and passing tests
- `reviewer.main` updated to delegate to the new watcher
- All existing tests green; new tests cover both phases and the timeout path

## Architecture

### State file schema (`state/pr_reviews/<task_id>.json`)

```json
{
  "task_id": "...",
  "pr_number": 42,
  "repo_key": "OperationsCenter",
  "phase": "self_review",
  "self_review_loops": 0,
  "human_review_loops": 0,
  "created_at": "2026-04-27T00:00:00Z",
  "updated_at": "2026-04-27T00:00:00Z"
}
```

### Phase 1 — Self-review

1. Poll GitHub for open PRs in each managed repo that have no verdict file yet
2. For each unclaimed PR: call kodo with the diff against base branch
   (`worker.main` + `execute.main` pipeline, source=`reviewer_self`)
3. kodo writes a verdict to a well-known path in the workspace:
   `verdict.json` → `{"result": "LGTM" | "CONCERNS", "summary": "..."}`
4. `LGTM` → merge PR, transition Plane task to Done
5. `CONCERNS` → post comment with concerns summary, run one revision pass
   (up to `reviewer.max_self_review_loops`, default 2), then re-check
6. Unresolved after max loops → escalate to Phase 2

### Phase 2 — Human review

1. Post escalation comment: `<!-- operations-center:bot -->` + concerns summary
2. Poll PR comments (ignoring `bot_logins` and own `<!-- operations-center:bot -->` markers)
3. Human 👍 reaction or `/lgtm` comment → merge, Done
4. Human comment (not a bot) → run kodo revision pass, post reply when done
   (up to `reviewer.max_human_review_loops`, default 3)
5. Timeout of 86400s (1 day) from phase 2 entry → auto-merge with notice comment

### Invariants

- Never re-processes own `<!-- operations-center:bot -->` comments
- `bot_logins` config list always filtered from comment triggers
- `allowed_reviewer_logins` (optional) restricts human-phase triggers to a whitelist
- PR state transitions happen on GitHub (merge/close), Plane transitions happen
  separately (Done) — the two are not coupled in a single atomic step
- Planning → execution pipeline used identically to `board_worker`
  (`worker.main` → `execute.main` subprocesses)

## Constraints

- Do not modify `worker.main` or `execute.main`
- Do not import from `behavior_calibration`
- State file is the single source of truth; Plane board is updated after
  state file is written, never before
- GitHub token read from `settings.git_token()` — no new secrets

## Out of Scope

- Multi-repo PR fan-out (one PR per task)
- Draft PR handling (treat drafts as unready — skip until converted)
- Reviewer assignment automation
