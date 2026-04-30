# Operations Center

Local planning, execution, policy, and evidence service for the coding platform. OperationsCenter turns work context into canonical proposals, routes them through SwitchBoard, executes routed work through bounded adapters, and retains evidence around what happened later.

## Primary Operator Model

OperationsCenter is operated through a **planning -> routing -> execution** flow:

1. Gather or derive work intent.
2. Build a canonical `TaskProposal`.
3. Route it through SwitchBoard to get a `LaneDecision`.
4. Hand the proposal/decision bundle to OperationsCenter's canonical execution boundary.
5. `ExecutionCoordinator` builds `ExecutionRequest`, runs the mandatory policy gate, invokes the selected adapter, and records observability evidence.

For a full reproducible walkthrough see **[docs/demo.md](docs/demo.md)**. Run it as a validation ritual after any significant config or threshold change.

---

## Overview

- **Plane** is the board and source of truth for tasks, states, comments, and labels.
- **OperationsCenter** is the planning, execution, policy, and evidence layer.
- **Execution backends** live behind canonical adapters owned by OperationsCenter's execution boundary.
- **goal**, **test**, **improve**, **propose**, and **review** are the board-facing worker lanes.
- The system is **local-first**, **single-machine**, and **polling-based** today.

## System Boundaries

- Local-first and single-machine today.
- Plane remains the board and source of truth for work state.
- Repo-aware autonomy is artifact-driven and bounded by guardrails.
- The proposer lane remains the board-facing guarded adapter.
- Expensive execution is budget-aware and suppresses unnecessary runs.
- Repo-aware autonomy stages remain explicit and inspectable for now.

## What OperationsCenter Is Not

- **Not the lane selector.** OperationsCenter may supply lane hints, but SwitchBoard
  makes the final lane selection. OperationsCenter does not know which execution lane
  will run a task.

- **Not the workflow harness.** Multi-step workflow structure (plan → implement →
  validate → PR) is Archon's responsibility. OperationsCenter drives the autonomy loop;
  it does not own execution step sequencing.

- **Not the infrastructure layer.** OperationsCenter does not deploy or manage services.
  WorkStation owns the Plane stack, SwitchBoard container, and local model deployment.

- **Not the operator shell.** OpenClaw (optional) provides the human-facing operator
  experience. OperationsCenter is the autonomous engine that runs beneath it.

## Canonical Contracts and Planning Pipeline (Phase 3–6)

OperationsCenter integrates with a canonical cross-repo contract layer that
makes task proposals and routing decisions backend-agnostic.

### Planning pipeline

```
PlanningContext  →  TaskProposal  →  LaneDecision  →  ProposalDecisionBundle
   (internal)        (canonical)       (SwitchBoard)       (execution ready)
```

1. **`PlanningContext`** — frozen dataclass with raw task intent (goal text,
   task type, repo coordinates, risk level, labels).
2. **`build_proposal(ctx)`** — pure function in `planning/proposal_builder.py`;
   translates context into a canonical `TaskProposal` with no backend knowledge.
3. **`PlanningService.plan(ctx)`** — calls `build_proposal`, then routes through
   `LaneRoutingClient` (SwitchBoard) to get a `LaneDecision`, then bundles both
   into a `ProposalDecisionBundle`.

```python
from operations_center.planning.models import PlanningContext
from operations_center.routing.service import PlanningService

service = PlanningService.default()
bundle = service.plan(PlanningContext(
    goal_text="Fix all ruff lint errors in src/",
    task_type="lint_fix",
    repo_key="api-service",
    clone_url="https://github.com/org/api-service.git",
    risk_level="low",
))
# bundle.proposal  → TaskProposal
# bundle.decision  → LaneDecision (from SwitchBoard)
# bundle.run_summary → "proposal=... task=... lane=aider_local backend=direct_local rule=..."
```

For full architecture and examples see:
- `WorkStation/docs/architecture/operations-center-routing.md`
- `WorkStation/docs/architecture/operations-center-routing-examples.md`

### Canonical contract types

All cross-repo contracts live in `src/operations_center/contracts/`:

| Module | Types |
|---|---|
| `enums.py` | `TaskType`, `LaneName`, `BackendName`, `ExecutionMode`, `Priority`, `RiskLevel` |
| `proposal.py` | `TaskProposal` |
| `routing.py` | `LaneDecision` |
| `execution.py` | `ExecutionRequest`, `ExecutionResult`, `ExecutionArtifact`, `RunTelemetry` |
| `common.py` | `TaskTarget`, `ExecutionConstraints`, `ValidationProfile`, `BranchPolicy` |

See `WorkStation/docs/architecture/contracts.md` for full documentation.

### Backend adapters (inside OperationsCenter's execution boundary)

The supported live execution path is:

```text
TaskProposal -> LaneDecision -> ExecutionRequest -> adapter -> ExecutionResult
```

`ExecutionCoordinator` is the supported execution boundary. It builds the canonical
`ExecutionRequest`, evaluates policy before execution, invokes the selected adapter,
and records the resulting `ExecutionRecord` / `ExecutionTrace`.

When an adapter supports `execute_and_capture()`, the coordinator also retains
backend-native raw detail by reference in `record.backend_detail_refs`. Changed-file
status in the retained record remains source-aware: inferred or unknown file lists
are not silently upgraded to authoritative.

```python
from pathlib import Path

from operations_center.backends.factory import CanonicalBackendRegistry
from operations_center.config.settings import load_settings
from operations_center.execution.coordinator import ExecutionCoordinator
from operations_center.execution.handoff import ExecutionRuntimeContext

settings = load_settings("config/operations_center.local.yaml")
registry = CanonicalBackendRegistry.from_settings(settings)
coordinator = ExecutionCoordinator(adapter_registry=registry)

outcome = coordinator.execute(
    bundle,
    ExecutionRuntimeContext(
        workspace_path=Path("/tmp/ws"),
        task_branch="auto/task-123",
    ),
)
# outcome.request         -> canonical ExecutionRequest
# outcome.policy_decision -> mandatory gate result
# outcome.result          -> canonical ExecutionResult
# outcome.record/trace    -> retained observability view
```

`KodoBackendAdapter` wraps the kodo subprocess behind the canonical interface:

```python
from operations_center.backends.kodo import KodoBackendAdapter
result = KodoBackendAdapter.from_settings().execute(request)  # ExecutionRequest → ExecutionResult
```

These adapters are OperationsCenter-owned integration modules reached through the
canonical execution boundary rather than legacy worker runtime code.

### Archon Backend Adapter (Phase 8, optional)

`ArchonBackendAdapter` is an optional, bounded second backend for workflow-oriented execution. Archon runs multi-step agentic workflows (plan → execute → validate) internally. It follows the same canonical interface as kodo but stays completely separate from it:

```python
from operations_center.backends.archon.adapter import ArchonBackendAdapter

# Use the stub factory in tests
adapter = ArchonBackendAdapter.with_stub(outcome="success", output_text="done")
result = adapter.execute(request)  # ExecutionRequest → ExecutionResult

# Or capture raw workflow events for observability
result, capture = adapter.execute_and_capture(request)
# capture.workflow_events — Archon-internal step trace, NOT in ExecutionResult
```

