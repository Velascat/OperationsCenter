# Control Plane

Self-hosted AI execution wrapper that uses **Plane** as the Jira-like board and **Kodo** as the coding engine.

## Current MVP

- Run one Plane work-item by id.
- Run the next eligible Plane work-item from the configured project.
- Run polling watch loops for the `goal`, `test`, and `improve` worker roles.
- Run a local `watch-all` wrapper that launches the three role watchers together.
- Parse structured task body sections: `## Execution`, `## Goal`, optional `## Constraints`.
- Use explicit repo/base branch metadata from the task.
- Create an isolated ephemeral clone and task branch (`plane/<task_id>-<slug>`).
- Prepare a repo-local Python virtualenv in the cloned repo (`.venv` by default).
- Generate the Kodo goal file from Goal/Constraints only.
- Run Kodo, then repo-configured validation commands.
- Enforce `allowed_paths` against changed files before commit/push.
- Set repo-local git identity from config before committing.
- Emit retained artifacts with a `run_id` under `tools/report/kodo_plane/`.
- Update Plane state and add explicit role-identified worker comments.

## Not implemented yet

- PR creation.
- Webhook consumer.
- Concurrency locking.
- Retries/idempotency.
- Multi-repo tasks.
- Distributed locking or multi-machine scheduling.
- Automatic dependency update checking/re-pinning workflow.

## Quick start

Interactive local setup:

```bash
./scripts/control-plane.sh setup
```

This bootstraps the repo `.venv`, installs the repo in editable mode with dev dependencies, installs/verifies `kodo` if missing, and writes:

- `config/control_plane.local.yaml`
- `.env.control-plane.local`
- `config/plane_task_template.local.md`

Fastest happy path:

```bash
./scripts/control-plane.sh setup
source .env.control-plane.local
./scripts/control-plane.sh start
./scripts/control-plane.sh plane-status
./scripts/control-plane.sh plane-doctor
```

Then create a Plane work item, move it to `Ready for AI`, and run one of:

```bash
./scripts/control-plane.sh run --task-id TASK-123
./scripts/control-plane.sh run-next
./scripts/control-plane.sh watch-all
```

Local operation commands:

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
./scripts/control-plane.sh dependency-check
./scripts/control-plane.sh dependency-check --create-plane-tasks
./scripts/control-plane.sh test
./scripts/control-plane.sh api
./scripts/control-plane.sh run --task-id TASK-123
./scripts/control-plane.sh run-next
./scripts/control-plane.sh watch --role goal
./scripts/control-plane.sh watch --role test
./scripts/control-plane.sh watch --role improve
./scripts/control-plane.sh watch-all
./scripts/control-plane.sh watch-all-status
./scripts/control-plane.sh watch-all-stop
./scripts/control-plane.sh janitor
./scripts/control-plane.sh smoke --task-id TASK-123 --comment-only
```

Maintenance commands:

```bash
./scripts/control-plane.sh providers-status
./scripts/control-plane.sh dependency-check
./scripts/control-plane.sh dependency-check --create-plane-tasks
```

Each helper command writes a local log file under `logs/local/`.

Runtime files and retained artifacts:

- command logs: `logs/local/`
- Plane runtime logs: `logs/local/plane-runtime/`
- watcher logs, PID files, and heartbeat status files: `logs/local/watch-all/`
- retained execution artifacts and reports: `tools/report/kodo_plane/`

Retention policy:

- `./scripts/control-plane.sh janitor` prunes local logs and retained artifact directories older than 1 day
- the shell wrapper runs this janitor automatically before commands, using `CONTROL_PLANE_RETENTION_DAYS` if you need to override the default

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
   - `./scripts/control-plane.sh watch-all`

`run-next` and the role watchers only pick tasks in `Ready for AI`, except `improve`, which also triages `Blocked` tasks.

## Automation model

Board-facing execution modes:

- Manual single-task run: `run --task-id TASK_ID`
- First eligible task: `run-next`
- Background polling loops:
  - `watch --role goal`
  - `watch --role test`
  - `watch --role improve`
- Local wrapper for all three:
  - `watch-all`

Current watch loop behavior:

- polls Plane on an interval
- looks for tasks in `Ready for AI`
- filters by board task kind
- claims a task by moving it to `Running`
- executes one task at a time
- logs each poll cycle to `logs/local/`
- writes one heartbeat/status file per watcher under `logs/local/watch-all/`
- uses the board itself for worker coordination through labels, comments, and follow-up tasks

Current role support:

- `goal`: implemented
- `test`: implemented
- `improve`: implemented

Lifecycle contract:

- `goal -> test`
  - when implementation succeeds and explicit verification is required
- `goal -> review`
  - when implementation succeeds and no explicit test handoff is required
- `goal -> blocked -> improve`
  - when implementation fails or is blocked
- `test -> done`
  - when verification succeeds
- `test -> goal`
  - when verification fails and a follow-up implementation task is required
- `blocked -> improve triage`
  - when a blocked task needs classification and a next step

Role responsibilities:

- `goal`
  - consumes `task-kind: goal`
  - runs implementation work
  - ends with an explicit next-step outcome:
    - success with no explicit verification need -> `Review`
    - success with verification required -> creates a `test` follow-up task
    - failure/block -> leaves the task `Blocked` for improve triage
- `test`
  - consumes `task-kind: test`
  - runs verification work
  - ends with an explicit next-step outcome:
    - verification success -> `Done`
    - verification failure -> creates a follow-up `goal` task
- `improve`
  - triages `Blocked` tasks
  - consumes explicit `task-kind: improve` tasks
  - reads task comments and recent retained artifacts
  - creates bounded follow-up tasks instead of leaving blocked work stuck

Blocked-task handling lives inside `improve`, not in a separate `unblocker` lane. Improve is the board-level triage lane for blocked work.

`watch-all` is only a local convenience wrapper. It launches three watcher processes with separate logs and PID files. It is not a queue, scheduler, or distributed supervisor.

Control Plane operates at the board/task level. Kodo remains the multi-agent executor for a single run.

Workers can read each other's board comments. In the current MVP:

- `improve` reads task comments through the Plane API when triaging blocked tasks
- worker-created comments are part of the shared board context for later runs
- coordination is still board-first rather than hidden inter-worker messaging

## Board clarity

Worker comments now identify the acting lane directly in Plane:

- `[Goal] ...`
- `[Test] ...`
- `[Improve] ...`

Each worker comment is intended to show:

- `run_id`
- `task_id`
- `task_kind`
- `worker_role`
- `result_status`
- `follow_up_task_ids`
- `blocked_classification` when relevant
- `handoff_reason` when a task is passed to another lane

Blocked-task triage uses a small fixed vocabulary:

- `infra_tooling`
- `validation_failure`
- `scope_policy`
- `parse_config`
- `unknown`

That same vocabulary is used in board comments, watcher logs, and retained summaries.

## Task lineage

When a worker creates a follow-up task:

- the parent task gets a comment with the child task ids
- the child task body includes:
  - `original_task_id`
  - `original_task_title`
  - `source_worker_role`
  - `source_task_kind`
  - `follow_up_task_kind`
  - `handoff_reason`
- the child task also gets an initial worker comment noting the source task and source worker role

This keeps the work chain readable from the Plane board itself instead of forcing log inspection first.

## Watcher heartbeat

`watch-all-status` now reports more than PID liveness.

For each watcher it shows:

- whether it is running
- current/last cycle
- current state
- last action
- current task id and task kind if any
- follow-up tasks created this session
- blocked tasks triaged this session
- last update timestamp

Example:

```text
watch-goal: running (pid 12345) | cycle=7 state=idle last_action=execute_complete task_id=TASK-123 task_kind=goal followups=1 triaged=0 created=1 updated_at=...
```

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
- Use labels such as `task-kind: goal`, `task-kind: test`, `task-kind: improve`, and `source: manual` when you want explicit board routing.

## Diagnostics

### Plane smoke test

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

### Plane doctor

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
4. Run `run --task-id TASK_ID`, `run-next`, `watch --role <role>`, or `watch-all`.
5. Inspect `result_summary.md`, `validation.json`, the worker log, `watch-all-status`, and the Plane comments.

The current autonomous path is a polling loop with `goal`, `test`, and `improve` roles. In normal board flow:

- `goal` produces implementation outcomes
- `test` resolves verification outcomes
- `improve` triages blocked work and creates bounded follow-ups

There is still no webhook consumer, concurrency lock manager, or distributed supervisor.

## Local tooling

### Kodo installation

Setup now manages Kodo installation for the local machine:

- checks whether `kodo` is already on `PATH`
- installs `uv` if missing
- installs Kodo with `uv tool install git+https://github.com/ikamensh/kodo`
- verifies the install with `kodo --help`

