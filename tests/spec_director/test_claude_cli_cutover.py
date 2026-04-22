from __future__ import annotations

from unittest.mock import patch

from control_plane.spec_director._claude_cli import call_claude


def test_call_claude_ignores_switchboard_url_after_cutover(monkeypatch) -> None:
    monkeypatch.setenv("SWITCHBOARD_URL", "http://localhost:20401")

    with patch("control_plane.spec_director._claude_cli._call_claude_cli", return_value="from-cli") as mock_cli:
        result = call_claude("hello", system_prompt="be concise")

    assert result == "from-cli"
    mock_cli.assert_called_once_with(
        "hello",
        system_prompt="be concise",
        model="claude-sonnet-4-6",
        timeout=300,
    )