Key design constraints:
- Archon is **not** a universal backend — `aider_local` lane runs stay on kodo
- Archon-native types (`ArchonWorkflowConfig`, `ArchonRunCapture`, `workflow_events`) are confined to `backends/archon/`
- `execute_and_capture()` exposes raw workflow events for `BackendDetailRef` retention without inlining them into canonical contracts
- Unsupported requests return `UNSUPPORTED_REQUEST` before invocation; the capture is `None`

See `WorkStation/docs/architecture/archon-adapter.md` for architecture and usage.

### OpenClaw Backend Adapter (Phase 11, optional)

`OpenClawBackendAdapter` makes OpenClaw available as an execution backend behind the canonical contracts. This is the **backend adapter role** — it is separate from the optional outer-shell role (Phase 10).

```python
from operations_center.backends.openclaw import OpenClawBackendAdapter

adapter = OpenClawBackendAdapter.with_stub(outcome="success")
result = adapter.execute(request)  # ExecutionRequest → ExecutionResult
result, capture = adapter.execute_and_capture(request)
# capture.events → raw OpenClaw events (NOT in ExecutionResult)
# capture.changed_files_source → "git_diff" | "event_stream" | "unknown"
```

**Changed-file evidence** is explicit — three possible states:
- `"git_diff"` — authoritative, from git diff on workspace
- `"event_stream"` — inferred, from OpenClaw-reported files in event stream
- `"unknown"` — no reliable source; `changed_files` will be empty

The adapter never presents inferred or unknown changed files as authoritative.

Subclass `OpenClawRunner` to provide a real implementation; `StubOpenClawRunner` and `OpenClawBackendAdapter.with_stub()` are available for tests and local dev.

See `WorkStation/docs/architecture/openclaw-backend-adapter.md` for architecture and examples.

### OpenClaw Outer Shell (Phase 10, optional)

`OpenClawBridge` is an optional outer-shell integration layer. It provides an operator-facing surface for triggering runs, deriving status summaries, and inspecting retained records — without replacing or modifying the internal architecture.

**The system runs fully without OpenClaw active.** The shell is enabled by setting `OPENCLAW_SHELL_ENABLED=1`.

```python
from operations_center.openclaw_shell.bridge import OpenClawBridge
from operations_center.openclaw_shell.models import OperatorContext

if OpenClawBridge.is_enabled():
    bridge = OpenClawBridge.default()
    ctx = OperatorContext(
        goal_text="Fix all lint errors",
        repo_key="svc",
        clone_url="https://github.com/example/svc.git",
    )
    handle = bridge.trigger(ctx)
    # handle.selected_lane, handle.selected_backend, handle.status == "planned"
    summary = bridge.status_from_result(result, lane="claude_cli", backend="kodo")
    inspection = bridge.inspect_from_record(record, trace)
```

Key design rules:
- Nothing inside the core architecture (`contracts/`, `planning/`, `routing/`, `backends/`, `observability/`) imports from `openclaw_shell/`
- Shell output types (`ShellRunHandle`, `ShellStatusSummary`, `ShellInspectionResult`) are derived from canonical internal data only — never from OpenClaw-native event streams
- `wrap_action()` catches exceptions at the shell boundary so failures don't propagate outward

See `WorkStation/docs/architecture/openclaw-outer-shell.md` for architecture and examples.

### Execution Observability (Phase 7)

`ExecutionObservabilityService` normalizes execution outcomes into retained records:

```python
from operations_center.observability.service import ExecutionObservabilityService

svc = ExecutionObservabilityService.default()
record, trace = svc.observe(result, backend="kodo", lane="claude_cli")
# record → ExecutionRecord (retained; classified artifacts, changed-file evidence, backend detail refs)
# trace  → ExecutionTrace (inspectable; headline, summary, warnings, key artifacts)
```

Key model distinctions:

| Model | Purpose |
|---|---|
| `ExecutionResult` | Canonical outcome contract (unchanged) |
| `ExecutionRecord` | Retained normalized record; wraps result + observability metadata |
| `ExecutionTrace` | Inspectable report; generated from record on demand |
| `BackendDetailRef` | Reference to raw backend output; kept separate from canonical data |
| `ChangedFilesEvidence` | Changed-file knowledge with honest uncertainty (KNOWN/NONE/UNKNOWN/NOT_APPLICABLE) |

See `WorkStation/docs/architecture/execution-observability.md` for architecture and examples.

### Policy and Guardrails (Phase 12)

`PolicyEngine` evaluates a `TaskProposal` + `LaneDecision` against configured guardrails and returns an inspectable `PolicyDecision`. Policy is a first-class system concern — it is not hidden inside a backend or shell.

```python
from operations_center.policy import PolicyEngine, explain

engine = PolicyEngine.from_defaults()
decision = engine.evaluate(proposal, lane_decision)

if decision.is_blocked:
    e = explain(decision)
    # e.summary → "BLOCKED: Path '.ssh/id_rsa' is blocked by policy rule"
elif decision.requires_review:
    # gate on human approval
elif decision.is_allowed:
    # proceed (check decision.warnings if relevant)
```

In the supported runtime, policy is evaluated after `ExecutionRequest` construction
and before any adapter invocation. `BLOCK` and `REQUIRE_REVIEW` stop autonomous
execution and are retained as inspectable blocked-execution records.

**PolicyStatus values:**
- `ALLOW` — no restrictions
- `ALLOW_WITH_WARNINGS` — warnings present (e.g. missing branch prefix)
- `REQUIRE_REVIEW` — human review gate (high-risk, sensitive path, task type)
- `BLOCK` — hard stop (disabled repo, blocked path/tool, routing violation)

**Default posture (conservative):**

| Dimension | Default |
|-----------|---------|
| Branch | No direct commit; branch required |
| Destructive actions | Blocked |
| SSH / GPG key paths | Blocked |
| High-risk tasks | Review required + strict validation enforced |
| Feature/refactor | Review required |
| Env / migration / workflow files | Review required |

Config validation: `validate_config(config)` returns a list of error strings for contradictory configs before evaluation.

See `WorkStation/docs/architecture/policy-guardrails.md` for full architecture and `policy-guardrails-examples.md` for usage examples.

### Evidence-Driven Routing Strategy Tuning (Phase 13)

`StrategyTuningService` analyzes retained `ExecutionRecord` evidence and produces routing/backend strategy comparisons, findings, and reviewable proposals.

```python
from operations_center.tuning import StrategyTuningService

service = StrategyTuningService.default()
report = service.analyze(records)  # list[ExecutionRecord] → StrategyAnalysisReport

# report.comparison_summaries → one BackendComparisonSummary per (lane, backend)
# report.findings             → bounded observations (reliability, change_evidence, etc.)
# report.recommendations      → candidate changes (ALL require human review)
# report.limitations          → honest statements about data gaps
```

