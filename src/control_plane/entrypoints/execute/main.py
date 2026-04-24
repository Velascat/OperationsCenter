"""Canonical execute entrypoint.

Consumes a proposal/decision bundle, constructs a canonical ExecutionRequest,
runs the mandatory policy gate, and then invokes the selected canonical
backend adapter when execution is allowed.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from control_plane.backends.factory import CanonicalBackendRegistry
from control_plane.config.settings import load_settings
from control_plane.contracts.proposal import TaskProposal
from control_plane.contracts.routing import LaneDecision
from control_plane.execution.artifact_writer import RunArtifactWriter
from control_plane.execution.coordinator import ExecutionCoordinator
from control_plane.execution.handoff import ExecutionRuntimeContext
from control_plane.planning.models import ProposalDecisionBundle


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Execute a routed proposal through the canonical execution boundary."
    )
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--bundle", required=True, type=Path, help="JSON file containing proposal and decision")
    parser.add_argument("--workspace-path", required=True, type=Path)
    parser.add_argument("--task-branch", required=True)
    parser.add_argument("--goal-file-path", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--no-artifacts", action="store_true", help="Skip writing run artifacts to disk")
    return parser


def _load_bundle(path: Path) -> ProposalDecisionBundle:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return ProposalDecisionBundle(
        proposal=TaskProposal.model_validate(payload["proposal"]),
        decision=LaneDecision.model_validate(payload["decision"]),
    )


def main() -> int:
    args = _build_parser().parse_args()
    settings = load_settings(args.config)
    bundle = _load_bundle(args.bundle)
    runtime = ExecutionRuntimeContext(
        workspace_path=args.workspace_path,
        task_branch=args.task_branch,
        goal_file_path=args.goal_file_path,
    )
    coordinator = ExecutionCoordinator(
        adapter_registry=CanonicalBackendRegistry.from_settings(settings),
    )
    outcome = coordinator.execute(bundle, runtime)

    if not args.no_artifacts:
        RunArtifactWriter().write_run(
            proposal=bundle.proposal,
            decision=bundle.decision,
            request=outcome.request,
            result=outcome.result,
            executed=outcome.executed,
        )

    payload = {
        "request": outcome.request.model_dump(mode="json"),
        "policy_decision": outcome.policy_decision.model_dump(mode="json"),
        "result": outcome.result.model_dump(mode="json"),
        "record": outcome.record.model_dump(mode="json"),
        "trace": outcome.trace.model_dump(mode="json"),
        "executed": outcome.executed,
    }
    rendered = json.dumps(payload, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
