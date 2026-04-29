# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
from __future__ import annotations

import os
from unittest.mock import patch

from operations_center.spec_director._claude_cli import call_claude


def test_call_claude_ignores_switchboard_url_after_cutover(monkeypatch) -> None:
    monkeypatch.setenv("SWITCHBOARD_URL", "http://localhost:20401")

    with patch("operations_center.spec_director._claude_cli._call_claude_cli", return_value="from-cli") as mock_cli:
        result = call_claude("hello", system_prompt="be concise")

    assert result == "from-cli"
    mock_cli.assert_called_once_with(
        "hello",
        system_prompt="be concise",
        model="claude-sonnet-4-6",
        timeout=300,
    )


def test_run_once_does_not_export_switchboard_url(tmp_path, monkeypatch) -> None:
    from unittest.mock import MagicMock, patch

    from operations_center.entrypoints.spec_director.main import run_once
    from operations_center.spec_director.phase_orchestrator import PhaseOrchestrationResult

    settings = MagicMock()
    settings.spec_director.enabled = True
    settings.spec_director.switchboard_url = "http://sb-configured:20401"
    settings.spec_director.spec_retention_days = 30
    settings.spec_director.campaign_abandon_hours = 72
    settings.spec_director.drop_file_path = str(tmp_path / "drop.md")
    settings.repos = {}

    client = MagicMock()
    client.list_issues.return_value = []
    monkeypatch.delenv("SWITCHBOARD_URL", raising=False)

    with (
        patch("operations_center.entrypoints.spec_director.main.PhaseOrchestrator") as mock_orch_cls,
        patch("operations_center.entrypoints.spec_director.main.RecoveryService"),
    ):
        mock_orch_cls.return_value.run.return_value = PhaseOrchestrationResult()
        run_once(settings, client)

    assert "SWITCHBOARD_URL" not in os.environ