**Design rules:**

- Recommendations are proposals, not policy mutations — `requires_review=True` always
- Supported runtime is recommendation-only. Requested auto-apply actions are retained as skipped review items rather than mutating live config.
- Sparse evidence (< 8 samples) produces only `sparse_data` findings and no recommendations
- Contradictory signals (high success + poor change evidence) are surfaced explicitly
- `StrategyAnalysisReport` is frozen and does not modify SwitchBoard routing policy

**Three things always kept separate:**
1. Current active routing policy (SwitchBoard)
2. Observed historical evidence (retained ExecutionRecords)
3. Proposed strategy changes (tuning proposals, subject to review)

See [docs/architecture/routing-tuning.md](docs/architecture/routing-tuning.md) for architecture and [docs/architecture/routing-tuning-examples.md](docs/architecture/routing-tuning-examples.md) for examples.

### Upstream Patch Evaluation (Phase 14)

`UpstreamPatchEvaluator` evaluates whether recurring friction around external systems like `openclaw`, `archon`, or `kodo` actually justifies upstream patching or deeper native integration work.

```python
from operations_center.upstream_eval import UpstreamPatchEvaluator

evaluator = UpstreamPatchEvaluator.default()
report = evaluator.analyze(evidence)

# report.friction_findings        → recurring, evidence-labeled integration issues
# report.workaround_assessments   → adapter-first workaround cost/reliability summaries
# report.recommendations          → review-only upstream patch proposals
```

Design rules:

- Upstream modifications are evaluated late and from retained evidence
- Adapter-first remains the default posture
- Patch proposals are reviewable recommendations, not silent commitments
- Maintenance burden and divergence risk are part of every serious recommendation

See [docs/architecture/upstream-patch-evaluation.md](docs/architecture/upstream-patch-evaluation.md) and [docs/architecture/upstream-patch-evaluation-examples.md](docs/architecture/upstream-patch-evaluation-examples.md).

---

## Repo-Aware Autonomy Loop

```text
observe -> analyze -> decide -> propose
```

- `observe` writes retained repo-state snapshots.
- `analyze` derives normalized insights from those snapshots.
- `decide` emits guarded proposal candidates plus suppression records.
- `propose` routes approved candidates into the existing board-facing proposer lane, which applies final protections before creating bounded Plane tasks.

## Repo-Aware Autonomy Flow

```text
[Repo]
  ↓
[Observer] -> repo_state_snapshot.json
  ↓
[Insight Engine] -> repo_insights.json
  ↓
[Decision Engine] -> proposal_candidates.json
  ↓
[Board-Facing Proposer Lane] -> proposal_results.json -> Plane tasks
```

## Autonomy Execution Model

The repo-aware autonomy loop can be run stage-by-stage or as a single chained command.

**Stage commands (explicit, inspectable):**
- `observe-repo` writes a retained repo-state snapshot.
- `generate-insights` reads retained snapshots and emits normalized insights.
- `decide-proposals` reads retained insights and emits guarded proposal candidates.
- `propose-from-candidates` routes emitted candidates through existing proposer protections and may create bounded Plane tasks.

**Chained command (dry-run first):**
- `autonomy-cycle` runs all four stages in sequence. Dry-run by default — shows what would be proposed without creating tasks. Pass `--execute` to create real Plane tasks. Pass `--all-families` to enable all twelve candidate families (seven default + five gated).

**Threshold tuning:**
- `analyze-artifacts` reads retained decision + proposer artifacts, computes per-family emit/suppress/create rates, and prints recommendations when suppression is too high or emitted candidates never reach the board.

**Self-tuning regulation (bounded):**
- `tune-autonomy` runs a bounded self-tuning regulation loop: reads retained decision and proposer artifacts, computes per-family behavior metrics, and emits conservative threshold recommendations. Recommendation-only by default. Optional `--apply` mode (requires `OPERATIONS_CENTER_TUNING_AUTO_APPLY_ENABLED=1`) applies small bounded changes to `config/autonomy_tuning.json` with full cooldowns, quotas, and audit trail. The `DecisionEngineService` reads the tuning config at startup if it exists.

**Execution health (automatic):**
- Every `autonomy-cycle` run also reads retained execution artifacts and checks whether the system is generating useful work. If no-op rate is high or validation keeps failing, a bounded improve task is proposed automatically. No separate command needed.

## `Propose` Means Two Related Things

- In the repo-aware autonomy loop, `propose` is the stage that routes approved candidates toward task creation.
- In the worker model, the proposer lane is the board-facing path that applies final guardrails and writes Plane tasks.

The naming is intentionally close because the second is the board adapter for the first, but they are not the same boundary.

## What Works Today

### Core Workflow

- Single-task execution from a Plane work item.
- Background watchers for `goal`, `test`, `improve`, `propose`, and `review`.
- `watch-all` to launch all five local watcher lanes together.
- Structured task parsing from `## Execution`, `## Goal`, and optional `## Constraints`.
- Isolated ephemeral clone + task branch workflow.
- Repo-local bootstrap and validation execution.
- Worker comments, retained artifacts, and local heartbeat/status files.

### Repo-Aware Autonomy

- Read-only repo observer snapshots with fifteen signal collectors: git context, recent commits, file hotspots, test signal, dependency drift, TODOs, execution health, lint violations (`ruff`), type errors (`ty`/`mypy`), CI check history (GitHub API), per-task validation failure patterns, static architecture coupling (AST), benchmark regression (reads retained benchmark output), security advisories (reads pip-audit/npm audit/trivy JSON), and test coverage gaps (reads coverage.xml/HTML/text reports).
- Read-only normalized insight generation from retained observer snapshots across twenty-two derivers (including architecture drift, benchmark regression, security vuln, execution outcome, quality trend, no-op loop, coverage gap, and theme aggregation derivers).
- Cross-signal correlation: lint and type violations are checked for overlap with git hotspot files; overlap boosts candidate confidence with a second corroborating signal.
- Guarded proposal-candidate generation from retained insights across twelve candidate families (seven active by default, five gated).
- Chain-aware candidate sequencing: `type_fix` is suppressed when `lint_fix` is active in the same cycle or was recently emitted; `execution_health_followup` is suppressed when `test_visibility` is active. Both suppression reasons are artifact-visible.
- Blast-radius guardrail: candidates whose `distinct_file_count` exceeds `max_changed_files` (default 30) are suppressed with reason `scope_too_broad` and logged in the decision artifact.
- Validation profiles in every created task: `validation_profile` (`ruff_clean`, `ty_clean`, `tests_pass`, `ci_green`, `manual_review`), `requires_human_approval`, and `evidence_schema_version` appear in every task's `## Provenance` block.
- Structured `EvidenceBundle` in decision artifact for `lint_fix` and `type_fix`: machine-readable count, `distinct_file_count`, delta, trend, top codes, and source.
- Candidate-driven Plane task creation through the proposer lane with provenance and dedup protection.
- End-to-end coverage of the repo-aware autonomy handoff chain from observer -> insights -> decision -> proposer.
- Improve-worker blocked-task triage, repeated-failure pattern detection, and bounded follow-up task creation.
- Proposer idle-board task generation with cooldowns, quotas, deduplication, velocity cap, and staleness guard.
- Dependency drift reporting with optional Plane improve-task creation.
- **Execution health self-tuning loop**: on every `autonomy-cycle` the observer reads retained kodo_plane execution artifacts, derives `high_no_op_rate` and `persistent_validation_failures` insights, and automatically proposes bounded improve tasks when execution quality degrades. No manual trigger or consecutive-run threshold required.
- **Bounded self-tuning regulator** (`tune-autonomy`): aggregates per-family metrics from retained artifacts and emits conservative threshold recommendations. Optional auto-apply mode (double-gated) writes bounded changes to `config/autonomy_tuning.json` with full audit trail.
- **Autonomy tier management** (`autonomy-tiers`): operator CLI to promote or demote families between tier 0 (decision artifact only), tier 1 (Backlog), and tier 2 (Ready for AI).

