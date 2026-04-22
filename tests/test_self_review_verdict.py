"""Tests for the self-review verdict parsing logic in ExecutionService.run_self_review_pass."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from control_plane.legacy_execution.service import ExecutionService, _SelfReviewVerdict


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_service_with_mocks(tmp_path):
    """Create an ExecutionService with __init__ bypassed and all collaborators mocked."""
    with patch.object(ExecutionService, "__init__", lambda self, _s: None):
        svc = ExecutionService(MagicMock())

    svc.logger = MagicMock()
    svc.git = MagicMock()
    svc.kodo = MagicMock()
    svc.workspace = MagicMock()

    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    repo_path = workspace_path / "repo"
    repo_path.mkdir()

    svc.workspace.create.return_value = workspace_path
    svc.workspace.cleanup = MagicMock()
    svc.git.clone.return_value = repo_path
    svc.git.add_local_exclude = MagicMock()
    svc.git.checkout_base = MagicMock()
    svc.git.try_merge_base.return_value = (True, [])

    return svc, repo_path


# ---------------------------------------------------------------------------
# Test: fuzzy fallback — LGTM in body but not first line → verdict="lgtm"
# ---------------------------------------------------------------------------


def test_self_review_fuzzy_fallback_lgtm(tmp_path):
    """When LGTM appears in body (not first line) and no CONCERN, fuzzy fallback yields 'lgtm'."""
    svc, repo_path = _make_service_with_mocks(tmp_path)

    # Make kodo.run write the verdict file with LGTM not on first line
    def fake_kodo_run(goal_file, rp):
        verdict_file = rp / ".review" / "verdict.txt"
        verdict_file.parent.mkdir(exist_ok=True)
        verdict_file.write_text("Everything looks great\nThe code is LGTM worthy\n")

    svc.kodo.run.side_effect = fake_kodo_run

    verdict = svc.run_self_review_pass(
        repo_key="org/repo",
        clone_url="https://github.com/org/repo.git",
        branch="plane/task-1-fix",
        base_branch="main",
        original_goal="Fix the bug",
        task_id="task-1",
    )

    assert isinstance(verdict, _SelfReviewVerdict)
    assert verdict.verdict == "lgtm"
    assert verdict.concerns == []
    svc.logger.warning.assert_called()
    # Verify fuzzy fallback was logged
    warning_calls = [str(c) for c in svc.logger.warning.call_args_list]
    assert any("fuzzy fallback" in w for w in warning_calls)


# ---------------------------------------------------------------------------
# Test: fuzzy fallback — CONCERN present → verdict="concerns"
# ---------------------------------------------------------------------------


def test_self_review_fuzzy_fallback_concerns(tmp_path):
    """When first line isn't LGTM/CONCERNS but body contains CONCERN, fuzzy fallback yields 'concerns'."""
    svc, repo_path = _make_service_with_mocks(tmp_path)

    concern_text = "Overall review: some CONCERN about error handling and LGTM otherwise"

    def fake_kodo_run(goal_file, rp):
        verdict_file = rp / ".review" / "verdict.txt"
        verdict_file.parent.mkdir(exist_ok=True)
        verdict_file.write_text(concern_text)

    svc.kodo.run.side_effect = fake_kodo_run

    verdict = svc.run_self_review_pass(
        repo_key="org/repo",
        clone_url="https://github.com/org/repo.git",
        branch="plane/task-2-fix",
        base_branch="main",
        original_goal="Improve error handling",
        task_id="task-2",
    )

    assert isinstance(verdict, _SelfReviewVerdict)
    assert verdict.verdict == "concerns"
    # The concerns list should contain the raw content (up to 500 chars)
    assert len(verdict.concerns) == 1
    assert verdict.concerns[0] == concern_text[:500]
