"""Tests for S8 autonomy gaps.

Coverage:
  S8-1:  Feedback loop completeness (auto-recording externally merged PRs; rejection capture)
  S8-2:  ExecutionOutcomeDeriver — Phase 4 execution feedback depth
  S8-3a: Stale task pruning (stale_autonomy_backlog_days config wiring)
  S8-3b: Semantic deduplication (_semantic_title_similarity + suppression)
  S8-4:  Goal decomposition (build_multi_step_plan exists and decomposes)
  S8-5:  Rollback on post-merge regression (create_revert_branch + auto_revert_pr)
  S8-6:  Branch divergence detection (reviewer triggers rebase on "behind" state)
  S8-7:  Quality trend tracking (QualityTrendDeriver lint/type delta detection)
  S8-8:  Runtime error ingestion (webhook receiver creates tasks; dedup logic)
  S8-9:  Auto-merge timeout control (require_explicit_approval skips timeout merge)
  S8-10: Confidence calibration infrastructure (ConfidenceCalibrationStore)
"""

from __future__ import annotations

import json
import threading
import time
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# S8-1: Feedback loop completeness
# ---------------------------------------------------------------------------

from control_plane.entrypoints.worker.main import handle_feedback_loop_scan
import control_plane.entrypoints.worker.main as _wmain_module


def _make_issue(task_id: str, status: str, labels: list[str] | None = None) -> dict:
    return {
        "id": task_id,
        "name": f"Task {task_id}",
        "state": {"name": status},
        "labels": [{"name": lbl} for lbl in (labels or [])],
        "created_at": "2025-01-01T00:00:00Z",
    }


def test_feedback_auto_records_merged_pr(tmp_path: Path) -> None:
    """handle_feedback_loop_scan writes 'merged' feedback for a Done task with a merged PR."""
    import importlib
    import control_plane.entrypoints.worker.main as wmain
    orig_feedback_dir = wmain._FEEDBACK_DIR
    wmain._FEEDBACK_DIR = tmp_path / "feedback"

    task_id = "aaaaaaaa-0001-0001-0001-000000000001"
    issue = _make_issue(task_id, "Done", ["task-kind: goal", "source: proposer"])

    mock_artifact = {
        "pull_request_url": "https://github.com/org/repo/pull/42",
        "repo_key": "myrepo",
    }

    class _MockStore:
        def get_task_artifact(self, tid):
            return mock_artifact if tid == task_id else None
        def record_proposal_outcome(self, **_kw): pass

    class _MockSettings:
        repos = {"myrepo": SimpleNamespace(clone_url="https://github.com/org/repo.git")}
        def repo_git_token(self, rk): return "tok"
        def git_token(self): return "tok"

    class _MockService:
        usage_store = _MockStore()
        settings = _MockSettings()

    class _MockClient:
        def list_issues(self): return [issue]
        def list_comments(self, tid): return []

    mock_pr = {"state": "closed", "merged": True, "merged_at": "2025-02-01T00:00:00Z"}

    with patch("control_plane.entrypoints.worker.main.GitHubPRClient") as MockGH:
        MockGH.owner_repo_from_clone_url.return_value = ("org", "repo")
        MockGH.return_value.get_pr.return_value = mock_pr

        result = handle_feedback_loop_scan(
            _MockClient(), _MockService(),
            issues=[issue], now=datetime.now(UTC)
        )

    assert task_id in result
    feedback_file = tmp_path / "feedback" / f"{task_id}.json"
    assert feedback_file.exists()
    data = json.loads(feedback_file.read_text())
    assert data["outcome"] == "merged"
    assert data["source"] == "feedback_loop_scan"

    wmain._FEEDBACK_DIR = orig_feedback_dir