### PR Automation with Review Loop

- After a successful push, Operations Center opens a PR and enters a two-phase review loop.
- Opt-in per repo via `await_review: true` in `config/operations_center.local.yaml`.
- A dedicated `review` watcher polls GitHub every 60 seconds and drives the loop:

**Phase 1 — Self-review (automatic):**
  - Kodo reads the diff against the base branch and writes a verdict (`LGTM` or `CONCERNS`).
  - `LGTM` → squash-merge, delete branch, task marked Done.
  - `CONCERNS` → kodo runs a revision pass on the branch, then re-reviews (up to `max_self_review_loops`, default 2).
  - If still unresolved after all loops → escalate to Phase 2.

**Phase 2 — Human review (escalated):**
  - Watcher posts a comment on the PR explaining what it couldn't resolve.
  - 👍 on the PR or the latest bot reply → squash-merge + done.
  - Human comment → kodo runs a revision pass; bot replies when done; repeat up to 3 times.
  - 👍 on bot reply → merge.
  - No action after 1 day → merge automatically (timeout fallback).

**Bot safety contract:**
  - All bot-posted comments carry a `<!-- operations-center:bot -->` marker so they are never mistaken for human review requests.
  - `reviewer.bot_logins` in config lists GitHub accounts whose comments are always ignored.
  - `reviewer.allowed_reviewer_logins` optionally restricts human-phase revisions to a whitelist of logins.

- Token resolution: per-repo `token_env` if set, otherwise falls back to the global `git.token_env`.
- PR creation and merge failures are logged but do not block the task from completing.
- Set `OPERATIONS_CENTER_PR_DRY_RUN=1` to log intended PR actions without touching GitHub.
- Existing open PRs are backfilled into the review loop on watcher startup: `./scripts/operations-center.sh backfill-pr-reviews`.

### Execution Safety and Self-Healing

- Execution budget enforcement, retry caps, no-op suppression, and proposal suppression when execution budget is low.
- Contract validation rejects unknown repo keys, missing goal text, and disallowed base branches early with clear Plane comments — no silent fallback.
- Retry cap auto-reset: if the last attempt on a task was more than 1 hour ago, the cap is cleared automatically so a human-unblocked task gets a clean slate.
- Merge conflict self-healing: when retrying a task whose branch is behind the base branch, Operations Center merges the base into the branch so conflict markers appear in the working tree. Kodo resolves them as part of the task. No manual rebase needed.

### Autonomy Hardening (83 improvements across ten implementation rounds)

The following capabilities were added to close gaps toward full autonomous operation:

**Session 1 — 8 proposal and execution improvements:**

- **Goal coherence** — `focus_areas` config list demotes off-topic proposals to Backlog so the system prioritises what matters before filling the board with lower-priority noise.
- **Dependency ordering** — `depends_on: <uuid>` in task descriptions is parsed and enforced; dependent tasks are skipped by the watcher until their dependencies reach Done.
- **Task sizing gate** — decompose findings targeting files >800 lines are split at proposal time into 2–4 bounded part-tasks so Kodo never receives a scope it cannot complete in one context window.
- **Post-merge CI feedback** — every 10 improve cycles, merged PRs are checked for CI failures; regression tasks are created automatically.
- **Self-modification controls** — `self_repo_key` in config identifies the OperationsCenter repo itself; proposals for it are capped at Backlog; auto-execution requires a `self-modify: approved` label.
- **Three-tier conflict detection** — proposals are checked against (1) artifact `changed_files` for Review/Running tasks, (2) open PR file lists from the GitHub API, (3) title tokens as a fallback.
- **Better failure attribution** — `context_limit` and `dependency_missing` classifications added to `classify_execution_result` before `validation_failure`, so follow-up tasks are scoped correctly.
- **Satiation signal** — proposal cycles are recorded; when the last 5 cycles were ≥90% dedup+skipped with zero new tasks created, proposing stops until external state changes.

**Session 2 — 10 full-autonomy gap improvements:**

- **Human escalation channel** — when the same classification blocks ≥5 tasks in 24 hours, a webhook POST is sent to `escalation.webhook_url`; a per-classification cooldown prevents spam.
- **Merge conflict detection and rebase** — every 5 improve cycles, open PRs are checked for `mergeable==false`; an in-place rebase is attempted; a `[Rebase]` task is created on failure.
- **Watcher heartbeat monitoring** — each watcher writes `heartbeat_<role>.json` every cycle; `python -m operations_center.entrypoints.worker.main heartbeat-check` exits non-zero when any watcher is stale.
- **Context handoff for context_limit tasks** — the execution summary is saved in the task artifact; `context_limit` follow-up tasks include a `prior_progress:` block so Kodo continues from where it stopped.
- **Flaky test detection** — per-command pass/fail history is tracked; when a command fails ≥30% of its last 10 runs, it is classified as `flaky_test` rather than `validation_failure`.
- **PR review revision cycle** — every 3 improve cycles, open PRs are checked for `CHANGES_REQUESTED` reviews; `[Revise]` tasks are created with the review comment text as context.
- **Token/credential expiry detection** — on the first watcher cycle, GitHub and Plane tokens are validated; a 401/403 logs a clear error, writes an escalation event, and aborts the loop.
- **Success/failure learning** — task outcomes are recorded per proposal category; categories with >70% success are boosted to Ready for AI; categories with <30% are demoted to Backlog.
- **Scheduled tasks** — `scheduled_tasks:` in config periodically seeds Plane work-items via simple interval expressions (`every: 1w`, `every: 6h`); each due entry creates a Ready-for-AI task that flows through the normal pipeline. Optional `at: HH:MM` and `on_days: [mon, ...]` for time-of-day / weekday anchors. No cron, no external dependency.
- **Stale PR TTL** — every 20 improve cycles, PRs older than `stale_pr_days` (default 7) are closed after a rebase attempt; the originating task is requeued to Backlog.

**Session 3 — 8 reliability and throughput improvements:**

