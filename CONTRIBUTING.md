# Contributing to OperationsCenter

OperationsCenter is the planning, orchestration, and execution boundary for the platform. It proposes work, routes through SwitchBoard, enforces policy, dispatches bounded adapters, and records observability.

## Before You Start

- Check open issues to avoid duplicate work
- For significant changes, open an issue first to discuss the approach
- All contributions must pass the test suite and linter before merging

## Development Setup

```bash
git clone https://github.com/ProtocolWarden/OperationsCenter.git
cd OperationsCenter
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Requires Python 3.11+.

## Running Tests

```bash
.venv/bin/python -m pytest tests/ -v
```

The full suite covers unit, integration, and contract tests. Tests that require a live stack are gated behind a `slow` or `live` marker and skipped by default.

## Running the Linter

```bash
ruff check src/
```

## Project Structure

```
src/operations_center/
  contracts/        # canonical TaskProposal, LaneDecision, ExecutionRequest, ExecutionResult
  planning/         # proposer, insights, decision engine
  routing/          # SwitchBoard client
  execution/        # ExecutionCoordinator, artifact writer
  backends/         # kodo, archon, openclaw, direct_local adapters
  policy/           # pre-execution policy gate
  observability/    # run artifact persistence, usage store
  tuning/           # recommendation-only tuning layer
  upstream_eval/    # evidence-based upstream patch evaluation
  autonomy/         # single-cycle autonomy loop
```

## Architectural Constraints

OperationsCenter is the **single execution boundary**. Contributions must not:

- Move contract definitions out of `contracts/`
- Add execution logic to SwitchBoard or OperatorConsole
- Create alternate execution paths that bypass policy
- Add silent fallbacks that mask failures

The canonical flow is:
```
OperationsCenter → SwitchBoard → Policy gate → Adapter → ExecutionResult
```

## Pull Requests

- Keep PRs focused — one concern per PR
- All new execution paths require tests
- Policy changes require explicit test coverage for the blocked case
- Update `docs/` if the change affects operator-visible behavior

## Commit Style

| Prefix | Use for |
|--------|---------|
| `feat:` | new user-facing feature |
| `fix:` | bug fix |
| `refactor:` | internal restructure, no behavior change |
| `docs:` | documentation only |
| `test:` | test additions or fixes |
| `chore:` | tooling, CI, dependency updates |

## Code of Conduct

This project follows the [Contributor Covenant v2.1](CODE_OF_CONDUCT.md). By participating you agree to uphold its standards.
