# Ghost Work Audit

**Definition.** *Ghost work* is any pipeline activity that consumes resources
(LLM tokens, watcher CPU, kodo time, GitHub API quota) but produces no
durably-useful output. The defining symptom: a task records a terminal state
(Done, Blocked, In Review) without a corresponding artifact a human or
downstream consumer would value (merged commit, useful comment, actionable
follow-up).

This audit catalogs every ghost-work pattern observed in the autonomy loop,
classifies each one, and documents the mechanical fix (or notes when one
isn't yet in place).

## Why this matters

Each ghost-work cycle costs ~8–16 minutes of Opus time. Five ghost runs in
a night (the rate we hit before the fixes below) burns roughly an hour of
quota for zero shipped value. Worse, ghost runs are ambiguous: an operator
glancing at the Plane board sees tasks moving and assumes work is happening,
when in fact the loop is spinning.

## Patterns

| ID | Pattern | Symptom | Root cause | Status | Fix |
|----|---------|---------|------------|--------|-----|
| G1 | Workspace-pollution PRs | 25K-LOC PRs that are mostly kodo's own stdout log | `backends/kodo/adapter.py:262` writes capture to workspace; `git add -A` picks it up | **Fixed** | `.gitignore` lists `.operations_center/`, `.codex`, `.coverage`; `WorkspaceManager.prepare` adds the same to `.git/info/exclude` (defense in depth) |
| G2 | Reviewer creates PRs | `pr_review_watcher` self-review produces a PR with the *review prompt* as the title | `WorkspaceManager.finalize` ran for every branch | **Fixed** | `_NO_PUSH_BRANCH_PREFIXES = ("improve/", "review/")` — finalize skips push for analysis-only branches |
| G3 | Improve duplicates goal | Improve task succeeds → spawns goal follow-up with the same title → second PR for the same logical change | `_create_follow_up` rewrote the parent title | **Fixed** | Improve mode now requests structured output; spawns *focused* follow-ups with file scope and rationale, not duplicates of the parent |
| G4 | Kodo wide-scope | 5K-LOC PRs touching unrelated files | No scope guard before commit | **Fixed** | Pre-flight diff cap in `WorkspaceManager.finalize` (50 files / 2000 lines, configurable via `OPS_CENTER_MAX_*`); auto-split recovery spawns N focused chunks |
| G5 | Policy-blocked task burns time | 16-minute kodo run for a task that gets `status=skipped` at policy gate | Policy decision was BLOCK/REVIEW for routine task types | **Fixed** | Trusted-source bypass: tasks labelled `source: autonomy`, `source: spec-campaign`, or `source: board_worker` skip the task-type review gate |
| G6 | "Successful" run with no PR | Plane task → In Review, no actual PR on origin | `branch_pushed=False` was a silent-success path | **Fixed** | `branch_pushed=False + failure_category="scope_too_wide"` now routes through `_handle_failure` (Blocked with reason) |
| G7 | Empty-description tasks | Worker plans on empty goal text, kodo produces nothing | `spec_director` / propose can emit tasks where `_extract_goal` returns just the title | **Fixed (claim-time reject)** | `_claim_next` now skips Ready tasks whose extracted goal text is shorter than `_MIN_GOAL_TEXT_CHARS` (40); they're labelled `blocked-reason: empty_goal` and moved to Blocked so the operator sees them |
| G8 | Stale Running tasks | Worker dies mid-task (OOM, restart, kodo hang); Plane shows Running forever | No timeout-based reclaim | **Fixed (watchdog)** | `entrypoints/watchdog/main.py` already reclaims Running tasks with no progress for `> reclaim_after_seconds`; this audit verified the path is wired and tested |
| G9 | Workspace tempdir leaks | `/tmp/oc-*` accumulates when kodo subprocess outlives the supervisor | Bash supervisor only kills its direct child Python process | **Mitigated** | Already addressed via `start_new_session=True` and `os.killpg` in `KodoAdapter._run_subprocess`; documented here for traceability |
| G10 | Follow-up loops with same scope | Same goal text re-tried until quota exhausted | No retry counter | **Fixed** | `retry-count: N` label on follow-ups; `_create_split_followups` refuses past depth 2 |
| G11 | PR titles are prompt slices | "Review the following pull-request diff for correctness, style, and poten" as a PR title | `_commit_message` was raw `goal_text[:72]` | **Fixed** | Title sanitiser strips `[Tag]`, `**bold**`, `` `code` ``, prompt-shaped prefixes |

## Classification

- **Quota-burning ghosts** (G2, G3, G4, G5, G7): each ran a full kodo cycle for nothing. These are the most expensive class.
- **State-corruption ghosts** (G1, G6, G11): produced output that misled downstream readers (PRs that look real but aren't, "In Review" with no PR).
- **Resource-leak ghosts** (G8, G9): not directly LLM-cost but degrade the system over time.
- **Loop ghosts** (G10): can compound into runaway cost if not capped.

## Encoded check

A scanner enumerates each pattern's signature and reports counts:

```
python -m operations_center.entrypoints.ghost_audit \
    --config config/operations_center.local.yaml \
    --since 24h
```

Output is JSON with one entry per pattern ID, suitable for piping into a
status-pane card or an alerting rule. The scanner is best-effort — it reads
worker logs, the Plane board, and git remotes; missing data degrades to
"unknown" rather than failing.

See `src/operations_center/entrypoints/ghost_audit/main.py` for the
detection rules.

## Adding a new pattern

When a new ghost-work mode is observed:

1. Add a row to the table above with a new `Gn` ID.
2. Update `entrypoints/ghost_audit/main.py` with a detector for that
   pattern (best effort — symptoms are usually log lines or Plane label
   shapes that don't require deep instrumentation).
3. Implement the mechanical fix and link to the commit.
4. Add a regression test under `tests/ghost_work/test_<pattern>.py`.

The goal is that "ghost work" is a *finite* category we can drive to zero,
not an open-ended catch-all. Every observed instance should either match a
listed pattern or motivate a new one.