def test_feedback_records_human_rejection_with_dedup_key(tmp_path: Path) -> None:
    """Cancelled autonomy tasks are captured as abandoned with their dedup_key."""
    import control_plane.entrypoints.worker.main as wmain
    orig_dir = wmain._FEEDBACK_DIR
    wmain._FEEDBACK_DIR = tmp_path / "feedback"

    task_id = "bbbbbbbb-0002-0002-0002-000000000002"
    issue = {
        "id": task_id,
        "name": "Fix lint errors in api.py",
        "state": {"name": "Cancelled"},
        "labels": [{"name": "source: autonomy"}, {"name": "task-kind: goal"}],
        "description": "## Provenance\n- proposal_dedup_key: lint_fix:api.py\n",
        "created_at": "2025-01-01T00:00:00Z",
    }

    class _Store:
        def get_task_artifact(self, _): return None
        def record_proposal_outcome(self, **_kw): pass

    class _Settings:
        repos = {}
        def repo_git_token(self, rk): return None
        def git_token(self): return None

    class _Service:
        usage_store = _Store()
        settings = _Settings()

    class _Client:
        def list_issues(self): return [issue]
        def list_comments(self, _): return []  # not stale-scan-produced

    result = handle_feedback_loop_scan(
        _Client(), _Service(), issues=[issue], now=datetime.now(UTC)
    )

    assert task_id in result
    feedback_file = tmp_path / "feedback" / f"{task_id}.json"
    assert feedback_file.exists()
    data = json.loads(feedback_file.read_text())
    assert data["outcome"] == "abandoned"
    assert "lint_fix:api.py" in data.get("dedup_key", "")

    wmain._FEEDBACK_DIR = orig_dir


# ---------------------------------------------------------------------------
# S8-2: ExecutionOutcomeDeriver (Phase 4)
# ---------------------------------------------------------------------------

from control_plane.insights.derivers.execution_outcome import ExecutionOutcomeDeriver
from control_plane.insights.normalizer import InsightNormalizer
from control_plane.observer.models import (
    RepoContextSnapshot, RepoSignalsSnapshot, RepoStateSnapshot,
    TestSignal, DependencyDriftSignal, TodoSignal,
)


def _make_snapshot(repo_name: str = "testrepo") -> RepoStateSnapshot:
    return RepoStateSnapshot(
        run_id="r1",
        observed_at=datetime.now(UTC),
        source_command="test",
        repo=RepoContextSnapshot(name=repo_name, path=Path("/tmp"), current_branch="main", is_dirty=False),
        signals=RepoSignalsSnapshot(
            test_signal=TestSignal(status="unavailable"),
            dependency_drift=DependencyDriftSignal(status="unavailable"),
            todo_signal=TodoSignal(),
        ),
    )


def test_execution_outcome_deriver_empty_when_no_artifacts(tmp_path: Path) -> None:
    normalizer = InsightNormalizer()
    deriver = ExecutionOutcomeDeriver(normalizer, artifact_root=tmp_path / "nonexistent")
    insights = deriver.derive([_make_snapshot()])
    assert insights == []


def test_execution_outcome_deriver_detects_timeout_pattern(tmp_path: Path) -> None:
    """Two timeout failures → execution_outcome/timeout_pattern insight."""
    normalizer = InsightNormalizer()
    artifact_root = tmp_path / "kodo_plane"
    artifact_root.mkdir()

    for i in range(2):
        run_dir = artifact_root / f"2025-01-01_task{i:04d}_r{i}"
        run_dir.mkdir()
        (run_dir / "request.json").write_text(json.dumps({"task": {"repo_key": "myrepo"}}))
        (run_dir / "control_outcome.json").write_text(json.dumps({
            "outcome_status": "blocked",
            "blocked_classification": "timeout",
        }))

    deriver = ExecutionOutcomeDeriver(normalizer, artifact_root=artifact_root)
    insights = deriver.derive([_make_snapshot("myrepo")])
    kinds = [i.kind for i in insights]
    assert "execution_outcome/timeout_pattern" in kinds


