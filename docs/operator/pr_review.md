# PR Review Loop Guide

The PR review loop is a two-phase automated review process. This guide covers how it works, what to verify, and how to troubleshoot it.

## Overview

When a task completes with a branch push and `await_review: true` is set for the repo:

```text
[Task executes] -> [Branch pushed] -> [PR opened] -> [Phase 1: Self-review]
                                                              ↓ LGTM
                                                    [Squash-merge + Done]
                                                              ↓ CONCERNS
                                               [Revision pass, re-review, repeat]
                                                              ↓ unresolved
                                                    [Phase 2: Human review]
                                                              ↓ 👍 or timeout
                                                    [Squash-merge + Done]
```

## Phase 1 — Self-Review (Automatic)

The `review` watcher polls tracked PRs every 60 seconds (configurable via `CONTROL_PLANE_WATCH_INTERVAL_REVIEW_SECONDS`).

1. Kodo reads the diff against the base branch.
2. Kodo writes a verdict: `LGTM` or `CONCERNS`.
3. **`LGTM`** → squash-merge, delete branch, task marked Done.
4. **`CONCERNS`** → kodo runs a revision pass on the branch, then re-reviews.
5. This loop repeats up to `max_self_review_loops` times (default: 2).
6. If still unresolved after all loops → escalates to Phase 2.

The self-review verdict is posted as a PR comment with the `<!-- controlplane:bot -->` marker so it is never mistaken for human input.

## Phase 2 — Human Review (Escalated)

1. Watcher posts a comment on the PR explaining what it couldn't resolve automatically.
2. A human can then:
   - **👍 the PR** or **👍 the latest bot comment** → squash-merge + Done.
   - **Post a comment** → kodo runs a revision pass; bot replies when done; repeat up to 3 times.
3. If no action after 1 day → auto-merge (timeout fallback).

## Enabling the Review Loop

In `config/control_plane.local.yaml`:

```yaml
repos:
  MyRepo:
    clone_url: git@github.com:yourorg/myrepo.git
    default_branch: main
    await_review: true
```

No other config is required for basic operation.

## Bot Safety Contract

All bot-posted comments carry the `<!-- controlplane:bot -->` HTML marker. This ensures:

- The bot never responds to its own comments as if they were human review requests.
- The bot never triggers another revision loop from its own output.

**Required config to prevent bot loops:**

```yaml
reviewer:
  bot_logins:
    - controlplane-bot
    - your-github-bot-account
```

Any login in `bot_logins` has its comments ignored by the review watcher, regardless of comment content.

**Optional: restrict human-phase revisions to a whitelist:**

```yaml
reviewer:
  allowed_reviewer_logins:
    - your-github-username
    - trusted-collaborator
```

When set, only comments from listed logins trigger revision passes in Phase 2. All other human comments are ignored.

## Guardrail Checklist

Before enabling `await_review: true` for a new repo, verify:

- [ ] `reviewer.bot_logins` includes every GitHub account the bot posts as.
- [ ] `<!-- controlplane:bot -->` marker is being appended to all bot comments (check any existing PR comment in `state/pr_reviews/`).
- [ ] Branch protection rules on GitHub do not require status checks that the bot cannot satisfy — otherwise auto-merge will be blocked.
- [ ] `max_self_review_loops` is set to a reasonable value (default 2 is conservative; raise to 3 only if the first revision reliably resolves concerns).
- [ ] `CONTROL_PLANE_PR_DRY_RUN=1` is NOT set in production unless you intend to prevent all PR actions.

## Audit Trail

Every PR action is logged in two places:

1. **Plane comment** on the task — transition reason, run_id, outcome.
2. **Retained artifact** under `tools/report/kodo_plane/<task-id>/<run-id>/` — full diff, kodo stdout, summary.json.

PR state files live in `state/pr_reviews/<owner>/<repo>/<pr-number>.json`. These track:

- Current phase (`self_review` or `human_review`)
- Loop count
- Last action taken
- Escalation timestamp

To inspect the state of a specific PR:

```bash
cat state/pr_reviews/<owner>/<repo>/<pr-number>.json | python3 -m json.tool
```

## Backfilling Existing PRs

If the review watcher restarts and misses PRs that were opened while it was down:

```bash
./scripts/control-plane.sh backfill-pr-reviews
```

This scans GitHub for open PRs on all `await_review`-enabled repos and creates missing state files. Run this after any watcher restart.

## Dry-Run Mode

Set `CONTROL_PLANE_PR_DRY_RUN=1` to log all intended PR actions (merge, comment, push) without actually touching GitHub. Use this to:

- Verify the review watcher is polling correctly.
- Test the transition logic after a config change.
- Debug a stuck review state without risk of unintended merges.

```bash
CONTROL_PLANE_PR_DRY_RUN=1 ./scripts/control-plane.sh watch --role review
```

## Troubleshooting

### Review watcher is not picking up a PR

1. Check that `await_review: true` is set for the repo in config.
2. Check that the PR state file exists in `state/pr_reviews/`. If not, run `backfill-pr-reviews`.
3. Check the review watcher log for errors: `logs/local/watch-all/review.log`.

### Self-review loop is stuck

1. Check the loop count in the PR state file (`self_review_loop_count`).
2. If the count is at `max_self_review_loops`, the watcher should have escalated. Check for a Phase 2 comment on the PR.
3. If the PR has no escalation comment, check the review watcher log for errors around the `pr_self_review_escalate` event.

### Bot is responding to its own comments

1. Confirm `reviewer.bot_logins` includes the bot's GitHub login.
2. Confirm the bot comment includes `<!-- controlplane:bot -->`. Check `state/pr_reviews/` and the actual PR comment on GitHub.

### PR was merged manually before the bot reached it

This is safe. The review watcher checks merge status before every action. If it sees the PR is already merged, it transitions the task to Done and cleans up the state file without retrying.

### Human escalation is not triggering a revision

1. Confirm the human's GitHub login is in `allowed_reviewer_logins` if that config key is set. An empty or missing `allowed_reviewer_logins` means all logins are allowed.
2. Confirm the human commented on the PR itself, not on a commit or on the Plane task.
3. Check that the comment does not carry the `<!-- controlplane:bot -->` marker — if it does, the watcher will skip it.

## Test Scenarios

Run these scenarios against a controlled test repo to validate the loop before enabling it in production:

1. **Happy path (LGTM)**: Create a task with a simple goal, let it complete, verify Phase 1 merges the PR automatically.
2. **Revision loop (CONCERNS)**: Make the diff produce a CONCERNS verdict (e.g. remove a test), verify kodo revises and re-reviews.
3. **Human escalation**: Let max_self_review_loops run out, verify Phase 2 escalation comment appears, verify 👍 triggers a merge.
4. **Bot loop prevention**: Post a comment from a bot login listed in `bot_logins`, verify no revision is triggered.
5. **Dry-run**: Set `PR_DRY_RUN=1`, run a full cycle, verify no GitHub writes but full log output.
