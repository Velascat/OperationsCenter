"""Canonical execute entrypoint.

Consumes a proposal/decision bundle, constructs a canonical ExecutionRequest,
runs the mandatory policy gate, and then invokes the selected canonical
backend adapter when execution is allowed.

Failure handling:
- Unexpected coordinator exception: writes partial artifacts (proposal +
  decision), emits structured error JSON to --output or stdout, exits 1.
"""

from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path

from operations_center.backends.factory import CanonicalBackendRegistry
from operations_center.config.settings import load_settings
from operations_center.contracts.proposal import TaskProposal
from operations_center.contracts.routing import LaneDecision
from operations_center.execution.artifact_writer import RunArtifactWriter
from operations_center.execution.coordinator import ExecutionCoordinator
from operations_center.execution.handoff import ExecutionRuntimeContext
from operations_center.execution.workspace import WorkspaceManager
from operations_center.planning.models import ProposalDecisionBundle


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
    parser.add_argument("--source", default="", help="Run source tag written to run_metadata.json (e.g. manual, auto_once)")
    return parser


def _load_bundle(path: Path) -> ProposalDecisionBundle:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return ProposalDecisionBundle(
        proposal=TaskProposal.model_validate(payload["proposal"]),
        decision=LaneDecision.model_validate(payload["decision"]),
    )


def _emit(payload: dict, output: Path | None) -> None:
    rendered = json.dumps(payload, indent=2, sort_keys=True)
    if output:
        output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)


def _artifacts_suppressed(args) -> bool:
    """Return True when artifact writing must be skipped.

    Suppressed when --no-artifacts is passed, or when running under pytest
    (PYTEST_CURRENT_TEST env var set) without an explicit --source flag,
    or when CONSOLE_DISABLE_ARTIFACTS=1.
    """
    import os
    if args.no_artifacts:
        return True
    if os.environ.get("CONSOLE_DISABLE_ARTIFACTS") == "1":
        return True
    return False


def main() -> int:
    args = _build_parser().parse_args()
    no_artifacts = _artifacts_suppressed(args)
    settings = load_settings(args.config)
    bundle = _load_bundle(args.bundle)
    runtime = ExecutionRuntimeContext(
        workspace_path=args.workspace_path,
        task_branch=args.task_branch,
        goal_file_path=args.goal_file_path,
    )
    import os as _os
    await_review_repos = {
        rk for rk, rcfg in (settings.repos or {}).items()
        if getattr(rcfg, "await_review", False)
    }
    def _env_int(name: str) -> int | None:
        raw = _os.environ.get(name, "").strip()
        try:
            return int(raw) if raw else None
        except ValueError:
            return None
    # Bot identity: prefer settings.git.author_name / author_email so commits
    # attribute correctly per repo workflow. Falls back to a generic identity
    # when the fields aren't set.
    bot_name  = getattr(settings.git, "author_name",  None)  or "Operations Center"
    bot_email = getattr(settings.git, "author_email", None)  or "operations-center@local"
    workspace_manager = WorkspaceManager(
        github_token=settings.git_token(),
        await_review_repos=await_review_repos,
        bot_identity=(bot_name, bot_email),
        max_files=_env_int("OPS_CENTER_MAX_FILES"),
        max_lines=_env_int("OPS_CENTER_MAX_LINES"),
    )
    coordinator = ExecutionCoordinator(
        adapter_registry=CanonicalBackendRegistry.from_settings(settings),
        workspace_manager=workspace_manager,
    )

    try:
        outcome = coordinator.execute(bundle, runtime)
    except Exception as exc:
        partial_run_id = f"partial-{uuid.uuid4().hex[:8]}"
        if not no_artifacts:
            try:
                RunArtifactWriter().write_partial(
                    run_id=partial_run_id,
                    proposal=bundle.proposal,
                    decision=bundle.decision,
                    reason=f"Coordinator raised unexpected exception: {exc}",
                )
            except Exception:
                pass  # best-effort

        error_payload = {
            "error": "coordinator_failure",
            "error_type": "backend_error",
            "message": str(exc),
            "partial_run_id": partial_run_id,
        }
        _emit(error_payload, args.output)
        return 1

    if not no_artifacts:
        extra: dict = {}
        if args.source:
            extra["source"] = args.source
        RunArtifactWriter().write_run(
            proposal=bundle.proposal,
            decision=bundle.decision,
            request=outcome.request,
            result=outcome.result,
            executed=outcome.executed,
            extra_metadata=extra or None,
        )

    payload = {
        "request": outcome.request.model_dump(mode="json"),
        "policy_decision": outcome.policy_decision.model_dump(mode="json"),
        "result": outcome.result.model_dump(mode="json"),
        "record": outcome.record.model_dump(mode="json"),
        "trace": outcome.trace.model_dump(mode="json"),
        "executed": outcome.executed,
    }
    _emit(payload, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
