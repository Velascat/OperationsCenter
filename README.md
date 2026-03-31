# Control Plane

Self-hosted AI execution wrapper that uses **Plane** as the Jira-like board and **Kodo** as the coding engine.

## What this repo provides

- Typed execution contracts (`BoardTask`, `RepoTarget`, `ExecutionRequest`, `ExecutionResult`)
- Plane adapter for task fetch/comment/status update
- Git/workspace orchestration for isolated ephemeral clones
- Kodo CLI adapter
- Validation runner and retained artifact writer
- Worker orchestrator for end-to-end run of one Plane task
- Optional FastAPI entrypoint for health and dry-run task parsing

## Layout

```text
control-plane/
  README.md
  pyproject.toml
  src/
    control_plane/
      config/
      domain/
      application/
      adapters/
        plane/
        git/
        kodo/
        workspace/
        reporting/
      entrypoints/
        api/
        worker/
  docs/
    design/
      plane_kodo_wrapper.md
```

## Quick start

1. Create config file, for example `config/control_plane.yaml`.
2. Export secrets referenced by `*_env` fields.
3. Run worker for a task id:

```bash
PYTHONPATH=src python -m control_plane.entrypoints.worker.main --config config/control_plane.yaml --task-id TASK-123
```

4. Or run API:

```bash
PYTHONPATH=src uvicorn control_plane.entrypoints.api.main:app --reload
```

## Config example

See `docs/design/plane_kodo_wrapper.md` for full details.
