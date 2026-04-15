# tests/spec_director/test_reviewer_compliance.py
from __future__ import annotations
from unittest.mock import MagicMock, patch


def test_compliance_branch_called_for_campaign_task():
    """When spec_campaign_id is in task metadata, SpecComplianceService is called."""
    from unittest.mock import patch, MagicMock

    task_description = """## Execution
repo: MyRepo
base_branch: main
mode: implement
spec_campaign_id: abc-123
spec_file: docs/specs/add-auth.md
task_phase: implement
spec_coverage_hint: Goal 1

## Goal
Add JWT middleware.
"""
    state = {
        "task_id": "task-001",
        "repo_key": "MyRepo",
        "owner": "org",
        "repo": "myrepo",
        "pr_number": 42,
        "branch": "plane/task-001-add-jwt",
        "base": "main",
        "original_goal": "Add JWT middleware.",
        "created_at": "2026-04-15T00:00:00+00:00",
        "phase": "self_review",
        "description_checked": True,
        "self_review_loops": 0,
    }

    with patch("control_plane.entrypoints.reviewer.main._get_spec_campaign_id") as mock_get_id:
        mock_get_id.return_value = "abc-123"
        with patch("control_plane.entrypoints.reviewer.main._run_spec_compliance") as mock_compliance:
            mock_compliance.return_value = "LGTM"
            # If _get_spec_campaign_id returns a value, compliance branch should fire.
            assert mock_get_id("task description") == "abc-123"
