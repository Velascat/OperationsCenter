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

Interactive local setup:

```bash
./scripts/control-plane.sh setup
```

This bootstraps `.venv`, installs the repo in editable mode with dev dependencies, and launches a Typer setup wizard that writes:

- `config/control_plane.local.yaml`
- `.env.control-plane.local`

Then use the helper script for common tasks:

```bash
./scripts/control-plane.sh test
./scripts/control-plane.sh api
./scripts/control-plane.sh worker --task-id TASK-123
./scripts/control-plane.sh smoke --task-id TASK-123 --comment-only
```

Manual equivalents remain available. For example, worker:

```bash
source .env.control-plane.local
.venv/bin/python -m control_plane.entrypoints.worker.main --config config/control_plane.local.yaml --task-id TASK-123
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

## Local setup wizard

The setup wizard is implemented with Typer and is intended for local operator setup rather than production secret management.

It prompts for:

- Plane base URL, workspace slug, project id, and Plane API token
- Git provider, GitHub token, and bot identity
- Kodo binary/orchestration defaults
- Preferred provider auth mode for Kodo:
  - `codex_subscription`
  - `claude_subscription`
  - `openai_api_key`
  - `anthropic_api_key`
  - `custom`
- One default repo entry with clone URL, allowed branches, validation commands, and repo-local `.venv` bootstrap settings

For subscription-backed modes, the wizard records the mode and leaves a note in `.env.control-plane.local`; you still need the relevant local provider tooling already installed and logged in on the machine.

## Plane API verification note

- Adapter targets Plane `work-items` endpoints under `/api/v1/workspaces/{workspace}/projects/{project}/work-items/{id}/`.
- Auth header is `X-API-Key`.
- Status transitions use `PATCH` with `{ "state": "<state>" }`.
- Comments use `POST .../comments/` with structured `comment_html`.
- This repository verifies these contracts via mocked HTTP tests and provides a live smoke-test entrypoint for operator verification.
- No live Plane contract record is checked into this repository yet; capture observed response and state/comment behavior from your deployment before production use.