def test_execution_outcome_deriver_detects_validation_loop(tmp_path: Path) -> None:
    """Same task failing validation 3+ times → execution_outcome/validation_loop insight."""
    normalizer = InsightNormalizer()
    artifact_root = tmp_path / "kodo_plane"
    artifact_root.mkdir()
    task_id = "looptask"

    for i in range(3):
        run_dir = artifact_root / f"run_{i:04d}"
        run_dir.mkdir()
        (run_dir / "request.json").write_text(json.dumps({"task": {"repo_key": "repo1", "task_id": task_id}}))
        (run_dir / "control_outcome.json").write_text(json.dumps({
            "outcome_status": "blocked",
            "blocked_classification": "validation_failure",
        }))

    deriver = ExecutionOutcomeDeriver(normalizer, artifact_root=artifact_root)
    insights = deriver.derive([_make_snapshot("repo1")])
    kinds = [i.kind for i in insights]
    assert "execution_outcome/validation_loop" in kinds


# ---------------------------------------------------------------------------
# S8-3a: Stale task pruning — config wiring
# ---------------------------------------------------------------------------

def test_stale_autonomy_respects_config_days() -> None:
    """handle_stale_autonomy_task_scan uses stale_days from settings via watch loop."""
    from control_plane.entrypoints.worker.main import handle_stale_autonomy_task_scan, _STALE_AUTONOMY_TASK_DAYS

    # The default constant should match the reasonable default
    assert _STALE_AUTONOMY_TASK_DAYS > 0

    # Calling with explicit stale_days=1 should cancel a task created 2 days ago
    old_task_id = "cccccccc-0003-0003-0003-000000000003"
    old_date = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    issue = {
        "id": old_task_id,
        "name": "Old autonomy task",
        "state": {"name": "Backlog"},
        "labels": [{"name": "source: autonomy"}],
        "created_at": old_date,
    }

    cancelled = []

    class _Client:
        def list_issues(self): return [issue]
        def transition_issue(self, tid, state):
            if state == "Cancelled":
                cancelled.append(tid)
        def comment_issue(self, *_): pass

    class _Settings:
        stale_autonomy_backlog_days = 30
        repos = {}

    class _Service:
        settings = _Settings()

    handle_stale_autonomy_task_scan(
        _Client(), _Service(), now=datetime.now(UTC), stale_days=1
    )
    assert old_task_id in cancelled


# ---------------------------------------------------------------------------
# S8-3b: Semantic deduplication
# ---------------------------------------------------------------------------

from control_plane.entrypoints.worker.main import _semantic_title_similarity, _SEMANTIC_DEDUP_THRESHOLD


def test_semantic_similarity_identical_titles() -> None:
    assert _semantic_title_similarity("fix lint errors in api.py", "fix lint errors in api.py") == pytest.approx(1.0)


def test_semantic_similarity_near_duplicate() -> None:
    a = "Fix lint errors in authentication module"
    b = "Lint fix: authentication module errors"
    sim = _semantic_title_similarity(a, b)
    assert sim >= _SEMANTIC_DEDUP_THRESHOLD


def test_semantic_similarity_different_topics() -> None:
    a = "Add test coverage for payment processing"
    b = "Fix lint errors in authentication module"
    sim = _semantic_title_similarity(a, b)
    assert sim < _SEMANTIC_DEDUP_THRESHOLD


def test_semantic_dedup_suppresses_near_duplicate_proposal() -> None:
    """create_proposed_task_if_missing returns None when title is semantically similar to an existing one."""
    from control_plane.entrypoints.worker.main import create_proposed_task_if_missing, ProposalSpec

    proposal = ProposalSpec(
        task_kind="goal",
        title="Fix lint errors in authentication.py",
        goal_text="Fix lint",
        reason_summary="",
        source_signal="lint",
        confidence="medium",
        recommended_state="Backlog",
        handoff_reason="",
        dedup_key="unique-key-xyz",
    )

    # An existing task with similar (not identical) wording
    existing_names = {"lint fix for authentication.py module", "add tests"}

    class _MockClient:
        def create_issue(self, **_): return {"id": "new-id"}
        def comment_issue(self, *_): pass

    class _MockSettings:
        focus_areas: list = []
        repos = {}
        def git_token(self): return None

    class _MockService:
        settings = _MockSettings()
        usage_store = MagicMock()

    result = create_proposed_task_if_missing(
        _MockClient(), _MockService(),
        proposal=proposal,
        existing_names=existing_names,
        proposal_keys=set(),
    )
    assert result is None