- **GitHub API rate-limit handling** — all GitHub calls go through a `_request()` wrapper; 429 responses retry with `Retry-After` backoff; a warning is logged when `X-RateLimit-Remaining` drops below 10.
- **Pre-execution task validation** — before claiming a goal task, the watcher validates the goal text is non-empty, appropriately scoped, and not a vague catch-all; failures are moved to Backlog with an explanation.
- **Feedback loop automation** — every 15 improve cycles, Done tasks with PR URLs are checked on GitHub; merged/closed outcomes are written to `state/proposal_feedback/` automatically, closing the `proposal_success_rate` learning loop without operator CLI calls.
- **Workspace health monitoring** — every 25 improve cycles, the venv python is verified for each repo with a `local_path`; an automatic bootstrap repair is attempted on failure; a high-priority `[Workspace]` task is created if repair fails.
- **Config schema drift detection** — at watcher startup, `config/operations_center.local.yaml` is compared against the bundled example; missing keys are logged as warnings so silently-disabled features are surfaced immediately.
- **Cost/spend telemetry** — `cost_per_execution_usd` in config enables per-task cost recording; `spend-report` CLI subcommand shows total executions and estimated USD per repo per day/week.
- **Parallel execution within lanes** — `parallel_slots: N` in config (or `--parallel-slots N` CLI flag) launches N task-execution threads; slot 0 owns all periodic scans; throughput scales linearly for independent tasks.
- **Multi-step dependency planning** — tasks with titles containing `refactor`, `migrate`, `redesign`, etc. (or labeled `plan: multi-step`) are automatically decomposed into Analyze → Implement → Verify subtasks with `depends_on` links before any execution begins.

**Session 4 — 10 reliability and learning improvements:** watcher auto-restart bash loop, stale autonomy task invalidation, success-rate circuit breaker, parallel slot write safety (per-path RLock + atomic writes), observer snapshot staleness detection, connection error exponential backoff, human rejection capture, per-task-kind execution profiles, dry-run quiet diagnosis, long-lived deduplication store.

**Session 5 — 10 reliability and observability improvements:** Plane write retry, Kodo process-tree cleanup on timeout, per-task-kind Running TTL, disk space guardrail, quota exhaustion detection, task urgency scoring, board saturation backpressure, scope violation recording, improve→propose systemic feedback channel, Kodo quality erosion detection.

**Session 6 — 10 autonomous operation controls:**

- **Maintenance window gate** — `maintenance_windows:` in config pauses execution and proposals during planned windows (UTC hours, weekday filters, wrap-midnight support).
- **Per-repo daily execution cap** — `max_daily_executions: N` on a repo prevents one noisy repo from consuming the full global budget.
- **Auto-merge on CI green** — `auto_merge_on_ci_green: true` per-repo merges autonomy PRs automatically once all CI checks pass (requires success rate threshold).
- **Failure rate degradation detection** — warns at 60% success rate before the 80% circuit-breaker threshold; fires every 5 watcher cycles.
- **Execution duration baseline** — records wall-clock time per task; logs `duration_anomaly` when a run takes >2× the median.
- **Pre-execution rejection feedback** — when `validate_task_pre_execution` rejects a task, records a failure in the proposal success-rate store so the category learns.
- **Safe revert detection** — post-merge regression tasks now carry `recommended_action: revert` when the merge commit is still at HEAD, `investigate` when subsequent commits exist.
- **Kodo version attribution** — `kodo_version` is recorded in every execution outcome; the circuit breaker skips outcomes from the previous version during a kodo upgrade.
- **Structured audit log export** — `audit-export` CLI prints the full execution event log as JSON; filterable by `--window-days`.
- **Board health snapshot** — `board-health` CLI and automatic 40-cycle scan detect stuck_running tasks, clustered blocked reasons, and quiet repo lanes.

**Session 7 — 7 full-autonomy infrastructure gaps:**

- **Process supervisor** — `entrypoints/supervisor/main.py` manages watcher processes from a YAML manifest; restarts on crash or stale heartbeat; writes `supervisor.status.json`.
- **Credential rotation detection** — checks GitHub PAT expiry header at startup; warns ≤7 days before expiry; escalates ≤1 day before expiry.
- **Transcript failure classification** — `oom`, `timeout`, `model_error`, and `tool_failure` added as distinct classifications before the `infra_tooling` catch-all.
- **Self-healing for repeated blocks** — after 3 consecutive blocks on the same task, a self-healing comment is posted and a warning logged; resets on next success.
- **Dependency update loop** — every 50 improve cycles, `pip list --outdated` is run for repos with `local_path`; major-version bumps create bounded Plane tasks.
- **Cross-repo impact analysis** — `impact_report_paths:` per-repo declares shared interface paths; touching them after a successful goal execution posts a cross-repo warning comment.
- **Human escalation wiring** — circuit-breaker trips and proposer quiet-cycles now fire the escalation webhook with cooldown guard, in addition to the existing blocked-task threshold escalation.

**Session 8 — 10 execution depth and calibration improvements:**

- **ExecutionOutcomeDeriver (Phase 4)** — reads retained `control_outcome.json` and `stderr.txt` from kodo artifacts; classifies `timeout_pattern` (≥2 timeout failures), `test_regression` (test output in stderr of validation failure), `validation_loop` (same task fails validation ≥3 times).
- **Quality trend tracking** — `QualityTrendDeriver` computes lint/type error deltas across ≥3 observer snapshots; emits `lint_improving`, `lint_degrading`, `type_improving`, `type_degrading`, `stagnant` insights with a 10% change threshold.
- **Confidence calibration store** — `ConfidenceCalibrationStore` in `tuning/calibration.py` tracks whether `high/medium/low` confidence labels are accurate; `tune-autonomy` output now includes a calibration table with ⚠ flags for over-confident families.
- **Semantic deduplication** — near-duplicate proposals with different wording are suppressed via Jaccard similarity on title word tokens (threshold 0.5); `[...]` prefix markers are stripped before comparison.
- **Auto revert branch on regression** — when `detect_post_merge_regressions()` finds a safe-revert case (merge commit still at HEAD), a revert branch is automatically created and a `[Revert]` PR opened for human review.
- **Proactive branch divergence check** — the reviewer watcher proactively checks `mergeable_state == "behind"` and calls `_try_auto_rebase()` before waiting for human comments.
- **Runtime error ingestion** — new `entrypoints/error_ingest/main.py` accepts production errors via HTTP webhook (`POST /ingest`) or log file tailing; deduplicates via `state/error_ingest_dedup.json` and creates Plane tasks automatically.
- **Explicit approval control** — `require_explicit_approval: true` per-repo prevents the reviewer watcher from timeout-merging; posts a daily reminder comment instead.
- **Feedback loop config wiring** — `stale_autonomy_backlog_days` is now read from settings and passed into the stale scan on every cycle.
- **Feedback calibration CLI** — `feedback record` accepts optional `--family` and `--confidence` args; records to the calibration store when provided.

