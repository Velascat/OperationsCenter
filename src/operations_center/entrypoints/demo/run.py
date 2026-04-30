# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""
entrypoints/demo/run.py — self-contained OperationsCenter end-to-end demo.

Proves the full internal boundary without external services:

    PlanningContext
      -> TaskProposal          (proposal_builder)
      -> LaneDecision          (stub routing — labeled as such)
      -> ProposalDecisionBundle
      -> ExecutionCoordinator  (policy gate + adapter dispatch)
      -> ExecutionResult       (DemoStubBackendAdapter)
      -> ExecutionRecord       (observability recorder)
      -> ExecutionTrace        (report builder)
      -> retained evidence files
      -> human-readable terminal summary

Usage:

    python -m operations_center.entrypoints.demo.run \\
        --goal "Write a tiny hello-world execution artifact" \\
        --repo-key demo \\
        --workspace-path /tmp/operations-center-demo \\
        --backend stub

No Plane, no GitHub, no Kodo CLI, no Claude CLI, no network access required.
"""

from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path

from operations_center.backends.demo_stub import DemoStubBackendAdapter
from operations_center.backends.factory import CanonicalBackendRegistry
from operations_center.contracts.enums import BackendName, LaneName
from operations_center.contracts.routing import LaneDecision
from operations_center.execution.coordinator import ExecutionCoordinator, ExecutionRunOutcome
from operations_center.execution.handoff import ExecutionRuntimeContext
from operations_center.planning.models import PlanningContext, ProposalDecisionBundle
from operations_center.planning.proposal_builder import build_proposal
from operations_center.policy.engine import PolicyEngine
from operations_center.policy.models import (
    BranchGuardrail,
    PathPolicy,
    PolicyConfig,
    RepoPolicy,
    ReviewRequirement,
    ToolGuardrail,
)


# ---------------------------------------------------------------------------
# Demo-specific policy: explicitly permissive, labeled as demo-only
# ---------------------------------------------------------------------------


def _build_demo_policy(repo_key: str) -> PolicyEngine:
    """Permissive policy for the demo repo key.

    Any real repo key will fall through to DEFAULT_REPO_POLICY.  The demo
    repo key gets a fully permissive policy so the terminal output clearly
    shows ALLOW (not blocked by validation or branch requirements that do
    not apply to a stub run).
    """
    demo_repo_policy = RepoPolicy(
        repo_key=repo_key,
        enabled=True,
        risk_profile="demo",
        path_policy=PathPolicy(rules=[], default_mode="allow"),
        branch_guardrail=BranchGuardrail(
            allow_direct_commit=True,
            require_branch=False,
            branch_name_pattern="",
            require_pr=False,
            allowed_base_branches=[],
        ),
        tool_guardrail=ToolGuardrail(
            network_mode="allowed",
            blocked_tool_classes=[],
            allow_destructive_actions=False,
        ),
        validation_requirements=[],
        review_requirement=ReviewRequirement(
            autonomous_allowed=True,
            require_review_for_risk_levels=[],
            require_review_for_task_types=[],
            blocked_without_human=False,
        ),
        allowed_task_types=[],
        blocked_task_types=[],
    )
    from operations_center.policy.defaults import DEFAULT_REPO_POLICY

    config = PolicyConfig(
        repo_policies=[demo_repo_policy],
        default_policy=DEFAULT_REPO_POLICY,
    )
    return PolicyEngine.from_config(config)


# ---------------------------------------------------------------------------
# Stub routing
# ---------------------------------------------------------------------------


def _make_stub_lane_decision(proposal_id: str) -> LaneDecision:
    """Build a deterministic LaneDecision without calling SwitchBoard."""
    return LaneDecision(
        proposal_id=proposal_id,
        selected_lane=LaneName.AIDER_LOCAL,
        selected_backend=BackendName.DEMO_STUB,
        confidence=1.0,
        policy_rule_matched="demo.stub_routing",
        rationale=(
            "Offline stub routing for demo mode — deterministic, no external services required"
        ),
    )


# ---------------------------------------------------------------------------
# Evidence persistence
# ---------------------------------------------------------------------------


def _write_evidence(
    outcome: ExecutionRunOutcome,
    evidence_dir: Path,
    proposal,
    decision: LaneDecision,
) -> list[Path]:
    evidence_dir.mkdir(parents=True, exist_ok=True)

    files: list[tuple[Path, str]] = [
        (evidence_dir / "proposal.json", proposal.model_dump_json(indent=2)),
        (evidence_dir / "decision.json", decision.model_dump_json(indent=2)),
        (evidence_dir / "execution_request.json", outcome.request.model_dump_json(indent=2)),
        (evidence_dir / "result.json", outcome.result.model_dump_json(indent=2)),
        (evidence_dir / "execution_record.json", outcome.record.model_dump_json(indent=2)),
        (evidence_dir / "execution_trace.json", outcome.trace.model_dump_json(indent=2)),
    ]
    written: list[Path] = []
    for path, content in files:
        path.write_text(content + "\n", encoding="utf-8")
        written.append(path)

    meta = {
        "run_id": outcome.result.run_id,
        "proposal_id": proposal.proposal_id,
        "decision_id": decision.decision_id,
        "selected_lane": decision.selected_lane.value,
        "selected_backend": decision.selected_backend.value,
        "policy_status": outcome.policy_decision.status.value,
        "result_status": outcome.result.status.value,
        "success": outcome.result.success,
        "executed": outcome.executed,
    }
    meta_path = evidence_dir / "run_metadata.json"
    meta_path.write_text(json.dumps(meta, indent=2, default=str) + "\n", encoding="utf-8")
    written.append(meta_path)
    return written


# ---------------------------------------------------------------------------
# Terminal output
# ---------------------------------------------------------------------------


def _print_section(title: str) -> None:
    print(f"\n[{title}]")


def _print_summary(
    outcome: ExecutionRunOutcome,
    proposal,
    decision: LaneDecision,
    evidence_files: list[Path],
) -> None:
    result = outcome.result
    policy = outcome.policy_decision
    trace = outcome.trace

    print("=" * 60)
    print("OperationsCenter Demo Run")
    print("=" * 60)

    _print_section("PLANNING — TaskProposal")
    print(f"  proposal_id : {proposal.proposal_id}")
    print(f"  task_id     : {proposal.task_id}")
    print(f"  task_type   : {proposal.task_type.value}")
    print(f"  risk_level  : {proposal.risk_level.value}")
    print(f"  goal        : {proposal.goal_text}")

    _print_section("ROUTING — LaneDecision  [stub mode — labeled, not production]")
    print(f"  decision_id : {decision.decision_id}")
    print(f"  lane        : {decision.selected_lane.value}")
    print(f"  backend     : {decision.selected_backend.value}")
    print(f"  rule        : {decision.policy_rule_matched}")
    print(f"  rationale   : {decision.rationale}")

    _print_section("PROPOSAL-DECISION BUNDLE")
    print(f"  {outcome.request.proposal_id[:8]} + {decision.decision_id[:8]} -> bundled")

    _print_section("POLICY — gate result")
    print(f"  status      : {policy.status.value.upper()}")
    if policy.violations:
        for v in policy.violations:
            tag = "BLOCK" if v.blocking else "review"
            print(f"  violation   : [{tag}] {v.rule_id}: {v.message}")
    if policy.warnings:
        for w in policy.warnings:
            print(f"  warning     : {w.rule_id}: {w.message}")
    if not policy.violations and not policy.warnings:
        print("  (no violations or warnings)")
    print(f"  executed    : {outcome.executed}")

    if outcome.executed:
        _print_section("EXECUTION — DemoStubBackendAdapter")
        print(f"  run_id      : {result.run_id}")
        print(f"  status      : {result.status.value.upper()}")
        print(f"  success     : {result.success}")
        if result.diff_stat_excerpt:
            print(f"  diff_stat   : {result.diff_stat_excerpt}")
        for art in result.artifacts:
            print(f"  artifact    : {art.uri or art.content}")
    else:
        _print_section("EXECUTION — blocked / skipped")
        print(f"  reason      : {result.failure_reason}")

    _print_section("OBSERVABILITY — retained records")
    print(f"  headline    : {trace.headline}")
    print(f"  summary     : {trace.summary}")
    if trace.warnings:
        for w in trace.warnings:
            print(f"  trace warn  : {w}")
    print()
    print("  Evidence files:")
    for f in evidence_files:
        print(f"    {f}")

    print()
    print("=" * 60)
    if outcome.executed and result.success:
        print("Demo completed successfully.")
    elif not outcome.executed:
        print("Demo run blocked by policy (see [POLICY] above).")
    else:
        print(f"Demo run finished with status: {result.status.value}")
    print("=" * 60)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m operations_center.entrypoints.demo.run",
        description=(
            "OperationsCenter end-to-end demo — proves the full internal boundary "
            "without external services."
        ),
    )
    p.add_argument(
        "--goal",
        required=True,
        help='Natural-language task goal, e.g. "Write a tiny hello-world artifact"',
    )
    p.add_argument(
        "--repo-key",
        default="demo",
        help="Logical repo key (default: demo).  Must be non-empty.",
    )
    p.add_argument(
        "--workspace-path",
        type=Path,
        default=Path("/tmp/operations-center-demo"),
        help="Directory where the stub adapter writes its artifact (created if absent).",
    )
    p.add_argument(
        "--backend",
        choices=["stub"],
        default="stub",
        help="Backend to use (only 'stub' supported in demo mode).",
    )
    p.add_argument(
        "--routing",
        choices=["stub"],
        default="stub",
        help="Routing mode (only 'stub' supported in demo mode).",
    )
    p.add_argument(
        "--blocked-policy",
        action="store_true",
        help=(
            "Run with a policy that blocks execution.  "
            "Proves the policy gate prevents adapter invocation."
        ),
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    # --- 1. Planning ---
    context = PlanningContext(
        goal_text=args.goal,
        task_type="simple_edit",
        execution_mode="goal",
        repo_key=args.repo_key,
        clone_url="demo://local",
        base_branch="main",
        risk_level="low",
        priority="normal",
        proposer="operations-center-demo",
        push_on_success=False,
        open_pr=False,
    )
    proposal = build_proposal(context)

    # --- 2. Routing (stub) ---
    decision = _make_stub_lane_decision(proposal.proposal_id)

    # --- 3. Bundle ---
    bundle = ProposalDecisionBundle(
        proposal=proposal,
        decision=decision,
        context=context,
        trace_notes="demo run — stub routing and stub backend",
    )

    # --- 4. Runtime context ---
    workspace = args.workspace_path
    short_id = uuid.uuid4().hex[:8]
    runtime = ExecutionRuntimeContext(
        workspace_path=workspace,
        task_branch=f"demo/run-{short_id}",
    )

    # --- 5. Policy engine ---
    if args.blocked_policy:
        from operations_center.policy.models import RepoPolicy, PolicyConfig
        from operations_center.policy.models import (
            ReviewRequirement,
        )

        blocking_policy = RepoPolicy(
            repo_key=args.repo_key,
            enabled=True,
            review_requirement=ReviewRequirement(
                autonomous_allowed=False,
                blocked_without_human=True,
            ),
        )
        from operations_center.policy.defaults import DEFAULT_REPO_POLICY

        policy_engine = PolicyEngine.from_config(
            PolicyConfig(repo_policies=[blocking_policy], default_policy=DEFAULT_REPO_POLICY)
        )
    else:
        policy_engine = _build_demo_policy(args.repo_key)

    # --- 6. Adapter registry ---
    registry = CanonicalBackendRegistry(
        {
            BackendName.DEMO_STUB: DemoStubBackendAdapter(),
        }
    )

    # --- 7. ExecutionCoordinator (no WorkspaceManager — stub handles files itself) ---
    coordinator = ExecutionCoordinator(
        adapter_registry=registry,
        policy_engine=policy_engine,
        workspace_manager=None,
    )

    # --- 8. Execute ---
    outcome = coordinator.execute(bundle, runtime)

    # --- 9. Retain evidence ---
    run_id = outcome.result.run_id
    evidence_dir = workspace / ".operations_center" / "runs" / run_id
    evidence_files = _write_evidence(outcome, evidence_dir, proposal, decision)

    # --- 10. Terminal summary ---
    _print_summary(outcome, proposal, decision, evidence_files)

    # Exit code: 0 on success, non-zero when blocked or failed
    if outcome.executed and outcome.result.success:
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