# ---------------------------------------------------------------------------
# S8-4: Goal decomposition (build_multi_step_plan)
# ---------------------------------------------------------------------------

from control_plane.entrypoints.worker.main import build_multi_step_plan


def test_build_multi_step_plan_creates_subtasks() -> None:
    """A task with 'migrate' keyword triggers 3-step plan creation."""
    issue = {
        "id": "dddddddd-0004-0004-0004-000000000004",
        "name": "Migrate database from SQLite to Postgres",
        "state": {"name": "Ready for AI"},
        "labels": [{"name": "task-kind: goal"}, {"name": "repo: myrepo"}],
        "description": "## Execution\nrepo: myrepo\n## Goal\nMigrate the DB.\n",
    }

    created_tasks = []

    class _Client:
        def list_issues(self): return [issue]
        def fetch_issue(self, tid): return issue
        def create_issue(self, name, **_):
            t = {"id": f"step-{len(created_tasks)}", "name": name}
            created_tasks.append(t)
            return t
        def comment_issue(self, *_): pass
        def transition_issue(self, *_): pass

    class _Settings:
        repos = {"myrepo": SimpleNamespace(clone_url="https://github.com/org/repo.git", default_branch="main")}
        focus_areas = []

    class _Service:
        settings = _Settings()
        def parse_task(self, client, tid):
            return SimpleNamespace(goal_text="Migrate the DB.")

    result = build_multi_step_plan(_Client(), _Service(), issue["id"], issue)
    assert len(result) > 0  # at least one step task created


def test_build_multi_step_plan_skips_non_complex_task() -> None:
    """A simple lint-fix task does not trigger multi-step decomposition."""
    issue = {
        "id": "eeeeeeee-0005-0005-0005-000000000005",
        "name": "Fix lint errors in api.py",
        "state": {"name": "Ready for AI"},
        "labels": [{"name": "task-kind: goal"}, {"name": "repo: myrepo"}],
        "description": "## Execution\nrepo: myrepo\n## Goal\nFix lint.\n",
    }

    class _Client:
        def list_issues(self): return [issue]
        def fetch_issue(self, tid): return issue

    class _Settings:
        repos = {"myrepo": SimpleNamespace(clone_url="https://github.com/org/repo.git")}

    class _Service:
        settings = _Settings()

    result = build_multi_step_plan(_Client(), _Service(), issue["id"], issue)
    assert result == []


# ---------------------------------------------------------------------------
# S8-5: Rollback on post-merge regression
# ---------------------------------------------------------------------------

from control_plane.adapters.git.client import GitClient


def test_git_revert_commit_creates_branch(tmp_path: Path) -> None:
    """GitClient.revert_commit() creates a new branch and applies the revert commit."""
    repo_path = tmp_path / "repo"
    repo_path.mkdir()

    import subprocess
    subprocess.run(["git", "init"], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_path, check=True, capture_output=True)
    (repo_path / "file.txt").write_text("initial")
    subprocess.run(["git", "add", "."], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo_path, check=True, capture_output=True)
    (repo_path / "file.txt").write_text("changed")
    subprocess.run(["git", "add", "."], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "change"], cwd=repo_path, check=True, capture_output=True)

    sha_result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_path, capture_output=True, text=True)
    merge_sha = sha_result.stdout.strip()

    gc = GitClient()
    success = gc.revert_commit(repo_path, merge_sha, new_branch="revert/test")
    assert success is True

    # Should be on the revert branch
    branch_result = subprocess.run(["git", "branch", "--show-current"], cwd=repo_path, capture_output=True, text=True)
    assert branch_result.stdout.strip() == "revert/test"

    # File should be back to initial
    assert (repo_path / "file.txt").read_text() == "initial"


