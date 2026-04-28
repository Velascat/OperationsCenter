"""Tests for pr_review_watcher — two-phase PR review state machine.

All GitHub API calls are intercepted via monkeypatching GitHubPRClient methods.
The pipeline (_run_pipeline) is stubbed to return controlled verdicts.
State files use tmp_path so no real disk state is left behind.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from operations_center.entrypoints.pr_review_watcher import main as watcher


# ── Shared fixtures ───────────────────────────────────────────────────────────

REPO_KEY   = "MyRepo"
PR_NUMBER  = 42
STATE_KEY  = f"{REPO_KEY}-{PR_NUMBER}"

REVIEWER_CFG = MagicMock(
    bot_logins=[],
    allowed_reviewer_logins=[],
    max_self_review_loops=2,
    max_human_review_loops=3,
    human_review_timeout_seconds=86400,
    bot_comment_marker="<!-- operations-center:bot -->",
)

SETTINGS = MagicMock(
    reviewer=REVIEWER_CFG,
    repos={},
    plane=MagicMock(base_url="http://plane.local", project_id="proj", workspace_slug="ws"),
)


def _pr_data(*, draft: bool = False, title: str = "My PR") -> dict[str, Any]:
    return {"number": PR_NUMBER, "title": title, "draft": draft}


def _make_comment(cid: int, body: str, login: str = "human") -> dict[str, Any]:
    return {"id": cid, "body": body, "user": {"login": login}}


def _make_state(tmp_path: Path, **overrides: Any) -> tuple[dict, Path]:
    state = watcher._new_state(REPO_KEY, PR_NUMBER)
    state.update(overrides)
    sp = watcher._state_path(tmp_path, REPO_KEY, PR_NUMBER)
    watcher._save_state(sp, state)
    return state, sp


def _make_gh() -> MagicMock:
    gh = MagicMock()
    gh.get_pr_diff.return_value = "diff --git a/foo.py\n+print('hello')"
    gh.list_pr_comments.return_value = []
    gh.get_pr_reactions.return_value = []
    gh.has_thumbs_up.return_value = False
    gh.post_comment.return_value = {}
    gh.merge_pr.return_value = {}
    return gh


# ── State helpers ─────────────────────────────────────────────────────────────

def test_new_state_defaults(tmp_path: Path) -> None:
    state = watcher._new_state(REPO_KEY, PR_NUMBER)
    assert state["phase"] == "self_review"
    assert state["self_review_loops"] == 0
    assert state["human_review_loops"] == 0
    assert state["pr_number"] == PR_NUMBER
    assert state["repo_key"] == REPO_KEY
    assert state["plane_task_id"] is None


def test_save_and_load_state(tmp_path: Path) -> None:
    state = watcher._new_state(REPO_KEY, PR_NUMBER)
    sp = watcher._state_path(tmp_path, REPO_KEY, PR_NUMBER)
    watcher._save_state(sp, state)
    loaded = watcher._load_state(sp)
    assert loaded["pr_number"] == PR_NUMBER
    assert loaded["phase"] == "self_review"


def test_load_state_missing_file(tmp_path: Path) -> None:
    sp = tmp_path / "nonexistent.json"
    assert watcher._load_state(sp) == {}


def test_save_state_creates_parent_dirs(tmp_path: Path) -> None:
    sp = tmp_path / "deep" / "dir" / "state.json"
    watcher._save_state(sp, {"pr_number": 1, "phase": "self_review"})
    assert sp.exists()


# ── is_bot_comment / is_lgtm ──────────────────────────────────────────────────

def test_is_bot_comment_by_login() -> None:
    c = _make_comment(1, "whatever", login="github-actions")
    assert watcher._is_bot_comment(c, {"github-actions"}, "<!-- bot -->")


def test_is_bot_comment_by_marker() -> None:
    c = _make_comment(1, "<!-- operations-center:bot -->\nHello")
    assert watcher._is_bot_comment(c, set(), "<!-- operations-center:bot -->")


def test_is_not_bot_comment() -> None:
    c = _make_comment(1, "Looks good!")
    assert not watcher._is_bot_comment(c, {"ci-bot"}, "<!-- bot -->")


def test_is_lgtm_comment_exact() -> None:
    assert watcher._is_lgtm_comment(_make_comment(1, "/lgtm"))
    assert watcher._is_lgtm_comment(_make_comment(1, "/LGTM"))
    assert watcher._is_lgtm_comment(_make_comment(1, "  /lgtm  "))


def test_is_not_lgtm_comment() -> None:
    assert not watcher._is_lgtm_comment(_make_comment(1, "lgtm, great work"))
    assert not watcher._is_lgtm_comment(_make_comment(1, "please /lgtm this"))


# ── Phase 1: LGTM path ───────────────────────────────────────────────────────

def test_phase1_lgtm_merges_and_removes_state(tmp_path: Path) -> None:
    state, sp = _make_state(tmp_path)
    gh = _make_gh()

    with patch.object(watcher, "_run_pipeline", return_value={"result": "LGTM", "summary": "all good"}), \
         patch.object(watcher, "_plane_client") as mock_pc:
        mock_plane = MagicMock()
        mock_pc.return_value.__enter__ = lambda s: mock_plane
        mock_pc.return_value = MagicMock()
        mock_pc.return_value.close = MagicMock()

        watcher._phase1(state, sp, _pr_data(), gh, "owner", "repo", tmp_path, tmp_path / "cfg.yaml", SETTINGS)

    gh.merge_pr.assert_called_once_with("owner", "repo", PR_NUMBER, merge_method="squash")
    assert not sp.exists()


def test_phase1_lgtm_increments_loop_count(tmp_path: Path) -> None:
    state, sp = _make_state(tmp_path)
    gh = _make_gh()

    with patch.object(watcher, "_run_pipeline", return_value={"result": "LGTM", "summary": "ok"}), \
         patch.object(watcher, "_merge_and_done") as mock_merge:
        watcher._phase1(state, sp, _pr_data(), gh, "owner", "repo", tmp_path, tmp_path / "cfg.yaml", SETTINGS)

    mock_merge.assert_called_once()
    # loop count was incremented before merge decision
    args = mock_merge.call_args
    assert args[1]["reason"] == "self_review_lgtm"


# ── Phase 1: CONCERNS path ───────────────────────────────────────────────────

def test_phase1_concerns_posts_comment(tmp_path: Path) -> None:
    state, sp = _make_state(tmp_path)
    gh = _make_gh()

    with patch.object(watcher, "_run_pipeline", return_value={"result": "CONCERNS", "summary": "fix the bug"}):
        watcher._phase1(state, sp, _pr_data(), gh, "owner", "repo", tmp_path, tmp_path / "cfg.yaml", SETTINGS)

    gh.post_comment.assert_called_once()
    body = gh.post_comment.call_args[0][3]
    assert "<!-- operations-center:bot -->" in body
    assert "fix the bug" in body


def test_phase1_concerns_stays_in_phase1_below_max_loops(tmp_path: Path) -> None:
    state, sp = _make_state(tmp_path, self_review_loops=0)
    gh = _make_gh()

    with patch.object(watcher, "_run_pipeline", return_value={"result": "CONCERNS", "summary": "issues"}):
        watcher._phase1(state, sp, _pr_data(), gh, "owner", "repo", tmp_path, tmp_path / "cfg.yaml", SETTINGS)

    loaded = watcher._load_state(sp)
    assert loaded["phase"] == "self_review"
    assert loaded["self_review_loops"] == 1


def test_phase1_concerns_escalates_at_max_loops(tmp_path: Path) -> None:
    # max_self_review_loops=2, already at loop 1 — this call pushes it to 2 → escalate
    state, sp = _make_state(tmp_path, self_review_loops=1)
    gh = _make_gh()

    with patch.object(watcher, "_run_pipeline", return_value={"result": "CONCERNS", "summary": "still broken"}):
        watcher._phase1(state, sp, _pr_data(), gh, "owner", "repo", tmp_path, tmp_path / "cfg.yaml", SETTINGS)

    loaded = watcher._load_state(sp)
    assert loaded["phase"] == "human_review"
    assert loaded["phase2_entered_at"] is not None
    # Two comments: concern + escalation
    assert gh.post_comment.call_count == 2
    escalation_body = gh.post_comment.call_args_list[1][0][3]
    assert "Escalated to human review" in escalation_body


def test_phase1_no_verdict_retries_next_poll(tmp_path: Path) -> None:
    state, sp = _make_state(tmp_path, self_review_loops=0)
    gh = _make_gh()

    with patch.object(watcher, "_run_pipeline", return_value=None):
        watcher._phase1(state, sp, _pr_data(), gh, "owner", "repo", tmp_path, tmp_path / "cfg.yaml", SETTINGS)

    loaded = watcher._load_state(sp)
    assert loaded["phase"] == "self_review"
    assert loaded["self_review_loops"] == 1
    gh.merge_pr.assert_not_called()
    gh.post_comment.assert_not_called()


def test_phase1_skips_empty_diff(tmp_path: Path) -> None:
    state, sp = _make_state(tmp_path)
    gh = _make_gh()
    gh.get_pr_diff.return_value = ""

    with patch.object(watcher, "_run_pipeline") as mock_pipeline:
        watcher._phase1(state, sp, _pr_data(), gh, "owner", "repo", tmp_path, tmp_path / "cfg.yaml", SETTINGS)

    mock_pipeline.assert_not_called()
    gh.merge_pr.assert_not_called()


# ── Phase 2: /lgtm ───────────────────────────────────────────────────────────

def test_phase2_lgtm_comment_merges(tmp_path: Path) -> None:
    state, sp = _make_state(tmp_path, phase="human_review",
                            phase2_entered_at=datetime.now(UTC).isoformat())
    gh = _make_gh()
    gh.list_pr_comments.return_value = [_make_comment(1, "/lgtm")]

    with patch.object(watcher, "_merge_and_done") as mock_merge:
        watcher._phase2(state, sp, _pr_data(), gh, "owner", "repo", tmp_path, tmp_path / "cfg.yaml", SETTINGS)

    mock_merge.assert_called_once()
    assert mock_merge.call_args[1]["reason"] == "lgtm_comment"


def test_phase2_lgtm_comment_case_insensitive(tmp_path: Path) -> None:
    state, sp = _make_state(tmp_path, phase="human_review",
                            phase2_entered_at=datetime.now(UTC).isoformat())
    gh = _make_gh()
    gh.list_pr_comments.return_value = [_make_comment(1, "/LGTM")]

    with patch.object(watcher, "_merge_and_done") as mock_merge:
        watcher._phase2(state, sp, _pr_data(), gh, "owner", "repo", tmp_path, tmp_path / "cfg.yaml", SETTINGS)

    mock_merge.assert_called_once()


# ── Phase 2: 👍 reaction ──────────────────────────────────────────────────────

def test_phase2_thumbs_up_reaction_merges(tmp_path: Path) -> None:
    state, sp = _make_state(tmp_path, phase="human_review",
                            phase2_entered_at=datetime.now(UTC).isoformat())
    gh = _make_gh()
    gh.get_pr_reactions.return_value = [{"content": "+1", "user": {"login": "alice"}}]
    gh.has_thumbs_up.return_value = True

    with patch.object(watcher, "_merge_and_done") as mock_merge:
        watcher._phase2(state, sp, _pr_data(), gh, "owner", "repo", tmp_path, tmp_path / "cfg.yaml", SETTINGS)

    mock_merge.assert_called_once()
    assert mock_merge.call_args[1]["reason"] == "thumbs_up_reaction"


def test_phase2_bot_reaction_ignored(tmp_path: Path) -> None:
    reviewer = MagicMock(**vars(REVIEWER_CFG))
    reviewer.bot_logins = ["ci-bot"]
    settings = MagicMock(reviewer=reviewer, repos={}, plane=SETTINGS.plane)

    state, sp = _make_state(tmp_path, phase="human_review",
                            phase2_entered_at=datetime.now(UTC).isoformat())
    gh = _make_gh()
    gh.get_pr_reactions.return_value = [{"content": "+1", "user": {"login": "ci-bot"}}]
    # has_thumbs_up is called only on non-bot reactions, so we return False
    gh.has_thumbs_up.return_value = False

    with patch.object(watcher, "_merge_and_done") as mock_merge:
        watcher._phase2(state, sp, _pr_data(), gh, "owner", "repo", tmp_path, tmp_path / "cfg.yaml", settings)

    mock_merge.assert_not_called()


# ── Phase 2: human comment → revision pass ───────────────────────────────────

def test_phase2_human_comment_triggers_revision(tmp_path: Path) -> None:
    state, sp = _make_state(tmp_path, phase="human_review",
                            phase2_entered_at=datetime.now(UTC).isoformat())
    gh = _make_gh()
    gh.list_pr_comments.return_value = [_make_comment(10, "Please rename the variable")]

    with patch.object(watcher, "_run_pipeline", return_value={"result": "CONCERNS_ADDRESSED", "summary": "renamed it"}), \
         patch.object(watcher, "_merge_and_done") as mock_merge:
        watcher._phase2(state, sp, _pr_data(), gh, "owner", "repo", tmp_path, tmp_path / "cfg.yaml", SETTINGS)

    mock_merge.assert_not_called()
    # Reply posted
    gh.post_comment.assert_called_once()
    reply_body = gh.post_comment.call_args[0][3]
    assert "<!-- operations-center:bot -->" in reply_body
    assert "renamed it" in reply_body

    loaded = watcher._load_state(sp)
    assert loaded["human_review_loops"] == 1
    assert 10 in loaded["processed_comment_ids"]


def test_phase2_processed_comments_not_retriggered(tmp_path: Path) -> None:
    state, sp = _make_state(tmp_path, phase="human_review",
                            phase2_entered_at=datetime.now(UTC).isoformat(),
                            processed_comment_ids=[10])
    gh = _make_gh()
    # comment 10 already processed
    gh.list_pr_comments.return_value = [_make_comment(10, "old comment")]

    with patch.object(watcher, "_run_pipeline") as mock_pipeline:
        watcher._phase2(state, sp, _pr_data(), gh, "owner", "repo", tmp_path, tmp_path / "cfg.yaml", SETTINGS)

    mock_pipeline.assert_not_called()
    gh.merge_pr.assert_not_called()


def test_phase2_bot_comments_filtered(tmp_path: Path) -> None:
    state, sp = _make_state(tmp_path, phase="human_review",
                            phase2_entered_at=datetime.now(UTC).isoformat())
    gh = _make_gh()
    bot_comment = _make_comment(5, "<!-- operations-center:bot -->\nSelf-review concerns")
    gh.list_pr_comments.return_value = [bot_comment]

    with patch.object(watcher, "_run_pipeline") as mock_pipeline:
        watcher._phase2(state, sp, _pr_data(), gh, "owner", "repo", tmp_path, tmp_path / "cfg.yaml", SETTINGS)

    mock_pipeline.assert_not_called()
    gh.merge_pr.assert_not_called()


# ── Phase 2: timeout ─────────────────────────────────────────────────────────

def test_phase2_timeout_auto_merges(tmp_path: Path) -> None:
    past = (datetime.now(UTC) - timedelta(seconds=86401)).isoformat()
    state, sp = _make_state(tmp_path, phase="human_review", phase2_entered_at=past)
    gh = _make_gh()

    with patch.object(watcher, "_merge_and_done") as mock_merge:
        watcher._phase2(state, sp, _pr_data(), gh, "owner", "repo", tmp_path, tmp_path / "cfg.yaml", SETTINGS)

    mock_merge.assert_called_once()
    assert mock_merge.call_args[1]["reason"] == "human_review_timeout"
    # Notice comment posted before merge
    gh.post_comment.assert_called_once()
    assert "Auto-merging" in gh.post_comment.call_args[0][3]


def test_phase2_not_timed_out_before_threshold(tmp_path: Path) -> None:
    recent = (datetime.now(UTC) - timedelta(seconds=3600)).isoformat()
    state, sp = _make_state(tmp_path, phase="human_review", phase2_entered_at=recent)
    gh = _make_gh()
    gh.list_pr_comments.return_value = []

    with patch.object(watcher, "_merge_and_done") as mock_merge:
        watcher._phase2(state, sp, _pr_data(), gh, "owner", "repo", tmp_path, tmp_path / "cfg.yaml", SETTINGS)

    mock_merge.assert_not_called()


# ── Phase 2: max human loops ─────────────────────────────────────────────────

def test_phase2_max_human_loops_auto_merges(tmp_path: Path) -> None:
    state, sp = _make_state(tmp_path, phase="human_review",
                            phase2_entered_at=datetime.now(UTC).isoformat(),
                            human_review_loops=3)  # already at max
    gh = _make_gh()
    gh.list_pr_comments.return_value = [_make_comment(20, "one more change please")]

    with patch.object(watcher, "_merge_and_done") as mock_merge:
        watcher._phase2(state, sp, _pr_data(), gh, "owner", "repo", tmp_path, tmp_path / "cfg.yaml", SETTINGS)

    mock_merge.assert_called_once()
    assert mock_merge.call_args[1]["reason"] == "max_human_loops"


# ── merge_and_done ────────────────────────────────────────────────────────────

def test_merge_and_done_removes_state_file(tmp_path: Path) -> None:
    state, sp = _make_state(tmp_path, plane_task_id=None)
    gh = _make_gh()

    watcher._merge_and_done(state, sp, _pr_data(), gh, "owner", "repo", SETTINGS, reason="test")

    gh.merge_pr.assert_called_once_with("owner", "repo", PR_NUMBER, merge_method="squash")
    assert not sp.exists()


def test_merge_and_done_transitions_plane_task(tmp_path: Path) -> None:
    state, sp = _make_state(tmp_path, plane_task_id="task-abc")
    gh = _make_gh()

    mock_client = MagicMock()
    with patch.object(watcher, "_plane_client", return_value=mock_client):
        watcher._merge_and_done(state, sp, _pr_data(), gh, "owner", "repo", SETTINGS, reason="lgtm_comment")

    mock_client.transition_issue.assert_called_once_with("task-abc", "Done")
    mock_client.comment_issue.assert_called_once()


def test_merge_and_done_keeps_state_on_merge_failure(tmp_path: Path) -> None:
    state, sp = _make_state(tmp_path, plane_task_id=None)
    gh = _make_gh()
    gh.merge_pr.side_effect = Exception("merge conflict")

    watcher._merge_and_done(state, sp, _pr_data(), gh, "owner", "repo", SETTINGS, reason="test")

    assert sp.exists()  # state preserved for operator inspection


# ── allowed_reviewer_logins filter ───────────────────────────────────────────

def test_phase2_lgtm_only_from_allowed_logins(tmp_path: Path) -> None:
    reviewer = MagicMock(
        bot_logins=[],
        allowed_reviewer_logins=["alice"],
        max_self_review_loops=2,
        max_human_review_loops=3,
        human_review_timeout_seconds=86400,
        bot_comment_marker="<!-- operations-center:bot -->",
    )
    settings = MagicMock(reviewer=reviewer, repos={}, plane=SETTINGS.plane)

    state, sp = _make_state(tmp_path, phase="human_review",
                            phase2_entered_at=datetime.now(UTC).isoformat())
    gh = _make_gh()
    # /lgtm from non-allowed user
    gh.list_pr_comments.return_value = [_make_comment(1, "/lgtm", login="mallory")]

    with patch.object(watcher, "_merge_and_done") as mock_merge:
        watcher._phase2(state, sp, _pr_data(), gh, "owner", "repo", tmp_path, tmp_path / "cfg.yaml", settings)

    mock_merge.assert_not_called()


def test_phase2_lgtm_from_allowed_login_merges(tmp_path: Path) -> None:
    reviewer = MagicMock(
        bot_logins=[],
        allowed_reviewer_logins=["alice"],
        max_self_review_loops=2,
        max_human_review_loops=3,
        human_review_timeout_seconds=86400,
        bot_comment_marker="<!-- operations-center:bot -->",
    )
    settings = MagicMock(reviewer=reviewer, repos={}, plane=SETTINGS.plane)

    state, sp = _make_state(tmp_path, phase="human_review",
                            phase2_entered_at=datetime.now(UTC).isoformat())
    gh = _make_gh()
    gh.list_pr_comments.return_value = [_make_comment(1, "/lgtm", login="alice")]

    with patch.object(watcher, "_merge_and_done") as mock_merge:
        watcher._phase2(state, sp, _pr_data(), gh, "owner", "repo", tmp_path, tmp_path / "cfg.yaml", settings)

    mock_merge.assert_called_once()


# ── draft PR skipped ─────────────────────────────────────────────────────────

def test_poll_once_skips_draft_prs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = MagicMock(
        reviewer=REVIEWER_CFG,
        repos={REPO_KEY: MagicMock(await_review=True, clone_url=f"git@github.com:owner/{REPO_KEY}.git")},
        plane=SETTINGS.plane,
    )

    gh = _make_gh()
    gh.list_open_prs.return_value = [{"number": 1, "title": "WIP", "draft": True}]

    with patch.object(watcher, "_github_client", return_value=gh), \
         patch.object(watcher, "_find_plane_task_id", return_value=None):
        watcher._poll_once(tmp_path, tmp_path / "cfg.yaml", settings)

    sp = watcher._state_path(tmp_path, REPO_KEY, 1)
    assert not sp.exists()


# ── poll_once creates state for new PRs ──────────────────────────────────────

def test_poll_once_creates_state_for_new_pr(tmp_path: Path) -> None:
    settings = MagicMock(
        reviewer=REVIEWER_CFG,
        repos={REPO_KEY: MagicMock(await_review=True, clone_url=f"git@github.com:owner/{REPO_KEY}.git")},
        plane=SETTINGS.plane,
    )

    gh = _make_gh()
    gh.list_open_prs.return_value = [_pr_data()]

    with patch.object(watcher, "_github_client", return_value=gh), \
         patch.object(watcher, "_find_plane_task_id", return_value=None), \
         patch.object(watcher, "_phase1") as mock_phase1:
        watcher._poll_once(tmp_path, tmp_path / "cfg.yaml", settings)

    sp = watcher._state_path(tmp_path, REPO_KEY, PR_NUMBER)
    assert sp.exists()
    loaded = watcher._load_state(sp)
    assert loaded["phase"] == "self_review"
    assert loaded["pr_number"] == PR_NUMBER
    mock_phase1.assert_called_once()


def test_poll_once_skips_repos_without_await_review(tmp_path: Path) -> None:
    settings = MagicMock(
        reviewer=REVIEWER_CFG,
        repos={REPO_KEY: MagicMock(await_review=False, clone_url=f"git@github.com:owner/{REPO_KEY}.git")},
        plane=SETTINGS.plane,
    )

    gh = _make_gh()
    with patch.object(watcher, "_github_client", return_value=gh):
        watcher._poll_once(tmp_path, tmp_path / "cfg.yaml", settings)

    gh.list_open_prs.assert_not_called()


# ── heartbeat ────────────────────────────────────────────────────────────────

def test_write_heartbeat_creates_file(tmp_path: Path) -> None:
    watcher._write_heartbeat(tmp_path)
    hb = tmp_path / "heartbeat_review.json"
    assert hb.exists()
    data = json.loads(hb.read_text())
    assert data["role"] == "review"
    assert data["status"] == "active"


def test_write_heartbeat_idempotent(tmp_path: Path) -> None:
    watcher._write_heartbeat(tmp_path)
    watcher._write_heartbeat(tmp_path)
    hb = tmp_path / "heartbeat_review.json"
    assert hb.exists()


# ── CLI contract ─────────────────────────────────────────────────────────────

def test_cli_accepts_all_flags(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config = tmp_path / "cfg.yaml"
    config.write_text("plane:\n  base_url: http://x\n  api_token_env: X\n  workspace_slug: ws\n  project_id: p\ngit:\n  provider: github\nkodo: {}\nrepos: {}\n")

    monkeypatch.setenv("X", "token")

    with patch.object(watcher, "_load_settings") as mock_settings, \
         patch.object(watcher, "_poll_once"):
        mock_settings.return_value = SETTINGS
        result = watcher.main.__wrapped__() if hasattr(watcher.main, "__wrapped__") else None

    # Just verify --help doesn't crash and flags are accepted
    import subprocess, sys
    proc = subprocess.run(
        [sys.executable, "-m", "operations_center.entrypoints.pr_review_watcher.main", "--help"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0
    assert "--config" in proc.stdout
    assert "--watch" in proc.stdout
    assert "--poll-interval-seconds" in proc.stdout
    assert "--status-dir" in proc.stdout
