# Control Plane

Self-hosted AI execution wrapper that uses **Plane** as the Jira-like board and **Kodo** as the coding engine.

## Current MVP (implemented)

- Run **one Plane work-item by id** via worker CLI.
- Parse structured task body sections: `## Execution`, `## Goal`, optional `## Constraints`.
- Use explicit repo/base branch metadata from the task.
- Create an isolated ephemeral clone and task branch (`plane/<task_id>-<slug>`).
- Generate Kodo goal file from Goal/Constraints only (Execution metadata is excluded).
- Run Kodo, then repo-configured validation commands.
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
