# Plane + Kodo Wrapper Design

## Purpose

Build a self-hosted AI execution wrapper that uses Plane as the Jira-like board and Kodo as the coding engine, with explicit repo and base-branch selection per task.

## MVP behavior

1. Plane task enters `Ready for AI`.
2. Worker fetches task and parses `## Execution` metadata.
3. Worker resolves repo config and verifies base branch policy.
4. Worker creates isolated ephemeral clone.
5. Worker creates `plane/<task_id>-<slug>` branch.
6. Worker writes goal file and invokes Kodo.
7. Worker runs validation commands.
8. Worker commits and pushes branch based on policy.
9. Worker writes retained artifacts under `tools/report/kodo_plane/`.
10. Worker comments result in Plane and updates status.

## Task metadata template

```text
## Execution
repo: code_youtube_shorts
base_branch: main
mode: goal
allowed_paths:
  - src/workflow/long_form/
  - tools/audit/
validation_profile: default
open_pr: true
```

## Config example

```yaml
plane:
  base_url: http://plane.local
  api_token_env: PLANE_API_TOKEN
  workspace_slug: engineering
  project_id: project-123

git:
  provider: github
  token_env: GITHUB_TOKEN
  open_pr_default: true
  push_on_validation_failure: true

kodo:
  binary: kodo
  team: full
  cycles: 3
  exchanges: 20
  orchestrator: api
  effort: medium
  timeout_seconds: 3600

repos:
  code_youtube_shorts:
    clone_url: git@github.com:you/code_youtube_shorts.git
    default_branch: main
    validation_commands:
      - pytest -m "unit or contract"
      - ruff check .
    allowed_base_branches:
      - main
      - develop
      - feature/*
```