def test_auto_revert_pr_created_on_safe_regression(tmp_path: Path) -> None:
    """detect_post_merge_regressions creates a revert PR when safe_revert=True."""
    from control_plane.entrypoints.worker.main import detect_post_merge_regressions

    task_id = "ffffffff-0006-0006-0006-000000000006"
    pr_url = "https://github.com/org/repo/pull/99"
    merge_sha = "abc12345"

    issue = {
        "id": task_id,
        "name": "Some feature task",
        "state": {"name": "Done"},
        "labels": [{"name": "task-kind: goal"}, {"name": "repo: myrepo"}],
        "description": "",
    }

    artifact = {
        "pull_request_url": pr_url,
        "repo_key": "myrepo",
    }

    class _Store:
        def get_task_artifact(self, tid): return artifact if tid == task_id else None

    class _Settings:
        repos = {"myrepo": SimpleNamespace(
            clone_url="https://github.com/org/repo.git",
            default_branch="main",
        )}
        def git_token(self): return "tok"
        def repo_git_token(self, rk): return "tok"

    class _Service:
        usage_store = _Store()
        settings = _Settings()
        def create_revert_branch(self, **_kw): return True

    revert_pr_calls = []

    class _Client:
        def list_issues(self): return [issue]
        def create_issue(self, name, **_): return {"id": "reg-task-id", "name": name}
        def comment_issue(self, *_): pass
        def fetch_issue(self, tid): return issue
        def list_comments(self, tid): return []

    pr_data = {
        "merged": True, "merged_at": "2025-01-01T00:00:00Z",
        "merge_commit_sha": merge_sha,
        "head": {"sha": merge_sha},
        "base": {"ref": "main"},
        "state": "closed",
    }

    with patch("control_plane.entrypoints.worker.main.GitHubPRClient") as MockGH:
        mock_gh_instance = MockGH.return_value
        mock_gh_instance.get_pr.return_value = pr_data
        mock_gh_instance.get_failed_checks.return_value = ["check1: failed"]
        mock_gh_instance.get_branch_head.return_value = merge_sha  # safe revert
        mock_gh_instance.create_pr.side_effect = lambda *a, **k: revert_pr_calls.append(k) or {"html_url": "https://github.com/org/repo/pull/100"}

        result = detect_post_merge_regressions(_Client(), _Service(), issues=[issue])

    assert len(result) > 0  # regression task was created
    assert len(revert_pr_calls) > 0  # revert PR was opened


# ---------------------------------------------------------------------------
# S8-6: Branch divergence detection
# ---------------------------------------------------------------------------

def test_reviewer_triggers_rebase_on_behind_branch() -> None:
    """_process_human_review triggers rebase when mergeable_state == 'behind'."""
    from control_plane.entrypoints.reviewer.main import _process_human_review

    task_id = "11111111-0007-0007-0007-000000000007"
    state = {
        "task_id": task_id, "repo_key": "myrepo",
        "owner": "org", "repo": "repo", "pr_number": 5,
        "branch": "plane/xxx", "base": "main",
        "phase": "human_review", "created_at": datetime.now(UTC).isoformat(),
        "bot_comment_ids": [], "processed_human_comment_ids": [],
    }
    state_file = MagicMock()

    pr_data = {"state": "open", "merged": False, "mergeable_state": "behind"}

    rebase_called = []

    class _MockGH:
        def get_pr(self, *a): return pr_data
        def get_pr_reactions(self, *a): return []
        def get_comment_reactions(self, *a): return []
        def list_pr_comments(self, *a): return []
        def list_pr_review_comments(self, *a): return []

    class _MockSettings:
        repos = {"myrepo": SimpleNamespace(
            clone_url="https://github.com/org/repo.git",
            auto_merge_on_ci_green=False,
            require_explicit_approval=False,
        )}
        reviewer = SimpleNamespace(
            bot_logins=[], allowed_reviewer_logins=[],
            bot_comment_marker="<!-- controlplane:bot -->",
            auto_merge_success_rate_threshold=0.9,
        )
        def repo_git_token(self, rk): return "tok"

    class _MockService:
        settings = _MockSettings()
        usage_store = MagicMock()
        def rebase_branch(self, **_kw):
            rebase_called.append(True)
            return True

    with patch("control_plane.entrypoints.reviewer.main.GitHubPRClient", return_value=_MockGH()):
        _process_human_review(state_file, state, MagicMock(), _MockService(), MagicMock())

    assert len(rebase_called) > 0


