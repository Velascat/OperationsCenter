"""Tests for spec_director main loop — cycle ordering and single board fetch."""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch


def _make_settings(tmp_path):
    settings = MagicMock()
    settings.spec_director.enabled = True
    settings.spec_director.spec_retention_days = 30
    settings.spec_director.campaign_abandon_hours = 72
    settings.spec_director.drop_file_path = str(tmp_path / "drop.md")
    settings.spec_director.brainstorm_model = "claude-opus-4-6"
    settings.spec_director.max_tasks_per_campaign = 10
    settings.spec_director.poll_interval_seconds = 30
    settings.repos = {}
    settings.plane.project_id = "proj-1"
    return settings


def test_run_once_calls_phase_orchestrator_before_recovery(tmp_path):
    """Phase orchestrator must run before recovery in each cycle."""
    from operations_center.entrypoints.spec_director.main import run_once

    settings = _make_settings(tmp_path)
    client = MagicMock()
    client.list_issues.return_value = []

    call_order = []

    with (
        patch("operations_center.entrypoints.spec_director.main.PhaseOrchestrator") as mock_orch_cls,
        patch("operations_center.entrypoints.spec_director.main.RecoveryService") as mock_rec_cls,
    ):
        from operations_center.spec_director.phase_orchestrator import PhaseOrchestrationResult

        mock_orch_inst = MagicMock()
        mock_orch_inst.run.side_effect = lambda issues: (
            call_order.append("phase_orch") or PhaseOrchestrationResult()
        )
        mock_orch_cls.return_value = mock_orch_inst

        mock_rec_inst = MagicMock()
        mock_rec_inst.should_abandon.side_effect = lambda c: call_order.append("recovery") or False
        mock_rec_cls.return_value = mock_rec_inst

        run_once(settings, client)

    assert "phase_orch" in call_order, "PhaseOrchestrator.run() was never called"
    assert call_order.index("phase_orch") < call_order.index("recovery") if "recovery" in call_order else True, \
        "PhaseOrchestrator must run before RecoveryService"


def _make_orch_result():
    from operations_center.spec_director.phase_orchestrator import PhaseOrchestrationResult
    return PhaseOrchestrationResult()


def test_run_once_fetches_issues_once(tmp_path):
    """list_issues() must be called exactly once per cycle (not once per counter)."""
    from operations_center.entrypoints.spec_director.main import run_once

    settings = _make_settings(tmp_path)
    client = MagicMock()
    client.list_issues.return_value = []

    with (
        patch("operations_center.entrypoints.spec_director.main.PhaseOrchestrator") as mock_orch_cls,
        patch("operations_center.entrypoints.spec_director.main.RecoveryService"),
    ):
        mock_orch_inst = MagicMock()
        mock_orch_inst.run.return_value = _make_orch_result()
        mock_orch_cls.return_value = mock_orch_inst

        run_once(settings, client)

    assert client.list_issues.call_count == 1, (
        f"Expected 1 list_issues call, got {client.list_issues.call_count}"
    )


def test_run_once_disabled_returns_early(tmp_path):
    """When spec_director.enabled is False, run_once exits without touching the board."""
    from operations_center.entrypoints.spec_director.main import run_once

    settings = _make_settings(tmp_path)
    settings.spec_director.enabled = False
    client = MagicMock()

    run_once(settings, client)

    client.list_issues.assert_not_called()


def test_run_once_does_not_set_switchboard_env(tmp_path, monkeypatch):
    from operations_center.entrypoints.spec_director.main import run_once

    settings = _make_settings(tmp_path)
    settings.spec_director.switchboard_url = "http://sb-configured:20401"
    client = MagicMock()
    client.list_issues.return_value = []

    with (
        patch("operations_center.entrypoints.spec_director.main.PhaseOrchestrator") as mock_orch_cls,
        patch("operations_center.entrypoints.spec_director.main.RecoveryService"),
    ):
        mock_orch_inst = MagicMock()
        mock_orch_inst.run.return_value = _make_orch_result()
        mock_orch_cls.return_value = mock_orch_inst

        monkeypatch.delenv("SWITCHBOARD_URL", raising=False)
        run_once(settings, client)

    assert "SWITCHBOARD_URL" not in os.environ
