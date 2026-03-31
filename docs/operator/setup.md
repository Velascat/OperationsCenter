# Setup Guide

`./scripts/control-plane.sh setup` is the interactive local operator setup flow.

It prepares:

- local Plane API config
- local repo config
- provider readiness
- Kodo install/verification
- repo target defaults

## Files Written

Setup writes:

- `config/control_plane.local.yaml`
- `.env.control-plane.local`
- `config/plane_task_template.local.md`

## Typical Flow

```bash
./scripts/control-plane.sh setup
source .env.control-plane.local
```

## What Setup Covers

### Plane

- base URL
- workspace identifier
- project id
- API token
- optional live API verification

### Git

- provider
- optional HTTPS token
- bot author identity
- GitHub SSH bootstrap/verification

### Kodo

- install/verify `kodo`
- configure orchestrator defaults
- persist local execution settings

### Providers

- detect Claude Code, Codex CLI, Gemini CLI, Cursor Agent
- install supported missing CLIs when possible
- verify login/auth readiness
- record preferred smart/fast provider choices

### Repo Targets

- clone URL
- default/base branch
- validation commands
- repo-local `.venv` bootstrap behavior

## Kodo Install Behavior

Setup:

- checks whether `kodo` is on `PATH`
- installs `uv` if needed
- installs Kodo if missing
- verifies the install with `kodo --help`

Setup is intended to be idempotent: it does not reinstall Kodo when the current install already works.

## Advanced Mode

Advanced mode also exposes optional version pins for:

- Plane
- Kodo
- supported provider CLIs

Pins are for reproducible local installs. They do not automatically trigger update checks during normal runs.

## Notes

- The setup wizard is for local operator use, not production secret management.
- The local environment is still single-machine and polling-based after setup completes.
- Re-run readiness checks later with `providers-status`, `plane-doctor`, or `dependency-check`.