# ---------------------------------------------------------------------------
# S8-7: Quality trend tracking
# ---------------------------------------------------------------------------

from control_plane.insights.derivers.quality_trend import QualityTrendDeriver
from control_plane.observer.models import LintSignal, TypeSignal


def _make_snapshot_with_metrics(
    lint_violations: int, type_errors: int, days_ago: int = 0
) -> RepoStateSnapshot:
    from control_plane.observer.models import RepoSignalsSnapshot
    signals = RepoSignalsSnapshot(
        test_signal=TestSignal(status="unavailable"),
        dependency_drift=DependencyDriftSignal(status="unavailable"),
        todo_signal=TodoSignal(),
        lint_signal=LintSignal(
            status="violations" if lint_violations > 0 else "clean",
            violation_count=lint_violations,
        ),
        type_signal=TypeSignal(
            status="errors" if type_errors > 0 else "clean",
            error_count=type_errors,
        ),
    )
    snap = RepoStateSnapshot(
        run_id=f"r-{days_ago}",
        observed_at=datetime.now(UTC) - timedelta(days=days_ago),
        source_command="test",
        repo=RepoContextSnapshot(name="repo", path=Path("/tmp"), current_branch="main", is_dirty=False),
        signals=signals,
    )
    return snap


def test_quality_trend_detects_lint_improving() -> None:
    """Lint violations dropping consistently → quality_trend/lint_improving."""
    normalizer = InsightNormalizer()
    deriver = QualityTrendDeriver(normalizer)
    # newest → oldest order (as derivers receive snapshots)
    snapshots = [
        _make_snapshot_with_metrics(lint_violations=5, type_errors=0, days_ago=0),
        _make_snapshot_with_metrics(lint_violations=8, type_errors=0, days_ago=1),
        _make_snapshot_with_metrics(lint_violations=12, type_errors=0, days_ago=2),
    ]
    insights = deriver.derive(snapshots)
    kinds = [i.kind for i in insights]
    assert "quality_trend/lint_improving" in kinds


def test_quality_trend_detects_lint_degrading() -> None:
    """Lint violations growing → quality_trend/lint_degrading."""
    normalizer = InsightNormalizer()
    deriver = QualityTrendDeriver(normalizer)
    snapshots = [
        _make_snapshot_with_metrics(lint_violations=20, type_errors=0, days_ago=0),
        _make_snapshot_with_metrics(lint_violations=12, type_errors=0, days_ago=1),
        _make_snapshot_with_metrics(lint_violations=5, type_errors=0, days_ago=2),
    ]
    insights = deriver.derive(snapshots)
    kinds = [i.kind for i in insights]
    assert "quality_trend/lint_degrading" in kinds


def test_quality_trend_no_insight_for_single_snapshot() -> None:
    """Need at least 3 snapshots to detect a trend."""
    normalizer = InsightNormalizer()
    deriver = QualityTrendDeriver(normalizer)
    insights = deriver.derive([_make_snapshot_with_metrics(10, 5)])
    assert insights == []