**Session 9 — 10 structural and observability improvements:**

- **Event-driven pipeline trigger** — `entrypoints/pipeline_trigger/main.py` watches `.git/FETCH_HEAD`, error ingest state, and CI artifact dirs; fires `autonomy-cycle` reactively within a configurable debounce window instead of relying on schedule alone.
- **Execution environment pre-flight** — before claiming a task, `_check_execution_environment()` verifies required tools (`ruff` for lint_fix, `ty`/`mypy` for type_fix, `pytest` for test_fix) are present in PATH or the repo venv; warns without blocking.
- **No-op loop detection** — `NoOpLoopDeriver` reads proposer artifacts and feedback to detect families proposed ≥3 times in 30 days with zero merged outcomes; emits `noop_loop/family_cycling` so operators can adjust thresholds.
- **Per-repo × family calibration** — `ConfidenceCalibrationStore` now accepts an optional `repo_key` dimension; `report(per_repo=True)` surfaces repo-specific miscalibration distinct from the global aggregate.
- **Rejection reason extraction** — when a PR is escalated to human review, `_extract_rejection_patterns()` scans comments for 8 known patterns (missing tests, naming convention, docstrings, coverage, style, scope, type annotations, breaking changes) and persists them to `state/rejection_patterns.json`.
- **Budget allocation by acceptance rate** — `execution_gate_decision()` reads calibration; when a family's `calibration_ratio < 0.5`, it records an extra execution credit to throttle under-performing families without fully blocking them.
- **Test coverage gap detection** — new `CoverageSignalCollector` reads `coverage.xml`, text reports, and HTML reports; `CoverageGapDeriver` emits `coverage_gap/low_overall` and `coverage_gap/uncovered_files`; `CoverageGapRule` proposes improvement tasks.
- **PR description quality check** — `_check_pr_description_quality()` detects empty or thin PR descriptions (<80 chars) before self-review and patches them with task context via `GitHubPRClient.update_pr_description()`.
- **Evidence-enriched conflict avoidance** — the proposal loop now checks file paths extracted from `evidence_lines` (not just title tokens) against in-flight task artifacts, giving higher-fidelity conflict suppression.
- **Theme aggregation** — `ThemeAggregationDeriver` groups files appearing persistently in top lint/type violations across ≥3 snapshots into `theme/lint_cluster` and `theme/type_cluster` insights; `LintClusterRule` proposes a single `[Refactor]` task instead of N individual fix proposals.

**Session 10 — 10 learning, feedback, and intelligence improvements:**

- **Rejection pattern injection** — `_load_rejection_patterns_for_proposal()` reads `state/rejection_patterns.json` and injects a `## Prior Rejection Patterns` section into every Kodo task description so known reviewer objections are addressed before submission.
- **`awaiting_input` classification** — Kodo can embed `<!-- cp:question: ... -->` in its output to signal a clarifying question; the improve watcher classifies the task as `awaiting_input`, extracts the question, posts it as a Plane comment, and re-queues the task every 8 cycles once a human reply appears.
- **Reviewer → goal requeue** — after `REQUEUE_AS_GOAL_ZERO_CHANGE_THRESHOLD` (default 2) zero-change revision passes in human review, the PR is closed and a fresh `goal` task is created so the problem is re-analysed from scratch instead of looping indefinitely.
- **CampaignStore** — multi-step plan progress is tracked in `state/campaigns.json`; `CampaignStore.create()` is called when a multi-step plan is decomposed; `campaign-status` CLI shows active campaigns with step-level progress bars.
- **Calibration time decay** — `ConfidenceCalibrationStore.calibration_for()` and `report()` now accept `window_days=90`; events older than the window are excluded from acceptance-rate calculations; `cleanup_old_events(window_days)` removes stale events from disk.
- **Complexity gate** — `_estimate_task_complexity()` counts affected files; proposals touching ≥8 files are automatically placed in Backlog instead of Ready for AI, preventing Kodo from receiving an unachievable scope in one context window.
- **Utility scoring** — `_score_proposal_utility()` combines confidence weight, calibration acceptance bonus, state bonus, and scope penalty into a float; proposals are sorted by score before the cycle cap is applied, ensuring the highest-value proposals are created first.
- **CI webhook** — `entrypoints/ci_webhook/main.py` accepts GitHub check-run events over HTTP with HMAC-SHA256 signature validation; writes trigger files to `state/ci_webhook_triggers/` or runs a configurable command, enabling event-driven autonomy-cycle invocation on CI completion.
- **Cross-repo synthesis** — `CrossRepoSynthesisDeriver` reads the latest `repo_insights.json` artifact for every repo in `tools/report/operations_center/insights/` and emits `cross_repo/pattern_detected` when the same insight kind appears in ≥2 repos, surfacing org-wide patterns that warrant a single shared fix task.
- **Priority rescore scan** — every 45 improve cycles, `handle_priority_rescore_scan()` re-evaluates backlog autonomy tasks: demotes those whose signal family's calibration acceptance rate has dropped below 40% (adds `signal_stale` label), and promotes those above 75% to `priority: high`.

### Repo and Branch Selection

- Repo is selected via a `repo: <key>` label on the Plane work item — no separate UI needed.
- Branch defaults to the repo's `default_branch` from config when not specified in the task description.
- Proposer scope is controlled by `propose_enabled: true/false` per repo in `config/operations_center.local.yaml`.
- Unknown repo keys and disallowed branches are rejected at task-start with a Plane comment explaining the failure.

## Lifecycle Contract

```text
goal -> review
goal -> test -> done
goal -> blocked -> improve -> follow-up goal/test or human attention
test -> goal when verification fails
improve -> bounded follow-up work, not open-ended rewrites
propose -> bounded board tasks when the board is idle or recent signals justify it
```

## Configuration

Two files drive local setup — both are gitignored. Committed templates document all available keys:

| Template | Copy to | Purpose |
|----------|---------|---------|
| `config/operations_center.example.yaml` | `config/operations_center.local.yaml` | Repos, branches, Plane connection, git behaviour, execution engine |
| `.env.operations-center.example` | `.env.operations-center.local` | Secrets and runtime knobs — never commit real values |

The split is intentional: config yaml holds structure and behaviour (safe to version), env file holds secrets (gitignored). `token_env` fields in the config yaml reference env var **names**, not values.

Key per-repo config options:

- `await_review: true` — open a PR and enter the review loop instead of auto-merging
- `bootstrap_commands: [...]` — custom install steps for non-Python repos (replaces Python venv setup when set)
- `propose_enabled: true/false` — controls whether the proposer watcher targets this repo

Top-level config options added in the autonomy hardening phase:

