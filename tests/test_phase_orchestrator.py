# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for PhaseOrchestrator — phase advancement and blocked-task unblocking."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from operations_center.spec_director.phase_orchestrator import PhaseOrchestrator
from operations_center.spec_director.models import CampaignRecord
from operations_center.spec_director.state import CampaignStateManager


_CAMPAIGN_ID = "test-campaign-uuid"


def _make_issue(
    *,
    task_id: str,
    name: str,
    state: str,
    kind: str,
    campaign_id: str = _CAMPAIGN_ID,
) -> dict:
    labels = [
        {"name": f"task-kind: {kind}"},
        {"name": "source: spec-campaign"},
        {"name": f"campaign-id: {campaign_id}"},
    ]
    return {
        "id": task_id,
        "name": name,
        "state": {"name": state},
        "labels": labels,
        "description": (
            f"## Execution\nrepo: repo_a\nbase_branch: main\nmode: {kind}\n"
            f"spec_campaign_id: {campaign_id}\nspec_file: docs/specs/my-slug.md\n"
            f"task_phase: {'implement' if kind == 'goal' else kind}\n"
        ),
    }


def _make_parent(*, campaign_id: str = _CAMPAIGN_ID) -> dict:
    return {
        "id": "parent-1",
        "name": "[Campaign] my-slug",
        "state": {"name": "Running"},
        "labels": [
            {"name": "source: spec-campaign"},
            {"name": f"campaign-id: {campaign_id}"},
        ],
    }


def _make_orchestrator(tmp_path: Path) -> tuple[PhaseOrchestrator, MagicMock]:
    client = MagicMock()
    client.transition_issue.return_value = None
    client.comment_issue.return_value = None
    client.update_issue_description.return_value = None
    client.list_issue_comments.return_value = []

    state_path = tmp_path / "active.json"
    state_mgr = CampaignStateManager(state_path=state_path)
    record = CampaignRecord(
        campaign_id=_CAMPAIGN_ID,
        slug="my-slug",
        spec_file="docs/specs/my-slug.md",
        status="active",
        created_at="2026-01-01T00:00:00",
    )
    state_mgr.add_campaign(record)

    orch = PhaseOrchestrator(
        client=client,
        state_manager=state_mgr,
        specs_dir=tmp_path / "docs" / "specs",
    )
    return orch, client


def test_advances_to_test_when_all_implement_done(tmp_path):
    orch, client = _make_orchestrator(tmp_path)
    issues = [
        _make_parent(),
        _make_issue(task_id="impl-1", name="[Impl] Goal 1", state="Done", kind="goal"),
        _make_issue(task_id="test-1", name="[Test] Goal 1", state="Backlog", kind="test_campaign"),
    ]
    result = orch.run(issues)
    assert result.phases_advanced == 1
    client.transition_issue.assert_any_call("test-1", "Ready for AI")
    # Parent should receive advancement comment
    comment_calls = [str(c) for c in client.comment_issue.call_args_list]
    assert any("Advancing to test phase" in c for c in comment_calls)


def test_does_not_advance_if_implement_blocked(tmp_path):
    orch, client = _make_orchestrator(tmp_path)
    blocked_issue = _make_issue(
        task_id="impl-1", name="[Impl] Goal 1", state="Blocked", kind="goal"
    )
    client.fetch_issue.return_value = dict(blocked_issue)
    issues = [
        _make_parent(),
        blocked_issue,
        _make_issue(task_id="test-1", name="[Test] Goal 1", state="Backlog", kind="test_campaign"),
    ]
    with patch("operations_center.spec_director.phase_orchestrator.call_claude") as mock_claude:
        mock_claude.return_value = (
            "## Execution\nrepo: repo_a\nbase_branch: main\nmode: goal\n"
            "spec_campaign_id: test-campaign-uuid\nspec_file: docs/specs/my-slug.md\n"
            "task_phase: implement\nblock_rewrite_count: 1\n\n## Goal\nRewritten.\n"
        )
        orch.run(issues)

    # test-1 must NOT be transitioned to Ready for AI (implement is blocked, not terminal)
    transition_calls = [str(c) for c in client.transition_issue.call_args_list]
    assert not any("test-1" in c and "Ready for AI" in c for c in transition_calls)


