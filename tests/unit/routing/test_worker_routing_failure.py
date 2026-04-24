"""Tests for worker/main.py SwitchBoard failure handling.

When SwitchBoardUnavailableError is raised during route_proposal(), the worker
must:
- Exit with return code 1
- Emit structured error JSON to stdout
- Attempt to write a partial artifact (best-effort; not asserted here)
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from control_plane.contracts.enums import FailureReasonCategory
from control_plane.contracts.proposal import TaskProposal
from control_plane.planning.models import PlanningContext, ProposalDecisionBundle
from control_plane.planning.proposal_builder import build_proposal
from control_plane.routing.client import SwitchBoardUnavailableError


def _make_service(*, route_raises: Exception | None = None):
    """Build a mock PlanningService for injection into main()."""
    service = MagicMock()

    proposal = build_proposal(
        PlanningContext(
            goal_text="Fix lint errors",
            task_type="lint_fix",
            repo_key="svc",
            clone_url="https://example.invalid/svc.git",
        )
    )
    service.build_proposal.return_value = proposal

    if route_raises is not None:
        service.route_proposal.side_effect = route_raises
    else:
        from control_plane.contracts.enums import BackendName, LaneName
        from control_plane.contracts.routing import LaneDecision
        decision = LaneDecision(
            proposal_id=proposal.proposal_id,
            selected_lane=LaneName.AIDER_LOCAL,
            selected_backend=BackendName.DIRECT_LOCAL,
            confidence=0.9,
            policy_rule_matched="test",
        )
        bundle = ProposalDecisionBundle(proposal=proposal, decision=decision)
        service.route_proposal.return_value = bundle

    return service, proposal


# ---------------------------------------------------------------------------
# SwitchBoard unavailable — exit 1 with structured JSON
# ---------------------------------------------------------------------------


class TestWorkerSwitchBoardFailure:
    def _run(self, capsys, goal="Fix lint errors", raises=None):
        """Run main() with a mock service, capturing stdout."""
        from control_plane.entrypoints.worker import main as worker_main

        exc = raises or SwitchBoardUnavailableError("SwitchBoard unreachable at http://localhost:20401")
        service, proposal = _make_service(route_raises=exc)

        with patch("sys.argv", ["worker", "--goal", goal]):
            # Suppress partial artifact write (RunArtifactWriter default path)
            with patch("control_plane.execution.artifact_writer.RunArtifactWriter.write_partial"):
                code = worker_main.main(service=service)

        captured = capsys.readouterr()
        return code, captured.out, captured.err

    def test_returns_exit_code_1(self, capsys):
        code, _, _ = self._run(capsys)
        assert code == 1

    def test_stdout_is_valid_json(self, capsys):
        _, out, _ = self._run(capsys)
        data = json.loads(out)
        assert isinstance(data, dict)

    def test_error_field_set(self, capsys):
        _, out, _ = self._run(capsys)
        data = json.loads(out)
        assert data["error"] == "routing_failure"

    def test_error_type_is_routing_error(self, capsys):
        _, out, _ = self._run(capsys)
        data = json.loads(out)
        assert data["error_type"] == "routing_error"

    def test_message_contains_cause(self, capsys):
        _, out, _ = self._run(capsys)
        data = json.loads(out)
        assert "SwitchBoard" in data["message"]

    def test_proposal_id_included(self, capsys):
        _, out, _ = self._run(capsys)
        data = json.loads(out)
        assert "proposal_id" in data
        assert data["proposal_id"]

    def test_partial_run_id_included(self, capsys):
        _, out, _ = self._run(capsys)
        data = json.loads(out)
        assert "partial_run_id" in data
        assert data["partial_run_id"].startswith("partial-")

    def test_no_python_traceback_on_stdout(self, capsys):
        _, out, _ = self._run(capsys)
        assert "Traceback" not in out
        assert "Error" not in out or out.strip().startswith("{")

    def test_timeout_exception_also_handled(self, capsys):
        from control_plane.routing.client import SwitchBoardUnavailableError
        code, out, _ = self._run(capsys, raises=SwitchBoardUnavailableError("timed out"))
        assert code == 1
        data = json.loads(out)
        assert data["error_type"] == "routing_error"


# ---------------------------------------------------------------------------
# Happy path still works
# ---------------------------------------------------------------------------


class TestWorkerHappyPath:
    def test_returns_0_on_success(self, capsys):
        from control_plane.entrypoints.worker import main as worker_main

        service, _ = _make_service()
        with patch("sys.argv", ["worker", "--goal", "Fix lint errors"]):
            code = worker_main.main(service=service)

        assert code == 0

    def test_stdout_contains_proposal_key(self, capsys):
        from control_plane.entrypoints.worker import main as worker_main

        service, _ = _make_service()
        with patch("sys.argv", ["worker", "--goal", "Fix lint errors"]):
            worker_main.main(service=service)

        out = capsys.readouterr().out
        data = json.loads(out)
        assert "proposal" in data
        assert "decision" in data