If setup cannot install or verify Kodo, it fails clearly.

Optional version pin:

- setup can record a Kodo git ref/tag/SHA in local env
- if present, install uses:
  - `uv tool install git+https://github.com/ikamensh/kodo@<ref>`

### Version pinning

Local setup supports optional pinning for the operator machine:

- Plane
  - release tag pin for the repo-managed `setup.sh` download
  - optional direct setup URL override
- Kodo
  - git ref/tag/SHA pin for `uv tool install`
- Providers
  - optional version pins for Claude Code, Codex CLI, and Gemini CLI

Pinning is intended to make local installs reproducible.

What pinning does not do:

- it does not automatically check for new upstream versions during normal task execution
- it does not auto-upgrade pinned tools
- it does not auto-repin after breakages are fixed

That maintenance/update-check workflow should be handled by a separate operator command or scheduled maintenance path, not by `setup`, `run`, or `watch`.

### Dependency check

Use the maintenance checker to inspect pinned versions, installed versions, and upstream latest versions without changing any pins:

```bash
./scripts/control-plane.sh dependency-check
```

This command:

- reads current local pins from `.env.control-plane.local`
- checks installed/local health for Plane, Kodo, and provider CLIs
- compares upstream latest where practical
- writes a retained report under `tools/report/kodo_plane/...`

Optional Plane task creation:

```bash
./scripts/control-plane.sh dependency-check --create-plane-tasks
```

When enabled, actionable drift or breakage creates Plane follow-up tasks labeled like:

- `task-kind: improve`
- `source: dependency-check`

`dependency-check` is a maintenance surface. It is intentionally separate from normal `setup`, `run`, and `watch` execution.

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
6. In advanced mode, setup can also record optional version pins for Plane, Kodo, and supported provider CLIs.

Setup prompts for:

- Plane base URL, Plane API workspace identifier, Plane API project id, and Plane API token
- optional version pins for Plane, Kodo, and supported providers in advanced mode
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

## Retained summaries

Retained `result_summary.md` files now align with the board/log vocabulary and include:

- `worker_role`
- `task_kind`
- `run_id`
- `final_status`
- `blocked_classification`
- `follow_up_task_ids`
- execution, validation, policy, and branch-push outcomes

This is intended to make board comments, watcher logs, and retained artifacts describe the same run in the same language.
