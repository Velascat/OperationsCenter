"""Tests for the reviewer entrypoint (_merge_and_finalize)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from control_plane.entrypoints.reviewer.main import _merge_and_finalize


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeGitHubPRClient:
    """Minimal fake GitHubPRClient that records calls."""

    def __init__(self, pr_data: dict) -> None:
        self._pr_data = pr_data
        self.merge_pr_calls: list[tuple] = []
        self.delete_branch_calls: list[tuple] = []

    def get_pr(self, owner: str, repo: str, pr_number: int) -> dict:
        return self._pr_data

    def merge_pr(self, owner: str, repo: str, pr_number: int, **kwargs) -> dict:
        self.merge_pr_calls.append((owner, repo, pr_number))
        return {}

    def delete_branch(self, owner: str, repo: str, branch: str) -> None:
        self.delete_branch_calls.append((owner, repo, branch))


class FakeReviewerPlaneClient:
    """Minimal fake PlaneClient that records transitions and comments."""

    def __init__(self) -> None:
        self.transitions: list[tuple[str, str]] = []
        self.comments: list[tuple[str, str]] = []

    def transition_issue(self, task_id: str, state: str) -> None:
        self.transitions.append((task_id, state))

    def comment_issue(self, task_id: str, comment_markdown: str) -> None:
        self.comments.append((task_id, comment_markdown))


# ---------------------------------------------------------------------------
# Test: PR already merged/closed — should not call merge_pr
# ---------------------------------------------------------------------------


def test_merge_and_finalize_pr_already_merged(tmp_path: Path) -> None:
    """When get_pr returns merged+closed, skip merge_pr, clean up state, and transition to Done."""
    gh = FakeGitHubPRClient({"state": "closed", "merged": True})
    plane = FakeReviewerPlaneClient()
    logger = logging.getLogger("test_reviewer_merged")

    state_file = tmp_path / "task-abc.json"
    state = {
        "owner": "org",
        "repo": "myrepo",
        "pr_number": 42,
        "branch": "plane/task-abc-fix",
        "task_id": "task-abc",
        "pr_url": "https://github.com/org/myrepo/pull/42",
    }
    state_file.write_text(json.dumps(state))

    _merge_and_finalize(
        gh=gh,
        state=state,
        state_file=state_file,
        plane_client=plane,
        logger=logger,
        reason="auto-merge timeout",
    )

    # merge_pr should NOT have been called
    assert gh.merge_pr_calls == []
    # state file should be removed
    assert not state_file.exists()
    # transition_issue should have been called with "Done"
    assert ("task-abc", "Done") in plane.transitions
    # A comment should have been posted
    assert len(plane.comments) == 1
    assert "already merged" in plane.comments[0][1]