- `focus_areas: [...]` — keywords; proposals not matching any entry are demoted to Backlog
- `self_repo_key: <key>` — identifies this OperationsCenter installation; proposals for it go to Backlog; auto-execution requires `self-modify: approved` label
- `stale_pr_days: 7` — PRs open longer than this are closed and requeued by the stale-PR scan
- `escalation.webhook_url: <url>` — POST target for threshold-based escalations
- `escalation.block_threshold: 5` — same-classification blocks within 24h before escalating
- `escalation.cooldown_seconds: 3600` — minimum gap between two escalation POSTs for the same classification
- `scheduled_tasks:` — list of `{every, title, goal, repo_key, kind}` entries plus optional `at: HH:MM` and `on_days: [mon,...]`. `every` is `<num><unit>` with unit ∈ `m h d w`. Due tasks are created at the start of each propose cycle. State persists to `state/scheduled_tasks_last_run.json`.
- `cost_per_execution_usd: 0.0` — operator estimate of cost per Kodo task run; enables spend telemetry (0.0 = disabled)
- `parallel_slots: 1` — number of parallel task-execution threads per watcher lane (1 = serial)

## Quick Start

```bash
./scripts/operations-center.sh setup
source .env.operations-center.local
./scripts/operations-center.sh dev-up
./scripts/operations-center.sh dev-status
```

Then:

1. Create a Plane work item in the configured project.
2. Add labels: `repo: <key>` (e.g. `repo: OperationsCenter`) and `task-kind: goal`.
3. Optionally write a `## Goal` section in the description, or just write the goal as plain text.
4. Move it to `Ready for AI`.
5. Watch the local loop with:

```bash
./scripts/operations-center.sh dev-status
```

## Core Commands

```bash
./scripts/operations-center.sh setup
./scripts/operations-center.sh start
./scripts/operations-center.sh stop
./scripts/operations-center.sh plane-status
./scripts/operations-center.sh run --task-id TASK-123
./scripts/operations-center.sh run-next
./scripts/operations-center.sh watch --role goal
./scripts/operations-center.sh watch --role test
./scripts/operations-center.sh watch --role improve
./scripts/operations-center.sh watch --role propose
./scripts/operations-center.sh watch --role review
./scripts/operations-center.sh watch-all
./scripts/operations-center.sh watch-all-status
./scripts/operations-center.sh watch-all-stop
./scripts/operations-center.sh dev-up
./scripts/operations-center.sh dev-down
./scripts/operations-center.sh dev-restart
./scripts/operations-center.sh dev-status
./scripts/operations-center.sh observe-repo
./scripts/operations-center.sh generate-insights
./scripts/operations-center.sh decide-proposals
./scripts/operations-center.sh decide-proposals --dry-run
./scripts/operations-center.sh propose-from-candidates
./scripts/operations-center.sh propose-from-candidates --dry-run
./scripts/operations-center.sh autonomy-cycle
./scripts/operations-center.sh autonomy-cycle --execute
./scripts/operations-center.sh autonomy-cycle --execute --all-families
./scripts/operations-center.sh analyze-artifacts
./scripts/operations-center.sh analyze-artifacts --repo OperationsCenter --limit 20
./scripts/operations-center.sh tune-autonomy
./scripts/operations-center.sh tune-autonomy --window 30
./scripts/operations-center.sh autonomy-tiers show
./scripts/operations-center.sh autonomy-tiers set --family lint_fix --tier 2
./scripts/operations-center.sh promote-backlog
./scripts/operations-center.sh promote-backlog --family lint_fix --execute
./scripts/operations-center.sh backfill-pr-reviews
./scripts/operations-center.sh plane-doctor --task-id TASK-123
./scripts/operations-center.sh smoke --task-id TASK-123 --comment-only
./scripts/operations-center.sh dependency-check
./scripts/operations-center.sh janitor
```

## CI and Local Validation

Three checks run on every push and PR (`.github/workflows/ci.yml`):

- **ruff** — lint and style
- **ty** — type checking (`ty check src/`)
- **pytest** — tests

Local equivalent:

```bash
ruff check .
ty check src/
pytest -q
```

`ty` is the active type-checking tool. `mypy` is not used or required.

---

## Execution Controls

Operations Center now enforces a bounded execution-control layer before expensive worker actions run.

- rolling execution budgets over hourly and daily windows
- retry caps per task for automatic `goal` and `test` execution
- watcher-side no-op suppression for unchanged task signatures
- proposal suppression when execution budget is low
- explicit retained accounting in `tools/report/operations_center/execution/usage.json`

Default local knobs are set in [.env.operations-center.local](/home/dev/Documents/GitHub/OperationsCenter/.env.operations-center.local):

- `OPERATIONS_CENTER_MAX_EXEC_PER_HOUR`
- `OPERATIONS_CENTER_MAX_EXEC_PER_DAY`
- `OPERATIONS_CENTER_MAX_RETRIES_PER_TASK`
- `OPERATIONS_CENTER_MIN_REMAINING_EXEC_FOR_PROPOSALS`
- `OPERATIONS_CENTER_WATCH_INTERVAL_GOAL_SECONDS`
- `OPERATIONS_CENTER_WATCH_INTERVAL_TEST_SECONDS`
- `OPERATIONS_CENTER_WATCH_INTERVAL_IMPROVE_SECONDS`
- `OPERATIONS_CENTER_WATCH_INTERVAL_PROPOSE_SECONDS`
- `OPERATIONS_CENTER_WATCH_INTERVAL_REVIEW_SECONDS`
- `OPERATIONS_CENTER_PR_DRY_RUN` — set to `1` to log intended PR actions without touching GitHub

Skipping is treated as a valid outcome when execution is not justified. Budget skips, no-op skips, retry-cap blocks, and proposal suppression are written to retained artifacts and surfaced in watcher logs.

### Heartbeat Monitoring

Each watcher writes a `heartbeat_<role>.json` timestamp file every cycle. To check liveness:

```bash
python -m operations_center.entrypoints.worker.main heartbeat-check --log-dir logs/local/watch-all
```

Exit code 0 = all healthy. Exit code 1 = stale watchers listed on stderr. Wire this into cron or a simple monitoring script for unattended operation.

### Spend Report

```bash
python -m operations_center.entrypoints.worker.main spend-report --window-days 7
```

Shows total executions and estimated cost per repo. Requires `cost_per_execution_usd` in config.

## Local Stack Startup

The easiest way to bring up the whole local system is:

```bash
./scripts/operations-center.sh dev-up
```

That starts:

- Plane on `http://localhost:8080`
- all five watcher lanes: `goal`, `test`, `improve`, `propose`, and `review`

Useful companions:

- `./scripts/operations-center.sh dev-status`
- `./scripts/operations-center.sh dev-down`
- `./scripts/operations-center.sh dev-restart`

### Live Status Dashboard

```bash
./scripts/operations-center.sh status [--repo REPO,REPO]
```

Runs `scripts/oc-status.py` — a live terminal dashboard that refreshes every 2 seconds, showing watcher state (role, cycle, last action, task ID, age, alive), active Kodo workspaces, board counts per repo, circuit breaker status, and available memory. Uses direct row addressing so it never grows the terminal scrollback. Pass `--repo OperationsCenter,OperatorConsole` to filter board and workspace rows by repo key.

## Runtime Files

