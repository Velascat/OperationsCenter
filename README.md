# Control Plane

Self-hosted AI execution wrapper that uses **Plane** as the Jira-like board and **Kodo** as the coding engine.

## Current MVP

- Run one Plane work-item by id.
- Run the next eligible Plane work-item from the configured project.
- Run a polling watch loop for the `goal` worker role.
- Parse structured task body sections: `## Execution`, `## Goal`, optional `## Constraints`.
- Use explicit repo/base branch metadata from the task.
- Create an isolated ephemeral clone and task branch (`plane/<task_id>-<slug>`).
- Prepare a repo-local Python virtualenv in the cloned repo (`.venv` by default).
- Generate the Kodo goal file from Goal/Constraints only.
- Run Kodo, then repo-configured validation commands.
- Enforce `allowed_paths` against changed files before commit/push.
- Set repo-local git identity from config before committing.
- Emit retained artifacts with a `run_id` under `tools/report/kodo_plane/`.
- Update Plane state and add short result comments.

## Not implemented yet

- PR creation.
- Webhook consumer.
- `watch` roles beyond `goal`.
- Concurrency locking.
- Retries/idempotency.
- Multi-repo tasks.
- `watch-all` or worker supervision.

## Quick start

Interactive local setup:

```bash
./scripts/control-plane.sh setup
```

This bootstraps the repo `.venv`, installs the repo in editable mode with dev dependencies, installs/verifies `kodo` if missing, and writes:

- `config/control_plane.local.yaml`
- `.env.control-plane.local`
- `config/plane_task_template.local.md`

Start the local dev stack:

```bash
source .env.control-plane.local
./scripts/control-plane.sh start
./scripts/control-plane.sh plane-status
```

Common commands:

```bash
./scripts/control-plane.sh start
./scripts/control-plane.sh stop
./scripts/control-plane.sh plane-up
./scripts/control-plane.sh plane-down
./scripts/control-plane.sh plane-status
./scripts/control-plane.sh dev-up
./scripts/control-plane.sh dev-down
./scripts/control-plane.sh providers-status
./scripts/control-plane.sh plane-doctor --task-id TASK-123
./scripts/control-plane.sh test
./scripts/control-plane.sh api
./scripts/control-plane.sh run --task-id TASK-123
./scripts/control-plane.sh run-next
./scripts/control-plane.sh watch --role goal
./scripts/control-plane.sh smoke --task-id TASK-123 --comment-only
```

Each helper command writes a local log file under `logs/local/`.

Manual worker equivalent:

```bash
source .env.control-plane.local
.venv/bin/python -m control_plane.entrypoints.worker.main --config config/control_plane.local.yaml --task-id TASK-123
```

## Local workflow

Plane is the board and source of truth. Control Plane is the local operator wrapper.

Typical local flow:

1. Run `./scripts/control-plane.sh setup`.
2. Run `source .env.control-plane.local`.
3. Run `./scripts/control-plane.sh start`.
4. Run `./scripts/control-plane.sh plane-doctor`.
5. Create a work item in the configured Plane project.
6. Move the work item to `Ready for AI`.
7. Execute it with one of:
   - `./scripts/control-plane.sh run --task-id TASK_ID`
   - `./scripts/control-plane.sh run-next`
   - `./scripts/control-plane.sh watch --role goal`

`run-next` and `watch --role goal` only pick tasks in `Ready for AI`.

## Automation model

Board-facing execution modes:

- Manual single-task run: `run --task-id TASK_ID`
- First eligible task: `run-next`
- Background polling loop: `watch --role goal`

Current watch loop behavior:

- polls Plane on an interval
- looks for tasks in `Ready for AI`
- filters by board task kind
- claims a task by moving it to `Running`
- executes one task at a time
- logs each poll cycle to `logs/local/`

Current role support:

- `goal`: implemented
- `test`: not implemented
- `improve`: not implemented

Control Plane operates at the board/task level. Kodo remains the multi-agent executor for a single run.

## Task template

Use a Plane work-item description/body like this:

```text
## Execution
repo: control-plane
base_branch: main
mode: goal
allowed_paths:
  - src/
  - tests/

## Goal
Improve the autonomous Plane watcher and local workflow.

## Constraints
- Keep changes scoped to the wrapper.
- Do not modify unrelated deployment behavior.
```

Notes:

- `mode: goal` is the supported runtime mode today.
- `allowed_paths` is enforced before commit/push.
- Kodo receives Goal/Constraints, not the `## Execution` block.

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

## Plane doctor

Use Plane doctor to verify API access and diagnose workspace/project/token mismatches:

```bash
./scripts/control-plane.sh plane-doctor --task-id TASK-123
```

