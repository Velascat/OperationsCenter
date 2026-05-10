# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Planning-only worker entrypoint.

This entrypoint exists to prove the split between OperationsCenter's planning
surface and its execution surface:

    PlanningContext -> TaskProposal -> LaneDecision -> ProposalDecisionBundle

It intentionally does not construct backend adapters, create workspaces, or run
execution backends. Live execution remains a separate OperationsCenter boundary
owned by ``operations_center.execution.coordinator.ExecutionCoordinator``.

Failure handling:
- SwitchBoardUnavailableError: writes partial artifact (proposal only),
  prints structured JSON error to stdout, exits 1.
"""

from __future__ import annotations

import argparse
import json
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from operations_center.planning.models import PlanningContext
from operations_center.routing.client import SwitchBoardUnavailableError
from operations_center.routing.service import PlanningService


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a canonical TaskProposal and route it through SwitchBoard."
    )
    parser.add_argument("--input", type=Path, help="Path to a JSON file describing a PlanningContext.")
    parser.add_argument("--output", type=Path, help="Optional output path for the proposal/decision bundle JSON.")
    parser.add_argument("--goal", help="Goal text when not using --input.")
    parser.add_argument("--task-type", default="unknown")
    parser.add_argument("--execution-mode", default="goal")
    parser.add_argument("--repo-key", default="")
    parser.add_argument("--clone-url", default="")
    parser.add_argument("--base-branch", default="main")
    parser.add_argument("--risk-level", default="low")
    parser.add_argument("--priority", default="normal")
    parser.add_argument("--task-id", default="")
    parser.add_argument("--project-id", default="")
    parser.add_argument("--label", action="append", dest="labels", default=[])
    parser.add_argument("--allowed-path", action="append", dest="allowed_paths", default=[])
    parser.add_argument("--validation-command", action="append", dest="validation_commands", default=[])
    return parser


def _context_from_args(args: argparse.Namespace) -> PlanningContext:
    if args.input:
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        return PlanningContext(**payload)
    if not args.goal:
        raise SystemExit("--goal is required when --input is not provided")
    return PlanningContext(
        goal_text=args.goal,
        task_type=args.task_type,
        execution_mode=args.execution_mode,
        repo_key=args.repo_key,
        clone_url=args.clone_url,
        base_branch=args.base_branch,
        risk_level=args.risk_level,
        priority=args.priority,
        task_id=args.task_id,
        project_id=args.project_id,
        labels=list(args.labels),
        allowed_paths=list(args.allowed_paths),
        validation_commands=list(args.validation_commands),
    )


def _bundle_json(bundle) -> dict[str, Any]:
    context = bundle.context if bundle.context is not None else None
    return {
        "proposal": bundle.proposal.model_dump(mode="json"),
        "decision": bundle.decision.model_dump(mode="json"),
        "context": asdict(context) if context is not None else None,
        "trace_notes": bundle.trace_notes,
        "run_summary": bundle.run_summary,
    }


def _routing_failure_json(proposal, partial_run_id: str, message: str) -> dict[str, Any]:
    """Structured error envelope emitted to stdout on SwitchBoard failure."""
    return {
        "error": "routing_failure",
        "error_type": "routing_error",
        "message": message,
        "proposal_id": proposal.proposal_id,
        "partial_run_id": partial_run_id,
    }


def main(service: PlanningService | None = None) -> int:
    args = _build_parser().parse_args()
    context = _context_from_args(args)

    if service is None:
        service = PlanningService.default()

    proposal = service.build_proposal(context)

    try:
        bundle = service.route_proposal(proposal, context=context)
    except SwitchBoardUnavailableError as exc:
        partial_run_id = f"partial-{uuid.uuid4().hex[:8]}"
        try:
            from operations_center.execution.artifact_writer import RunArtifactWriter
            RunArtifactWriter().write_partial(
                run_id=partial_run_id,
                proposal=proposal,
                reason=str(exc),
            )
        except Exception:
            pass  # best-effort; never mask the original error

        error_payload = _routing_failure_json(proposal, partial_run_id, str(exc))
        rendered = json.dumps(error_payload, indent=2, ensure_ascii=False)
        if args.output:
            args.output.write_text(rendered + "\n", encoding="utf-8")
        else:
            print(rendered)
        return 1

    payload = _bundle_json(bundle)
    rendered = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
