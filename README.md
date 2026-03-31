# Control Plane

Self-hosted AI execution wrapper that uses **Plane** as the Jira-like board and **Kodo** as the coding engine.

## Current MVP (implemented)

- Run **one Plane work-item by id** via worker CLI.
- Parse structured task body sections: `## Execution`, `## Goal`, optional `## Constraints`.
- Use explicit repo/base branch metadata from the task.
- Create an isolated ephemeral clone and task branch (`plane/<task_id>-<slug>`).
- Prepare a repo-local Python virtualenv in the cloned repo (`.venv` by default).
- Generate Kodo goal file from Goal/Constraints only (Execution metadata is excluded).
- Run Kodo, then repo-configured validation commands through the repo-local virtualenv.
- Enforce `allowed_paths` policy against changed files before commit/push.
- Set repo-local git identity from config before committing.
- Emit retained artifacts with a `run_id` under `tools/report/kodo_plane/`.
- Update Plane state and add short result comments.

## Not implemented yet

- PR creation.
- Webhook consumer.
- Polling scheduler.
- Concurrency locking.
- Retries/idempotency.
- Multi-repo tasks.

## Quick start

1. Create config file, for example `config/control_plane.yaml`.
2. Export secrets referenced by `*_env` fields.
3. Run worker for a task id:

```bash
PYTHONPATH=src python -m control_plane.entrypoints.worker.main --config config/control_plane.yaml --task-id TASK-123
```

4. Optional API:

```bash
PYTHONPATH=src uvicorn control_plane.entrypoints.api.main:app --reload
```

## Plane smoke test

Use the smoke entrypoint to verify Plane fetch, parse, comment, and optional state transition behavior without running Kodo:

```bash
PYTHONPATH=src python -m control_plane.entrypoints.smoke.plane \
  --config config/control_plane.yaml \
  --task-id TASK-123 \
  --comment-only
```

This writes retained smoke artifacts under `tools/report/kodo_plane/<timestamp>_<task_id>_<run_id>/`, including:

- `request_context.json`
- `plane_work_item.json`
- `smoke_result.json`

## Demo run recipe

For a safe end-to-end demo:

1. Create a Plane work item with `mode: goal`, a known safe `repo`, a known `base_branch`, and tight `allowed_paths`.
2. Use a low-risk goal that touches only that allowed path set.
3. Run the worker manually by task id.
4. Inspect `result_summary.md`, `validation.json`, and the Plane comment for the run outcome.

The worker remains manual-by-task-id in the current MVP. There is no scheduler or webhook consumer yet.

## Repo-local Python environment

For Python repos, the worker now bootstraps a repo-local virtual environment inside the cloned workspace before validation:

- default venv path: `.venv`
- default creation command: `python3 -m venv .venv`
- default install command: `.venv/bin/pip install -e .[dev]`

Validation then runs with `VIRTUAL_ENV` set to that repo-local environment and its `bin` directory prepended to `PATH`.

Per-repo config supports:

- `bootstrap_enabled`
- `python_binary`
- `venv_dir`
- `install_dev_command`

If `install_dev_command` is omitted, the worker defaults to `pip install -e .[dev]`. Repos without a usable dev extra should override this command or disable bootstrap explicitly.

## Plane API verification note

- Adapter targets Plane `work-items` endpoints under `/api/v1/workspaces/{workspace}/projects/{project}/work-items/{id}/`.
- Auth header is `X-API-Key`.
- Status transitions use `PATCH` with `{ "state": "<state>" }`.
- Comments use `POST .../comments/` with structured `comment_html`.
- This repository verifies these contracts via mocked HTTP tests and provides a live smoke-test entrypoint for operator verification.
- No live Plane contract record is checked into this repository yet; capture observed response and state/comment behavior from your deployment before production use.
