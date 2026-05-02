# Guidelines

_Stable repo policy for Claude. Low-churn. Not a scratchpad or task list._
_Edit when project rules change — not during normal work sessions._

## Branch Policy

- Do not commit directly to `main` or `master`.
- Before making any changes, confirm you are on a feature branch.
- If on a protected branch, stop and ask the operator to create a working branch.

## Session Start

1. Read `.console/.context` — your compiled startup context for this session.
2. Summarize your plan before making any edits.
3. Confirm you are on the correct branch.

## During Work

- Run `console test` before and after changes.
- Use `console audit` for linting and static checks.
- Use `console status` to check repo and session state.
- Prefer small, targeted edits over large rewrites.

## After Meaningful Progress

- Update `.console/backlog.md` to reflect completed and remaining work.
- Update `.console/log.md` with decisions made and rationale.
- Summarize what changed and what's next.

## What Not to Do

- Do not run destructive commands (`rm -rf`, `git reset --hard`) without explicit operator confirmation.
- Do not push to remote branches without the operator's explicit request.
- Do not modify files outside the repo root without explicit justification.
- Do not edit `.console/.context` directly — it is regenerated at each launch.
