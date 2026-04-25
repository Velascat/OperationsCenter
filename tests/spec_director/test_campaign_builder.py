# tests/spec_director/test_campaign_builder.py
from __future__ import annotations
from unittest.mock import MagicMock

_SPEC_FM = {
    "campaign_id": "abc-123",
    "slug": "add-auth",
    "phases": ["implement", "test", "improve"],
    "repos": ["MyRepo"],
    "area_keywords": ["src/auth/"],
    "status": "active",
    "created_at": "2026-04-15T00:00:00+00:00",
}

_SPEC_TEXT = """---
campaign_id: abc-123
slug: add-auth
phases:
  - implement
  - test
  - improve
repos:
  - MyRepo
area_keywords:
  - src/auth/
status: active
created_at: 2026-04-15T00:00:00+00:00
---
# Add Auth

## Goals
1. Add JWT middleware to src/auth/middleware.py
2. Add login endpoint to src/auth/routes.py

## Constraints
- Only modify src/auth/
"""


def test_creates_parent_and_child_tasks():
    from operations_center.spec_director.campaign_builder import CampaignBuilder
    mock_client = MagicMock()
    mock_client.create_issue.return_value = {"id": "task-001"}
    builder = CampaignBuilder(client=mock_client, project_id="proj-1", max_tasks=6)
    builder.build(spec_text=_SPEC_TEXT, repo_key="MyRepo", base_branch="main")
    # parent + 2 goals × 3 phases (capped) = parent + 6 tasks
    assert mock_client.create_issue.call_count >= 3  # at minimum parent + 2 implement tasks


def test_task_limit_enforced():
    from operations_center.spec_director.campaign_builder import CampaignBuilder
    mock_client = MagicMock()
    mock_client.create_issue.return_value = {"id": "task-001"}
    builder = CampaignBuilder(client=mock_client, project_id="proj-1", max_tasks=2)
    builder.build(spec_text=_SPEC_TEXT, repo_key="MyRepo", base_branch="main")
    # parent task + max_tasks child tasks
    assert mock_client.create_issue.call_count <= 3  # parent + 2


def test_child_task_body_contains_campaign_id():
    from operations_center.spec_director.campaign_builder import CampaignBuilder
    created_bodies = []

    def capture_create(**kwargs):
        created_bodies.append(kwargs.get("description", ""))
        return {"id": f"task-{len(created_bodies)}"}

    mock_client = MagicMock()
    mock_client.create_issue.side_effect = capture_create
    builder = CampaignBuilder(client=mock_client, project_id="proj-1", max_tasks=6)
    builder.build(spec_text=_SPEC_TEXT, repo_key="MyRepo", base_branch="main")
    child_bodies = [b for b in created_bodies if "spec_campaign_id" in b]
    assert len(child_bodies) > 0
    assert "abc-123" in child_bodies[0]
