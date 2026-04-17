"""Verify that spec-campaign-sourced Backlog tasks are promoted by promote_backlog_tasks."""
from __future__ import annotations

from unittest.mock import MagicMock

from control_plane.entrypoints.worker.main import promote_backlog_tasks


def _make_issue(*, task_id: str, state: str, labels: list[str]) -> dict:
    return {
        "id": task_id,
        "name": f"Task {task_id}",
        "state": {"name": state},
        "labels": [{"name": lbl} for lbl in labels],
        "description": "## Execution\nrepo: repo_a\nbase_branch: main\nmode: goal\n",
    }


def test_spec_campaign_source_task_is_promoted():
    issue = _make_issue(
        task_id="task-1",
        state="Backlog",
        labels=["task-kind: goal", "source: spec-campaign", "campaign-id: abc-123", "repo: repo_a"],
    )
    client = MagicMock()
    client.transition_issue.return_value = None
    client.comment_issue.return_value = None

    promoted = promote_backlog_tasks(client, [issue], max_promotions=5)

    assert "task-1" in promoted
    client.transition_issue.assert_called_once_with("task-1", "Ready for AI")
    client.comment_issue.assert_called_once()


def test_unknown_source_task_is_not_promoted():
    """A task with an unrecognized source label is not auto-promoted."""
    issue = _make_issue(
        task_id="task-2",
        state="Backlog",
        labels=["task-kind: goal", "source: some-unknown-source", "repo: repo_a"],
    )
    client = MagicMock()
    promoted = promote_backlog_tasks(client, [issue], max_promotions=5)
    assert promoted == []
    client.transition_issue.assert_not_called()


def test_suppressor_reads_area_keywords_from_spec_file(tmp_path):
    """Suppressor must work even when CampaignRecord has no area_keywords field."""
    from control_plane.spec_director.suppressor import is_suppressed
    from control_plane.spec_director.models import CampaignRecord

    specs_dir = tmp_path / "docs" / "specs"
    specs_dir.mkdir(parents=True)
    spec_path = specs_dir / "my-slug.md"
    spec_path.write_text("""\
---
campaign_id: test-uuid-1234
slug: my-slug
phases:
  - implement
repos:
  - repo_a
area_keywords:
  - src/auth/
  - authentication
status: active
created_at: 2026-01-01T00:00:00
---
## Overview
A test spec.
""")

    record = CampaignRecord(
        campaign_id="test-uuid-1234",
        slug="my-slug",
        spec_file=str(spec_path),
        status="active",
        created_at="2026-01-01T00:00:00",
    )

    suppressed = is_suppressed(
        proposal_title="Refactor authentication module",
        proposal_paths=["src/auth/login.py"],
        active_campaigns=[record],
        specs_dir=specs_dir,
    )
    assert suppressed is True


def test_suppressor_not_suppressed_when_no_keyword_match(tmp_path):
    from control_plane.spec_director.suppressor import is_suppressed
    from control_plane.spec_director.models import CampaignRecord

    specs_dir = tmp_path / "docs" / "specs"
    specs_dir.mkdir(parents=True)
    spec_path = specs_dir / "my-slug.md"
    spec_path.write_text("""\
---
campaign_id: test-uuid-1234
slug: my-slug
phases: [implement]
repos: [repo_a]
area_keywords:
  - src/auth/
status: active
created_at: 2026-01-01T00:00:00
---
""")

    record = CampaignRecord(
        campaign_id="test-uuid-1234",
        slug="my-slug",
        spec_file=str(spec_path),
        status="active",
        created_at="2026-01-01T00:00:00",
    )

    suppressed = is_suppressed(
        proposal_title="Add logging to database queries",
        proposal_paths=["src/db/queries.py"],
        active_campaigns=[record],
        specs_dir=specs_dir,
    )
    assert suppressed is False


def test_context_bundle_has_no_insight_snapshot():
    from control_plane.spec_director.context_bundle import ContextBundle
    bundle = ContextBundle(
        git_logs={},
        specs_index=[],
        recent_done_tasks=[],
        recent_cancelled_tasks=[],
        open_task_count=0,
        seed_text="",
        available_repos=[],
    )
    assert not hasattr(bundle, "insight_snapshot")


def test_context_bundle_build_includes_board_signals():
    from control_plane.spec_director.context_bundle import ContextBundleBuilder
    builder = ContextBundleBuilder()
    board_issues = [
        {"name": "Fix login bug", "state": {"name": "Done"}, "updated_at": "2026-04-10T00:00:00Z"},
        {"name": "Add tests", "state": {"name": "Cancelled"}, "updated_at": "2026-04-11T00:00:00Z"},
        {"name": "Refactor DB", "state": {"name": "Ready for AI"}, "updated_at": "2026-04-12T00:00:00Z"},
    ]
    bundle = builder.build(
        seed_text="",
        board_issues=board_issues,
        specs_index=[],
        git_logs={},
        available_repos=["repo_a", "repo_b"],
    )
    assert any(t["name"] == "Fix login bug" for t in bundle.recent_done_tasks)
    assert any(t["name"] == "Add tests" for t in bundle.recent_cancelled_tasks)
    assert bundle.open_task_count == 1
    assert bundle.available_repos == ["repo_a", "repo_b"]
    assert not hasattr(bundle, "insight_snapshot")