- Command logs: `logs/local/`
- Plane runtime logs: `logs/local/plane-runtime/`
- Watcher logs, PIDs, and heartbeat files: `logs/local/watch-all/`
- Retained execution artifacts: `tools/report/kodo_plane/`
- Retained execution usage ledger: `tools/report/operations_center/execution/`
- Repo observer snapshots: `tools/report/operations_center/observer/`
- Insight artifacts: `tools/report/operations_center/insights/`
- Decision artifacts: `tools/report/operations_center/decision/`
- Proposer result artifacts: `tools/report/operations_center/proposer/`
- Tuning run artifacts: `tools/report/operations_center/tuning/`
- Proposal feedback records: `state/proposal_feedback/`
- Autonomy cycle reports: `logs/autonomy_cycle/`

The wrapper runs `janitor` automatically before commands and keeps local logs/artifacts for 1 day by default. Override with `OPERATIONS_CENTER_RETENTION_DAYS`.

## Why An Autonomy-Generated Task Exists

Every repo-aware autonomy task should be traceable through retained artifacts and provenance metadata.

A generated task can be followed back through:

- repo observer snapshot(s)
- normalized insight artifact
- guarded decision artifact
- proposer result artifact
- task provenance fields and source labels in Plane

This keeps the loop inspectable instead of opaque: operators can reconstruct why a task exists, which signal path created it, and which candidate or dedup key it came from.

## What Good Looks Like

The repo-aware autonomy loop is behaving well when:

- it proposes useful, bounded tasks without manual prompting
- it suppresses duplicate or weak proposals through dedup keys, cooldowns, and quotas
- it produces zero proposals when no strong signal exists
- every autonomy-generated task can be traced back to retained repo signals and decision artifacts
- it improves visibility and workflow quality rather than inventing open-ended work
- when execution quality degrades (high no-op rate, recurring validation failures), it surfaces an improve task automatically rather than silently continuing to generate unhelpful work

## Current Limitations

- Local-first and single-machine only.
- Polling watchers, not webhooks.
- No queue cluster or distributed scheduler.
- No multi-machine lock manager or cross-host deduplication.
- PR automation is opt-in per repo; branch protection rules on GitHub may block auto-merge if required status checks are not satisfied.
- No multi-repo orchestration yet.
- No production-grade supervisor beyond local `watch-all`.
- No unlimited autonomous self-generated work; proposer is bounded by guardrails.
- No automatic dependency repinning workflow during normal runs.
- Scheduled tasks require the optional `croniter` Python package (`pip install croniter`).
- Cost telemetry is estimate-based; `cost_per_execution_usd` must be set manually — OperationsCenter does not parse Kodo billing output.
- Parallel slots share the Plane API rate limit; monitor `github_rate_limit_low` warnings when `parallel_slots > 1`.

## Documentation

### Design

- [Lifecycle Contract](docs/design/lifecycle.md)
- [Improve Worker](docs/design/improve_worker.md)
- [Autonomy Hardening](docs/design/autonomy_gaps.md) — 26 full-autonomy improvements across 3 sessions: rate-limit handling, pre-execution validation, feedback loop automation, workspace health, config drift detection, spend telemetry, parallel execution, multi-step planning, and more
- [Repo Observer](docs/design/autonomy_repo_observer.md)
- [Insight Engine](docs/design/autonomy_insight_engine.md)
- [Decision Engine](docs/design/autonomy_decision_engine.md)
- [Proposer Integration](docs/design/autonomy_proposer_integration.md)
- [Repo-Aware Autonomy Layer](docs/design/repo_aware_autonomy.md)
- [Self-Tuning Regulator](docs/design/autonomy_self_tuning_regulator.md)
- [Execution Budget And Safety Controls](docs/design/execution_budget_and_safety_controls.md)
- [Plane + Kodo Wrapper Design](docs/design/plane_kodo_wrapper.md)
- [Roadmap](docs/design/roadmap.md)

### Operator Guides

- [Golden-Path Demo](docs/demo.md) — start here; also use as a post-change validation ritual
- [Setup Guide](docs/operator/setup.md)
- [Runtime Guide](docs/operator/runtime.md) — commands, watcher roles, parallel slots, spend report, dry-run-first posture, heartbeat monitoring
- [Autonomy Threshold Tuning](docs/operator/tuning.md) — per-family thresholds, analyze-artifacts loop, tune-autonomy regulation loop
- [PR Review Loop Guide](docs/operator/pr_review.md) — two-phase review, guardrails, troubleshooting
- [Diagnostics and Maintenance](docs/operator/diagnostics.md) — heartbeat check, credential validation, config drift, workspace health, spend report, debugging order

### Roadmap

- [Backlog](docs/backlog.md)

## Task Template

The minimum required to create a task manually in Plane:

**Labels:** `repo: OperationsCenter`, `task-kind: goal`

Repos currently managed: `OperationsCenter`, `OperatorConsole`, `SwitchBoard`, `WorkStation`.

**Description (plain):**
```text
Fix the config parsing bug that causes repeated failures on startup.
```

Branch defaults to the repo's `default_branch` from config. For full control, use the `## Execution` block:

```text
## Execution
repo: OperationsCenter
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

- `repo: <key>` label is the primary way to set the target repo. The `## Execution` block is optional for manually created tasks.
- `base_branch` defaults to the repo's `default_branch` in config when omitted.
- `mode: goal` is the supported runtime mode today.
- `allowed_paths` is enforced before commit/push.
- Kodo receives Goal/Constraints, not the `## Execution` block.
- Use labels like `task-kind: goal`, `task-kind: test`, `task-kind: improve`, and `source: manual` for explicit routing.

## Ownership boundary

OperationsCenter owns everything about how autonomous agents reason, act, and interact with platform services: the autonomy loop, proposal/decision logic, executor adapters (Aider, Kodo), and client adapters for Plane and SwitchBoard (`PlaneClient`, `SwitchBoardClient`).

OperationsCenter does **not** own the infrastructure required to run Plane or SwitchBoard. Those are platform dependencies operated through [WorkStation](https://github.com/Velascat/WorkStation). OperationsCenter's `.env.operations-center.example` documents the environment contract (URLs, secrets) that WorkStation satisfies at runtime.

**Plane infra specifically:** `deployment/plane/manage.sh` is a delegation wrapper — it calls `WorkStation/scripts/plane.sh`, which is the canonical Plane lifecycle manager. The wrapper preserves the `dev-up`, `plane-up`, and `plane-down` command interface so existing workflows continue to work without any change. WorkStation must be cloned as a sibling directory (or `OPERATIONS_CENTER_WORKSTATION_DIR` must be set).

For the full platform ownership model see `WorkStation/docs/architecture/ownership.md`.

---

## Honesty Check

This repo is not trying to be a production distributed control plane yet. It is a local autonomous workflow system with clear worker lanes, explicit lifecycle handoffs, and operator-visible board/runtime feedback.

---

## License

GNU Affero General Public License v3.0 or later (AGPL-3.0-or-later) — see [LICENSE](LICENSE).
