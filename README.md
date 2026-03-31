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
- `config/plane_task_template.local.md`

Then use the helper script for common tasks:

```bash
./scripts/control-plane.sh plane-up
./scripts/control-plane.sh plane-down
./scripts/control-plane.sh plane-status
./scripts/control-plane.sh dev-up
./scripts/control-plane.sh dev-down
./scripts/control-plane.sh providers-status
./scripts/control-plane.sh test
./scripts/control-plane.sh api
./scripts/control-plane.sh worker --task-id TASK-123
./scripts/control-plane.sh smoke --task-id TASK-123 --comment-only
```

Each helper command writes a local log file under `logs/local/`.

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

### Before running setup

Plane:

1. This repo now owns the local Plane dev deployment path under `deployment/plane/`.
2. In the normal setup path, the wizard assumes the repo-managed Plane deployment and can start it immediately.
3. `./scripts/control-plane.sh plane-up` brings up Plane for local dev.
4. `./scripts/control-plane.sh plane-down` stops it.
5. `./scripts/control-plane.sh plane-status` checks reachability.
6. After Plane is up, sign in via the browser, create a personal access token, and paste it into setup.

GitHub:

1. During setup, the wizard now checks for a usable SSH key for GitHub.
2. If no SSH key exists, it generates `~/.ssh/id_ed25519`, starts `ssh-agent`, adds the key, and prints the public key.
3. Add that public key to GitHub at `https://github.com/settings/keys`.
4. The wizard pauses, then verifies SSH with `ssh -T git@github.com`.
5. If the current repo uses an HTTPS GitHub remote, setup can switch it to SSH automatically.
6. A GitHub token remains optional and can be left blank if SSH is used for git operations.

Provider CLIs:

1. Setup now detects provider CLIs for Claude Code, Codex CLI, Gemini CLI, and Cursor Agent.
2. Setup can install missing Claude, Codex, and Gemini CLIs when possible.
3. Setup then guides interactive auth or API-key/headless guidance per provider.
4. At least one usable provider backend is required before setup finishes successfully.
5. Recheck provider readiness anytime with `./scripts/control-plane.sh providers-status`.

It prompts for:

- Plane base URL (default `http://localhost:8080`), workspace slug, project id (default `1`), and Plane API token
- Git provider, optional GitHub token, bot identity, and GitHub SSH bootstrap
- Kodo binary/orchestration defaults
- Provider detection, install, auth guidance, and preferred-provider selection
- One or more repo entries with clone URL, allowed branches, validation commands, and repo-local `.venv` bootstrap settings
- A default repo key used when generating a starter Plane task template

In normal mode, the wizard uses the repo-managed local Plane deployment, default env var names like `PLANE_API_TOKEN` and `GITHUB_TOKEN`, provider-safe defaults, and other safe defaults without asking. Enable `Advanced setup` only if you want to override those values.

For subscription-backed modes, the wizard records the mode and leaves a note in `.env.control-plane.local`; you still need the relevant local provider tooling already installed and logged in on the machine.

References:

- Claude Code setup: https://code.claude.com/docs/en/setup
- Codex CLI install/auth: https://github.com/openai/codex/blob/main/docs/install.md
- Kodo provider backends: https://raw.githubusercontent.com/ikamensh/kodo/dev/docs/providers.md

## Plane API verification note

- Adapter targets Plane `work-items` endpoints under `/api/v1/workspaces/{workspace}/projects/{project}/work-items/{id}/`.
- Auth header is `X-API-Key`.
- Status transitions use `PATCH` with `{ "state": "<state>" }`.
- Comments use `POST .../comments/` with structured `comment_html`.
- This repository verifies these contracts via mocked HTTP tests and provides a live smoke-test entrypoint for operator verification.
- No live Plane contract record is checked into this repository yet; capture observed response and state/comment behavior from your deployment before production use.