def test_advances_to_improve_when_all_test_done(tmp_path):
    orch, client = _make_orchestrator(tmp_path)
    issues = [
        _make_parent(),
        _make_issue(task_id="impl-1", name="[Impl] Goal 1", state="Done", kind="goal"),
        _make_issue(task_id="test-1", name="[Test] Goal 1", state="Done", kind="test_campaign"),
        _make_issue(task_id="imp-1", name="[Improve] Goal 1", state="Backlog", kind="improve_campaign"),
    ]
    result = orch.run(issues)
    assert result.phases_advanced >= 1
    client.transition_issue.assert_any_call("imp-1", "Ready for AI")


def test_completes_campaign_when_all_phases_terminal(tmp_path):
    orch, client = _make_orchestrator(tmp_path)
    state_mgr = orch._state

    issues = [
        _make_parent(),
        _make_issue(task_id="impl-1", name="[Impl] Goal 1", state="Done", kind="goal"),
        _make_issue(task_id="test-1", name="[Test] Goal 1", state="Done", kind="test_campaign"),
        _make_issue(task_id="imp-1", name="[Improve] Goal 1", state="Cancelled", kind="improve_campaign"),
    ]
    result = orch.run(issues)

    assert result.campaigns_completed == 1
    client.transition_issue.assert_any_call("parent-1", "Done")
    active = state_mgr.load()
    assert not active.has_active()


def test_blocked_task_rewritten_and_requeued(tmp_path):
    orch, client = _make_orchestrator(tmp_path)
    blocked_issue = _make_issue(
        task_id="impl-1", name="[Impl] Goal 1", state="Blocked", kind="goal"
    )
    client.fetch_issue.return_value = dict(blocked_issue)
    client.list_issue_comments.return_value = [{"body": "Tests failed: assertion error on line 42."}]

    with patch("operations_center.spec_director.phase_orchestrator.call_claude") as mock_claude:
        mock_claude.return_value = (
            "## Execution\nrepo: repo_a\nbase_branch: main\nmode: goal\n"
            "spec_campaign_id: test-campaign-uuid\nspec_file: docs/specs/my-slug.md\n"
            "task_phase: implement\nblock_rewrite_count: 1\n\n## Goal\nRewritten goal text.\n"
        )
        result = orch.run([_make_parent(), blocked_issue])

    assert result.tasks_unblocked == 1
    client.update_issue_description.assert_called_once()
    client.transition_issue.assert_any_call("impl-1", "Ready for AI")
    # The failure comment must have been fetched to include in the rewrite prompt
    client.list_issue_comments.assert_called_once_with("impl-1")
    # The failure comment text must appear in the Claude prompt
    call_args = mock_claude.call_args[0][0]
    assert "## Failure comment" in call_args
    assert "Tests failed: assertion error on line 42." in call_args


def test_blocked_task_cancelled_after_two_rewrites(tmp_path):
    orch, client = _make_orchestrator(tmp_path)
    blocked_issue = _make_issue(
        task_id="impl-1", name="[Impl] Goal 1", state="Blocked", kind="goal"
    )
    full_desc = blocked_issue["description"] + "block_rewrite_count: 2\n"
    client.fetch_issue.return_value = dict(blocked_issue, description=full_desc)
    client.list_issue_comments.return_value = [{"body": "Build failed: missing module."}]

    result = orch.run([_make_parent(), blocked_issue])

    assert result.tasks_cancelled == 1
    client.transition_issue.assert_any_call("impl-1", "Cancelled")
    # Cancel comment must include the reason from the last failure comment
    comment_calls = client.comment_issue.call_args_list
    cancel_comment = next(
        (str(c) for c in comment_calls if "cancelled" in str(c).lower()),
        None,
    )
    assert cancel_comment is not None
    assert "Build failed: missing module." in cancel_comment


def test_no_action_when_no_active_campaigns(tmp_path):
    client = MagicMock()
    state_path = tmp_path / "active.json"
    state_mgr = CampaignStateManager(state_path=state_path)
    orch = PhaseOrchestrator(
        client=client,
        state_manager=state_mgr,
        specs_dir=tmp_path / "docs" / "specs",
    )
    result = orch.run([])
    assert result.phases_advanced == 0
    assert result.campaigns_completed == 0
    client.transition_issue.assert_not_called()
