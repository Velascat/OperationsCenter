# Plane + Kodo Wrapper Design

## Purpose

Build a self-hosted AI execution wrapper that uses Plane as the Jira-like board and Kodo as the coding engine, with explicit repo and base-branch selection per task.

## Current behavior

1. Worker fetches one Plane **work-item** by id.
2. Parser extracts `Execution` metadata, `Goal`, and optional `Constraints`.
3. Worker validates mode support (current: `goal` mode).
4. Worker resolves repo config and base-branch policy.
5. Worker creates isolated ephemeral clone and task branch.
   - If the branch already exists on remote (retry), it is checked out with tracking and the base branch is merged in. Conflict markers are left in the working tree for kodo to resolve.
6. Worker bootstraps a repo-local Python virtualenv inside the cloned repo when enabled.
7. Worker runs baseline validation before kodo; pre-existing failures are flagged separately.
8. Worker writes `goal.md` from Goal/Constraints (with conflict-resolution preamble if needed).
9. Worker runs kodo and validation commands.
10. Worker enforces `allowed_paths` policy for changed files.
11. Worker commits/pushes only when policy allows.
12. When `await_review: true`: worker creates a PR and writes a state file for the `review` watcher to manage. Task transitions to `In Review`.
13. Worker writes retained artifacts under `tools/report/kodo_plane/<timestamp>_<task_id>_<run_id>/`.
14. Worker posts a short Plane comment and updates status.

## Boundaries (not implemented)

- webhook ingestion
- concurrency locks
- multi-repo orchestration
- distributed scheduling

## Task metadata template

```text
## Execution
repo: MyRepo
base_branch: main
mode: goal
allowed_paths:
  - src/
  - tests/

## Goal
Improve reporting output and ensure policy violations are visible.

## Constraints
- Keep changes inside wrapper service code.
```


## Runtime semantics

- Current operation is **manual-by-task-id** (no scheduler/webhook yet).
- If validation fails but `push_on_validation_failure` is enabled, the branch may still be pushed as **draft output**. This does not indicate run success.
- Changed-file policy evaluation includes tracked modifications, additions, deletions, renames, and untracked files before commit.
- Python validation is intended to run inside a repo-local virtualenv in the cloned workspace, not against host-global Python packages.

## Repo bootstrap

- Default bootstrap path is `.venv` at the cloned repo root.
- Default bootstrap commands are `python3 -m venv .venv`, pip upgrade, and `.venv/bin/pip install -e .[dev]`.
- Repos can override `python_binary`, `venv_dir`, `install_dev_command`, or disable bootstrap with `bootstrap_enabled: false`.

## Smoke verification

- Use `python -m control_plane.entrypoints.smoke.plane --config ... --task-id ... --comment-only` to verify Plane connectivity and parsing without invoking Kodo.
- Smoke runs retain the raw fetched work-item payload in `plane_work_item.json`.
- Live Plane API behavior still needs to be confirmed by the operator against the target deployment and recorded from retained smoke artifacts.