def test_quality_trend_stagnant() -> None:
    """Metrics available but no significant change → quality_trend/stagnant."""
    normalizer = InsightNormalizer()
    deriver = QualityTrendDeriver(normalizer)
    snapshots = [
        _make_snapshot_with_metrics(lint_violations=10, type_errors=5, days_ago=0),
        _make_snapshot_with_metrics(lint_violations=10, type_errors=5, days_ago=1),
        _make_snapshot_with_metrics(lint_violations=10, type_errors=5, days_ago=2),
    ]
    insights = deriver.derive(snapshots)
    kinds = [i.kind for i in insights]
    assert "quality_trend/stagnant" in kinds


# ---------------------------------------------------------------------------
# S8-8: Runtime error ingestion
# ---------------------------------------------------------------------------

from control_plane.entrypoints.error_ingest.main import _is_duplicate, _mark_created, _dedup_key


def test_error_ingest_dedup_key_is_stable() -> None:
    k1 = _dedup_key("myrepo", "NullPointerException in PaymentService")
    k2 = _dedup_key("myrepo", "NullPointerException in PaymentService")
    assert k1 == k2


def test_error_ingest_dedup_not_duplicate_before_mark(tmp_path: Path) -> None:
    import control_plane.entrypoints.error_ingest.main as eingest
    orig = eingest._DEDUP_STATE_PATH
    eingest._DEDUP_STATE_PATH = tmp_path / "dedup.json"

    key = _dedup_key("repo", "some error")
    assert not _is_duplicate(key, window_seconds=3600)

    eingest._DEDUP_STATE_PATH = orig


def test_error_ingest_dedup_is_duplicate_after_mark(tmp_path: Path) -> None:
    import control_plane.entrypoints.error_ingest.main as eingest
    orig = eingest._DEDUP_STATE_PATH
    eingest._DEDUP_STATE_PATH = tmp_path / "dedup.json"

    key = _dedup_key("repo", "repeated error")
    _mark_created(key)
    assert _is_duplicate(key, window_seconds=3600)

    eingest._DEDUP_STATE_PATH = orig


def test_error_ingest_webhook_creates_plane_task(tmp_path: Path) -> None:
    """The webhook handler creates a Plane task for a valid ingest POST."""
    import control_plane.entrypoints.error_ingest.main as eingest
    orig = eingest._DEDUP_STATE_PATH
    eingest._DEDUP_STATE_PATH = tmp_path / "dedup.json"

    created_tasks = []

    class _MockClient:
        def create_issue(self, name, **_):
            t = {"id": f"task-{len(created_tasks)}", "name": name}
            created_tasks.append(t)
            return t

    from control_plane.entrypoints.error_ingest.main import _make_webhook_handler
    handler_class = _make_webhook_handler(_MockClient(), "myrepo")

    # Simulate a POST request using a mock socket
    payload = json.dumps({
        "title": "Database connection timeout",
        "severity": "error",
        "source": "test",
    }).encode()

    req = MagicMock()
    req.headers = {"Content-Length": str(len(payload))}
    req.rfile = MagicMock()
    req.rfile.read.return_value = payload

    responses = []
    req.wfile = MagicMock()
    req.wfile.write.side_effect = responses.append
    req.send_response = MagicMock()
    req.end_headers = MagicMock()
    req.path = "/ingest"

    handler = handler_class.__new__(handler_class)
    handler.__dict__.update(req.__dict__)

    with patch.object(handler_class, "__init__", lambda *a, **k: None):
        handler.do_POST()

    assert len(created_tasks) > 0
    assert "Database connection timeout" in created_tasks[0]["name"]

    eingest._DEDUP_STATE_PATH = orig


# ---------------------------------------------------------------------------
# S8-9: Auto-merge timeout control
# ---------------------------------------------------------------------------