It reports:

- configured Plane base URL
- configured workspace/project values
- current API user from `/api/v1/users/me/`
- status codes and body previews for project, work-item-list, and optional work-item-detail endpoints

This is the fastest way to verify whether local polling can actually enumerate eligible tasks.

## Demo run recipe

For a safe end-to-end demo:

1. Create a Plane work item with `mode: goal`, a known safe `repo`, a known `base_branch`, and tight `allowed_paths`.
2. Use a low-risk goal that touches only that allowed path set.
3. Move the task to `Ready for AI`.
4. Run `run --task-id TASK_ID`, `run-next`, or `watch --role goal`.
5. Inspect `result_summary.md`, `validation.json`, the worker log, and the Plane comment.

The current autonomous path is a polling loop for the `goal` role only. There is no webhook consumer, concurrency lock manager, or improve/test watcher yet.

## Kodo installation

Setup now manages Kodo installation for the local machine:

- checks whether `kodo` is already on `PATH`
- installs `uv` if missing
- installs Kodo with `uv tool install git+https://github.com/ikamensh/kodo`
- verifies the install with `kodo --help`

If setup cannot install or verify Kodo, it fails clearly.

## Repo-local Python environment

For Python repos, the worker bootstraps a repo-local virtual environment inside the cloned workspace before validation:

- default venv path: `.venv`
- default creation command: `python3 -m venv .venv`
- default install command: `.venv/bin/pip install -e .[dev]`

Validation then runs with `VIRTUAL_ENV` set to that repo-local environment and its `bin` directory prepended to `PATH`.

Per-repo config supports:

- `bootstrap_enabled`
- `python_binary`
- `venv_dir`
- `install_dev_command`

If `install_dev_command` is omitted, the worker defaults to `pip install -e .[dev]`.

## Local setup wizard

The setup wizard is implemented with Typer and is intended for local operator setup rather than production secret management.

### Before running setup

Plane:

1. This repo owns the local Plane dev deployment path under `deployment/plane/`.
2. `./scripts/control-plane.sh plane-up` brings up Plane for local dev.
3. `./scripts/control-plane.sh plane-down` stops it.
4. `./scripts/control-plane.sh plane-status` checks reachability.
5. After Plane is up, sign in via the browser, create a personal access token, and paste it into setup.
6. Setup can verify the configured Plane API workspace/project immediately after token entry.

GitHub:

1. Setup checks for a usable SSH key for GitHub.
2. If no SSH key exists, it generates `~/.ssh/id_ed25519`, starts `ssh-agent`, adds the key, and prints the public key.
3. Add that public key to GitHub at `https://github.com/settings/keys`.
4. Setup pauses, then verifies SSH with `ssh -T git@github.com`.
5. If the current repo uses an HTTPS GitHub remote, setup can switch it to SSH automatically.
6. A GitHub token remains optional and can be left blank if SSH is used for git operations.

Provider CLIs:

1. Setup detects provider CLIs for Claude Code, Codex CLI, Gemini CLI, and Cursor Agent.
2. Setup can install missing Claude, Codex, and Gemini CLIs when possible.
3. Setup then guides interactive auth or API-key/headless guidance per provider.
4. At least one usable provider backend is required before setup finishes successfully.
5. Recheck provider readiness anytime with `./scripts/control-plane.sh providers-status`.

Setup prompts for:

- Plane base URL, Plane API workspace identifier, Plane API project id, and Plane API token
- Git provider, optional HTTPS auth token, bot identity, and GitHub SSH bootstrap
- Kodo binary/orchestration defaults
- Provider detection, install, auth guidance, and preferred-provider selection
- One or more repo entries with clone URL, allowed branches, validation commands, and repo-local `.venv` bootstrap settings
- A default repo key used when generating a starter Plane task template

In normal mode, setup uses safe defaults and reuses saved values when available.

References:

- Claude Code setup: https://code.claude.com/docs/en/setup
- Codex CLI install/auth: https://github.com/openai/codex/blob/main/docs/install.md
- Kodo provider backends: https://raw.githubusercontent.com/ikamensh/kodo/dev/docs/providers.md

## Plane API verification note

- Adapter targets Plane `work-items` endpoints under `/api/v1/workspaces/{workspace}/projects/{project}/work-items/{id}/`.
- Auth header is `X-API-Key`.
- Status transitions resolve the configured state name to a live Plane state id, then patch the work item with that state id.
- Comments use `POST .../comments/` with structured `comment_html`.
- Live local verification is available via `./scripts/control-plane.sh plane-doctor`.
- This repository verifies these contracts via mocked HTTP tests and provides a live smoke-test entrypoint for operator verification.
