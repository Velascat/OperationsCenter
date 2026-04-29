# Backend: aider_local

**Class:** `AiderLocalBackendAdapter`  
**Module:** `operations_center.backends.aider_local`  
**BackendName enum:** `BackendName.AIDER_LOCAL`  

## What it does

Runs [Aider](https://aider.chat/) against a local [Ollama](https://ollama.com/) instance. No cloud API key is required. Execution is CPU-only.

## Invocation

```bash
aider \
  --model ollama/qwen2.5-coder:3b \
  --api-base http://localhost:11434 \
  --yes-always \
  --message-file /tmp/aider_local_<run_id>_<suffix>.txt
```

- **`--yes-always`** — accepts all prompts without pausing (non-interactive)
- **`--message-file`** — goal text written to a temp file to avoid shell escaping issues with long prompts
- A dummy `OPENAI_API_KEY=sk-local-ollama` is injected so Aider does not warn about missing keys

## Settings (`AiderLocalSettings`)

| Field | Default | Description |
|-------|---------|-------------|
| `binary` | `"aider"` | Path to the aider executable |
| `model` | `"ollama/qwen2.5-coder:3b"` | Ollama model name |
| `ollama_base_url` | `"http://localhost:11434"` | Ollama API base URL |
| `timeout_seconds` | `1800` | Max execution time (30 min for CPU) |
| `extra_args` | `[]` | Additional aider CLI flags |

Configure in `config/settings.yaml` under the `aider_local:` key:

```yaml
aider_local:
  model: ollama/qwen2.5-coder:3b
  ollama_base_url: http://localhost:11434
  timeout_seconds: 1800
```

## ExecutionResult

| Field | Value |
|-------|-------|
| `changed_files_source` | `"git_diff"` (via `git diff --name-status HEAD`) |
| `changed_files_confidence` | `1.0` |
| `validation` | `SKIPPED` (no post-execution validation) |
| `branch_pushed` | `False` |

## Failure modes

| Condition | Status | Category |
|-----------|--------|----------|
| `aider` exit code ≠ 0 | `FAILED` | `BACKEND_ERROR` |
| `aider` binary not in PATH | `FAILED` | `BACKEND_ERROR` |
| Execution exceeds `timeout_seconds` | `TIMED_OUT` | `TIMEOUT` |

## Factory registration

The adapter is always registered in `CanonicalBackendRegistry.from_settings()`. No optional argument is required (unlike `archon` or `openclaw`).

## Prerequisites

- `aider` must be installed and accessible in PATH
- Ollama must be running and reachable at `ollama_base_url`
- The target model must be pulled: `ollama pull qwen2.5-coder:3b`

See [WorkStation docs](../../../WorkStation/docs/local_aider_lane.md) for infrastructure setup.