def test_require_explicit_approval_skips_timeout_merge() -> None:
    """When require_explicit_approval=True, _process_human_review does NOT timeout-merge."""
    from control_plane.entrypoints.reviewer.main import _process_human_review

    task_id = "22222222-0009-0009-0009-000000000009"
    # Created 2 days ago — well past the 1-day timeout
    old_created = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    state = {
        "task_id": task_id, "repo_key": "strictrepo",
        "owner": "org", "repo": "repo", "pr_number": 7,
        "branch": "plane/yyy", "base": "main",
        "phase": "human_review", "created_at": old_created,
        "bot_comment_ids": [], "processed_human_comment_ids": [],
        "pr_url": "https://github.com/org/repo/pull/7",
    }
    state_file_data = {}
    state_file = MagicMock()
    state_file.write_text.side_effect = lambda s: state_file_data.update(json.loads(s))

    merged_calls = []

    class _MockGH:
        def get_pr(self, *a):
            return {"state": "open", "merged": False, "mergeable_state": "clean"}
        def get_pr_reactions(self, *a): return []
        def get_comment_reactions(self, *a): return []
        def list_pr_comments(self, *a): return []
        def list_pr_review_comments(self, *a): return []
        def merge_pr(self, *a, **kw):
            merged_calls.append(True)
        def post_comment(self, *a, **kw): return {"id": 1}

    class _MockSettings:
        repos = {"strictrepo": SimpleNamespace(
            clone_url="https://github.com/org/repo.git",
            auto_merge_on_ci_green=False,
            require_explicit_approval=True,  # <-- explicit approval required
        )}
        reviewer = SimpleNamespace(
            bot_logins=[], allowed_reviewer_logins=[],
            bot_comment_marker="<!-- controlplane:bot -->",
            auto_merge_success_rate_threshold=0.9,
        )
        def repo_git_token(self, rk): return "tok"

    class _MockService:
        settings = _MockSettings()
        usage_store = MagicMock()

    plane_client = MagicMock()

    with patch("control_plane.entrypoints.reviewer.main.GitHubPRClient", return_value=_MockGH()):
        _process_human_review(state_file, state, plane_client, _MockService(), MagicMock())

    assert len(merged_calls) == 0  # merge must NOT be called


# ---------------------------------------------------------------------------
# S8-10: Confidence calibration infrastructure
# ---------------------------------------------------------------------------

from control_plane.tuning.calibration import ConfidenceCalibrationStore, _MIN_SAMPLE_SIZE


def test_calibration_record_and_retrieve(tmp_path: Path) -> None:
    store = ConfidenceCalibrationStore(tmp_path / "cal.json")
    store.record("lint_fix", "high", "merged")
    store.record("lint_fix", "high", "abandoned")
    rate = store.calibration_for("lint_fix", "high")
    assert rate is None  # below MIN_SAMPLE_SIZE


def test_calibration_report_after_enough_records(tmp_path: Path) -> None:
    store = ConfidenceCalibrationStore(tmp_path / "cal.json")
    for _ in range(_MIN_SAMPLE_SIZE):
        store.record("type_fix", "medium", "merged")
    records = store.report()
    assert len(records) == 1
    r = records[0]
    assert r.family == "type_fix"
    assert r.confidence == "medium"
    assert r.total == _MIN_SAMPLE_SIZE
    assert r.acceptance_rate == pytest.approx(1.0)
    assert r.expected_rate == 0.5
    assert r.calibration_ratio == pytest.approx(2.0)


def test_calibration_ignores_unknown_confidence(tmp_path: Path) -> None:
    store = ConfidenceCalibrationStore(tmp_path / "cal.json")
    store.record("lint_fix", "extreme", "merged")  # not a valid confidence label
    assert store.calibration_for("lint_fix", "extreme") is None
    assert store.report() == []


def test_calibration_detects_over_confident_family(tmp_path: Path) -> None:
    """A family with high confidence but low actual acceptance has calibration_ratio < 1.0."""
    store = ConfidenceCalibrationStore(tmp_path / "cal.json")
    for _ in range(_MIN_SAMPLE_SIZE):
        store.record("arch_promotion", "high", "abandoned")  # 0% acceptance
    records = store.report()
    assert len(records) == 1
    r = records[0]
    assert r.acceptance_rate == pytest.approx(0.0)
    assert r.calibration_ratio == pytest.approx(0.0)
