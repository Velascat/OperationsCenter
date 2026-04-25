from pathlib import Path

import pytest

from operations_center.adapters.git.client import GitClient, branch_allowed


class FakeGitClient(GitClient):
    def __init__(self, outputs: dict[tuple[str, ...], bytes]) -> None:
        self.outputs = outputs

    def _run(self, args: list[str], cwd: Path | None = None) -> str:  # noqa: ARG002
        return self.outputs.get(tuple(args), b"").decode("utf-8", errors="replace").strip()

    def _run_bytes(self, args: list[str], cwd: Path | None = None) -> bytes:  # noqa: ARG002
        return self.outputs.get(tuple(args), b"")


class TrackingGitClient(GitClient):
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], Path | None]] = []

    def _run(self, args: list[str], cwd: Path | None = None) -> str:
        self.calls.append((args, cwd))
        return ""


def test_changed_files_handles_rename_delete_and_untracked_output() -> None:
    client = FakeGitClient(
        {
            ("git", "diff", "--name-status", "-z", "HEAD"): (
                b"R100\x00old/path.py\x00new/path.py\x00"
                b"D\x00src/deleted.py\x00"
                b"M\x00src/main.py\x00"
            ),
            ("git", "ls-files", "--others", "--exclude-standard", "-z"): b"tmp/new_file.py\x00",
        }
    )

    assert client.changed_files(Path(".")) == [
        "new/path.py",
        "src/deleted.py",
        "src/main.py",
        "tmp/new_file.py",
    ]


def test_changed_files_returns_empty_when_no_changes() -> None:
    """changed_files returns [] when both diff and ls-files produce empty output."""
    client = FakeGitClient(
        {
            ("git", "diff", "--name-status", "-z", "HEAD"): b"",
            ("git", "ls-files", "--others", "--exclude-standard", "-z"): b"",
        }
    )
    assert client.changed_files(Path(".")) == []


def test_diff_stat_returns_empty_when_no_changes() -> None:
    """diff_stat returns empty string when no tracked changes and no untracked files."""
    client = FakeGitClient(
        {
            ("git", "diff", "--stat", "HEAD"): b"",
            ("git", "ls-files", "--others", "--exclude-standard", "-z"): b"",
        }
    )
    assert client.diff_stat(Path(".")) == ""


def test_diff_stat_includes_untracked_files() -> None:
    client = FakeGitClient(
        {
            ("git", "diff", "--stat", "HEAD"): b" src/main.py | 2 +-\n 1 file changed, 1 insertion(+), 1 deletion(-)\n",
            ("git", "ls-files", "--others", "--exclude-standard", "-z"): b"tmp/new_file.py\x00",
        }
    )

    stat = client.diff_stat(Path("."))

    assert "src/main.py | 2 +-" in stat
    assert "untracked | tmp/new_file.py" in stat


def test_changed_files_propagates_diff_error() -> None:
    client = RaisingGitClient([("git", "diff", "--name-status")])

    with pytest.raises(RuntimeError, match="simulated failure"):
        client.changed_files(Path("."))


def test_changed_files_propagates_untracked_error() -> None:
    client = RaisingGitClient([("git", "ls-files", "--others")])

    with pytest.raises(RuntimeError, match="simulated failure"):
        client.changed_files(Path("."))


def test_diff_stat_propagates_tracked_diff_error() -> None:
    client = RaisingGitClient([("git", "diff", "--stat")])

    with pytest.raises(RuntimeError, match="simulated failure"):
        client.diff_stat(Path("."))


def test_diff_stat_propagates_untracked_error() -> None:
    client = RaisingGitClient([("git", "ls-files", "--others")])

    with pytest.raises(RuntimeError, match="simulated failure"):
        client.diff_stat(Path("."))


# ---------------------------------------------------------------------------
# _parse_name_status_output
# ---------------------------------------------------------------------------

def test_parse_name_status_output_empty_input() -> None:
    client = GitClient()
    assert client._parse_name_status_output(b"") == []


def test_parse_name_status_output_simple_statuses() -> None:
    client = GitClient()
    raw = b"M\x00src/main.py\x00D\x00old.py\x00A\x00new.py\x00"
    assert client._parse_name_status_output(raw) == ["src/main.py", "old.py", "new.py"]


def test_parse_name_status_output_rename_and_copy() -> None:
    client = GitClient()
    raw = b"R100\x00old.py\x00new.py\x00C050\x00a.py\x00b.py\x00"
    assert client._parse_name_status_output(raw) == ["new.py", "b.py"]


def test_parse_name_status_output_typechange_and_unmerged_statuses() -> None:
    client = GitClient()
    raw = b"T\x00src/typechange.py\x00U\x00src/conflicted.py\x00"
    assert client._parse_name_status_output(raw) == ["src/typechange.py", "src/conflicted.py"]


def test_parse_name_status_output_truncated_input() -> None:
    client = GitClient()
    raw = b"M\x00file.py\x00D"
    assert client._parse_name_status_output(raw) == ["file.py"]


# ---------------------------------------------------------------------------
# _parse_null_delimited_paths
# ---------------------------------------------------------------------------

def test_parse_null_delimited_paths_empty_input() -> None:
    client = GitClient()
    assert client._parse_null_delimited_paths(b"") == []


def test_parse_null_delimited_paths_single_path() -> None:
    client = GitClient()
    assert client._parse_null_delimited_paths(b"foo.py\x00") == ["foo.py"]


def test_parse_null_delimited_paths_multiple_paths() -> None:
    client = GitClient()
    assert client._parse_null_delimited_paths(b"a.py\x00b.py\x00c.py\x00") == ["a.py", "b.py", "c.py"]


def test_parse_null_delimited_paths_trailing_null_no_empty_strings() -> None:
    client = GitClient()
    result = client._parse_null_delimited_paths(b"a.py\x00b.py\x00")
    assert "" not in result
    assert result == ["a.py", "b.py"]


# ---------------------------------------------------------------------------
# _normalize_repo_relative_path
# ---------------------------------------------------------------------------

def test_normalize_repo_relative_path_leading_dot_slash() -> None:
    client = GitClient()
    assert client._normalize_repo_relative_path("./src/main.py") == "src/main.py"


def test_normalize_repo_relative_path_backslashes() -> None:
    client = GitClient()
    assert client._normalize_repo_relative_path("src\\utils\\helper.py") == "src/utils/helper.py"


def test_normalize_repo_relative_path_clean_passthrough() -> None:
    client = GitClient()
    assert client._normalize_repo_relative_path("src/main.py") == "src/main.py"


# ---------------------------------------------------------------------------
# simple delegations
# ---------------------------------------------------------------------------

def test_checkout_base_delegates_correct_command() -> None:
    client = TrackingGitClient()

    client.checkout_base(Path("/repo"), "main")

    assert client.calls == [
        (["git", "checkout", "main"], Path("/repo")),
        (["git", "pull", "--ff-only"], Path("/repo")),
    ]


def test_set_identity_calls_name_and_email_in_order() -> None:
    client = TrackingGitClient()

    client.set_identity(Path("/repo"), "Alice", "alice@example.com")

    assert client.calls == [
        (["git", "config", "user.name", "Alice"], Path("/repo")),
        (["git", "config", "user.email", "alice@example.com"], Path("/repo")),
    ]


def test_push_branch_delegates_correct_command() -> None:
    client = TrackingGitClient()

    client.push_branch(Path("/repo"), "feature-x")

    assert client.calls == [(["git", "push", "-u", "origin", "feature-x"], Path("/repo"))]


# ---------------------------------------------------------------------------
# branch_allowed (standalone function)
# ---------------------------------------------------------------------------

def test_branch_allowed_empty_patterns() -> None:
    assert branch_allowed("anything", []) is True


def test_branch_allowed_exact_match() -> None:
    assert branch_allowed("main", ["main", "develop"]) is True


def test_branch_allowed_glob_wildcard() -> None:
    assert branch_allowed("feature/foo", ["feature/*"]) is True


def test_branch_allowed_no_match() -> None:
    assert branch_allowed("hotfix/bar", ["main", "develop"]) is False


def test_branch_allowed_matches_later_pattern() -> None:
    """branch_allowed returns True when the first pattern fails but a later one matches."""
    assert branch_allowed("feature/abc", ["main", "develop", "feature/*"]) is True


def test_branch_allowed_star_pattern_matches_everything() -> None:
    """A single '*' pattern allows any branch."""
    assert branch_allowed("release/v2.0", ["*"]) is True


# ---------------------------------------------------------------------------
# add_local_exclude
# ---------------------------------------------------------------------------

def test_add_local_exclude_creates_file_when_missing(tmp_path: Path) -> None:
    client = GitClient()

    client.add_local_exclude(tmp_path, "pattern")

    exclude_path = tmp_path / ".git" / "info" / "exclude"
    assert exclude_path.exists()
    assert exclude_path.read_text() == "pattern\n"


def test_add_local_exclude_appends_to_existing(tmp_path: Path) -> None:
    client = GitClient()
    exclude_path = tmp_path / ".git" / "info" / "exclude"
    exclude_path.parent.mkdir(parents=True, exist_ok=True)
    exclude_path.write_text("first\n")

    client.add_local_exclude(tmp_path, "second")

    assert exclude_path.read_text() == "first\nsecond\n"


def test_add_local_exclude_dedup_skips_existing_pattern(tmp_path: Path) -> None:
    client = GitClient()
    exclude_path = tmp_path / ".git" / "info" / "exclude"
    exclude_path.parent.mkdir(parents=True, exist_ok=True)
    exclude_path.write_text("already\n")

    client.add_local_exclude(tmp_path, "already")

    assert exclude_path.read_text() == "already\n"


def test_add_local_exclude_normalizes_missing_trailing_newline(tmp_path: Path) -> None:
    client = GitClient()
    exclude_path = tmp_path / ".git" / "info" / "exclude"
    exclude_path.parent.mkdir(parents=True, exist_ok=True)
    exclude_path.write_text("first")

    client.add_local_exclude(tmp_path, "second")

    assert exclude_path.read_text() == "first\nsecond\n"


def test_add_local_exclude_appends_to_existing_empty_file(tmp_path: Path) -> None:
    client = GitClient()
    exclude_path = tmp_path / ".git" / "info" / "exclude"
    exclude_path.parent.mkdir(parents=True, exist_ok=True)
    exclude_path.write_text("")

    client.add_local_exclude(tmp_path, "pattern")

    assert exclude_path.read_text() == "pattern\n"


# ---------------------------------------------------------------------------
# _run / _run_bytes error paths (monkeypatch subprocess.run)
# ---------------------------------------------------------------------------

def test_run_raises_runtime_error_on_nonzero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess as sp

    class FakeProc:
        returncode = 1
        stdout = ""
        stderr = "fatal: not a git repository"

    monkeypatch.setattr(sp, "run", lambda *a, **kw: FakeProc())  # type: ignore[attr-defined]
    client = GitClient()
    with pytest.raises(RuntimeError, match="git command failed"):
        client._run(["git", "status"])


def test_run_error_message_includes_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess as sp

    class FakeProc:
        returncode = 1
        stdout = ""
        stderr = "fatal: not a git repository"

    monkeypatch.setattr(sp, "run", lambda *a, **kw: FakeProc())  # type: ignore[attr-defined]
    client = GitClient()
    with pytest.raises(RuntimeError, match="fatal: not a git repository"):
        client._run(["git", "status"])


def test_run_bytes_raises_runtime_error_on_nonzero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess as sp

    class FakeProc:
        returncode = 1
        stdout = b""
        stderr = b"fatal: bad object"

    monkeypatch.setattr(sp, "run", lambda *a, **kw: FakeProc())  # type: ignore[attr-defined]
    client = GitClient()
    with pytest.raises(RuntimeError, match="git command failed"):
        client._run_bytes(["git", "diff"])


def test_run_bytes_error_decodes_non_utf8_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    """_run_bytes should decode non-UTF-8 stderr gracefully using replacement chars."""
    import subprocess as sp

    class FakeProc:
        returncode = 1
        stdout = b""
        stderr = b"error: \xff\xfe bad bytes"

    monkeypatch.setattr(sp, "run", lambda *a, **kw: FakeProc())  # type: ignore[attr-defined]
    client = GitClient()
    with pytest.raises(RuntimeError, match="error:.*bad bytes"):
        client._run_bytes(["git", "diff"])


def test_run_returns_stripped_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess as sp

    class FakeProc:
        returncode = 0
        stdout = "  hello world  \n"
        stderr = ""

    monkeypatch.setattr(sp, "run", lambda *a, **kw: FakeProc())  # type: ignore[attr-defined]
    client = GitClient()
    assert client._run(["git", "status"]) == "hello world"


def test_run_bytes_returns_raw_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess as sp

    class FakeProc:
        returncode = 0
        stdout = b"\x00raw\xff"
        stderr = b""

    monkeypatch.setattr(sp, "run", lambda *a, **kw: FakeProc())  # type: ignore[attr-defined]
    client = GitClient()
    assert client._run_bytes(["git", "diff"]) == b"\x00raw\xff"


# ---------------------------------------------------------------------------
# recent_commits
# ---------------------------------------------------------------------------

def test_recent_commits_normal_output() -> None:
    client = FakeGitClient(
        {
            ("git", "log", "-n5", "--pretty=format:%h %s"): b"abc1234 Initial commit\ndef5678 Add feature\n",
        }
    )
    result = client.recent_commits(Path("."))
    assert result == ["abc1234 Initial commit", "def5678 Add feature"]


def test_recent_commits_empty_output() -> None:
    client = FakeGitClient(
        {
            ("git", "log", "-n5", "--pretty=format:%h %s"): b"",
        }
    )
    assert client.recent_commits(Path(".")) == []


def test_recent_commits_custom_max_count() -> None:
    client = FakeGitClient(
        {
            ("git", "log", "-n2", "--pretty=format:%h %s"): b"abc1234 First\n",
        }
    )
    result = client.recent_commits(Path("."), max_count=2)
    assert result == ["abc1234 First"]


# ---------------------------------------------------------------------------
# recent_changed_files
# ---------------------------------------------------------------------------

def test_recent_changed_files_deduplication() -> None:
    client = FakeGitClient(
        {
            ("git", "log", "-n3", "--name-only", "--pretty=format:"): b"\nsrc/a.py\nsrc/b.py\n\nsrc/a.py\nsrc/c.py\n",
        }
    )
    result = client.recent_changed_files(Path("."))
    assert result == ["src/a.py", "src/b.py", "src/c.py"]


def test_recent_changed_files_normalization() -> None:
    client = FakeGitClient(
        {
            ("git", "log", "-n3", "--name-only", "--pretty=format:"): b"\n./src/main.py\n",
        }
    )
    result = client.recent_changed_files(Path("."))
    assert result == ["src/main.py"]


def test_recent_changed_files_empty() -> None:
    client = FakeGitClient(
        {
            ("git", "log", "-n3", "--name-only", "--pretty=format:"): b"",
        }
    )
    assert client.recent_changed_files(Path(".")) == []


def test_recent_changed_files_custom_max_count() -> None:
    """recent_changed_files forwards a custom max_count into the git log command."""
    client = FakeGitClient(
        {
            ("git", "log", "-n7", "--name-only", "--pretty=format:"): b"\nsrc/x.py\nsrc/y.py\n",
        }
    )
    result = client.recent_changed_files(Path("."), max_count=7)
    assert result == ["src/x.py", "src/y.py"]


# ---------------------------------------------------------------------------
# commit_all
# ---------------------------------------------------------------------------

def test_commit_all_returns_false_when_no_changes() -> None:
    client = FakeGitClient(
        {
            ("git", "add", "-A"): b"",
            ("git", "status", "--porcelain"): b"",
        }
    )
    assert client.commit_all(Path("."), "msg") is False


def test_commit_all_returns_true_when_changes_exist() -> None:
    client = FakeGitClient(
        {
            ("git", "add", "-A"): b"",
            ("git", "status", "--porcelain"): b"M  src/main.py\n",
            ("git", "commit", "-m", "fix stuff"): b"",
        }
    )
    assert client.commit_all(Path("."), "fix stuff") is True


def test_commit_all_runs_add_status_commit_in_order() -> None:
    class OrderedTrackingGitClient(GitClient):
        def __init__(self) -> None:
            self.calls: list[tuple[list[str], Path | None]] = []

        def _run(self, args: list[str], cwd: Path | None = None) -> str:
            self.calls.append((args, cwd))
            if args == ["git", "status", "--porcelain"]:
                return "M  src/main.py"
            return ""

    client = OrderedTrackingGitClient()

    assert client.commit_all(Path("/repo"), "fix stuff") is True
    assert client.calls == [
        (["git", "add", "-A"], Path("/repo")),
        (["git", "status", "--porcelain"], Path("/repo")),
        (["git", "commit", "-m", "fix stuff"], Path("/repo")),
    ]


def test_commit_all_runs_git_add_before_noop_status_check() -> None:
    client = TrackingGitClient()

    assert client.commit_all(Path("/repo"), "msg") is False
    assert client.calls == [
        (["git", "add", "-A"], Path("/repo")),
        (["git", "status", "--porcelain"], Path("/repo")),
    ]


# ---------------------------------------------------------------------------
# create_task_branch
# ---------------------------------------------------------------------------

def test_create_task_branch_remote_exists() -> None:
    """When the branch already exists on the remote, track it."""
    calls: list[tuple[str, ...]] = []

    class TrackingFake(GitClient):
        def _run(self, args: list[str], cwd: Path | None = None) -> str:
            calls.append(tuple(args))
            if args[:4] == ["git", "ls-remote", "--heads", "origin"]:
                return "abc123\trefs/heads/task-1"
            return ""

    client = TrackingFake()
    client.create_task_branch(Path("."), "task-1")
    assert ("git", "checkout", "-b", "task-1", "origin/task-1") in calls


def test_create_task_branch_new_branch() -> None:
    """When the branch does not exist on remote, create locally."""
    calls: list[tuple[str, ...]] = []

    class TrackingFake(GitClient):
        def _run(self, args: list[str], cwd: Path | None = None) -> str:
            calls.append(tuple(args))
            return ""

    client = TrackingFake()
    client.create_task_branch(Path("."), "task-2")
    assert ("git", "checkout", "-b", "task-2") in calls
    assert ("git", "checkout", "-b", "task-2", "origin/task-2") not in calls


def test_create_task_branch_returns_true_when_remote_exists() -> None:
    """create_task_branch returns True when the branch already exists on remote."""
    class FakeRemoteExists(GitClient):
        def _run(self, args: list[str], cwd: Path | None = None) -> str:
            if args[:4] == ["git", "ls-remote", "--heads", "origin"]:
                return "abc123\trefs/heads/task-1"
            return ""

    client = FakeRemoteExists()
    assert client.create_task_branch(Path("."), "task-1") is True


def test_create_task_branch_returns_false_when_new() -> None:
    """create_task_branch returns False when the branch does not exist on remote."""
    class FakeNoRemote(GitClient):
        def _run(self, args: list[str], cwd: Path | None = None) -> str:
            return ""

    client = FakeNoRemote()
    assert client.create_task_branch(Path("."), "task-2") is False


# ---------------------------------------------------------------------------
# verify_remote_branch_exists
# ---------------------------------------------------------------------------

def test_verify_remote_branch_exists_success() -> None:
    client = FakeGitClient(
        {
            ("git", "ls-remote", "--heads", "origin", "main"): b"abc123\trefs/heads/main\n",
        }
    )
    # Should not raise
    client.verify_remote_branch_exists(Path("."), "main")


def test_verify_remote_branch_exists_raises_value_error() -> None:
    client = FakeGitClient(
        {
            ("git", "ls-remote", "--heads", "origin", "nonexistent"): b"",
        }
    )
    with pytest.raises(ValueError, match="Base branch does not exist on remote"):
        client.verify_remote_branch_exists(Path("."), "nonexistent")


# ---------------------------------------------------------------------------
# clone
# ---------------------------------------------------------------------------

def test_clone_returns_correct_path() -> None:
    client = FakeGitClient({})
    result = client.clone("https://github.com/example/repo.git", Path("/workspace"))
    assert result == Path("/workspace/repo")


# ---------------------------------------------------------------------------
# diff_patch
# ---------------------------------------------------------------------------

def test_diff_patch_returns_diff_output() -> None:
    client = FakeGitClient(
        {
            ("git", "diff", "--binary", "HEAD"): b"diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n",
        }
    )
    result = client.diff_patch(Path("."))
    assert "diff --git" in result


# ---------------------------------------------------------------------------
# try_merge_base
# ---------------------------------------------------------------------------

def test_try_merge_base_success(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess as sp
    from types import SimpleNamespace

    def fake_run(args: list[str], **kwargs: object) -> SimpleNamespace:
        if args[:2] == ["git", "merge"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(sp, "run", fake_run)
    client = GitClient()
    success, conflicts = client.try_merge_base(Path("/repo"), "main")
    assert success is True
    assert conflicts == []


def test_try_merge_base_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess as sp
    from types import SimpleNamespace

    def fake_run(args: list[str], **kwargs: object) -> SimpleNamespace:
        if args[:2] == ["git", "merge"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="merge conflict")
        if args[:2] == ["git", "diff"]:
            return SimpleNamespace(returncode=0, stdout="src/a.py\nsrc/b.py\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(sp, "run", fake_run)
    client = GitClient()
    success, conflicts = client.try_merge_base(Path("/repo"), "main")
    assert success is False
    assert conflicts == ["src/a.py", "src/b.py"]


def test_try_merge_base_treats_fatal_merge_error_as_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess as sp
    from types import SimpleNamespace

    def fake_run(args: list[str], **kwargs: object) -> SimpleNamespace:
        if args[:2] == ["git", "merge"]:
            return SimpleNamespace(returncode=128, stdout="", stderr="fatal: bad revision")
        if args[:2] == ["git", "diff"]:
            return SimpleNamespace(returncode=0, stdout="src/conflict.py\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(sp, "run", fake_run)
    client = GitClient()
    success, conflicts = client.try_merge_base(Path("/repo"), "main")
    assert success is False
    assert conflicts == ["src/conflict.py"]


# ===========================================================================
# COVERAGE GAP AUDIT — full review of client.py (179 lines)
# ===========================================================================
#
# Legend:
#   ✅ COVERED  — gap identified and test written (in this file below)
#   🔴 HIGH     — must add test (next stage)
#   🟡 MEDIUM   — should add test (next stage)
#   ⬜ SKIP     — implicitly covered or tests stdlib; not worth adding
#
# ---------------------------------------------------------------------------
# _run (line 9) / _run_bytes (line 15)
# ---------------------------------------------------------------------------
#   ✅ Error on nonzero exit (lines 228, 256)
#   ✅ Stderr included in error message (line 242)
#   ✅ _run_bytes decodes non-UTF-8 stderr gracefully (line 270)
#   ✅ _run returns stripped stdout (line 285)
#   ✅ _run_bytes returns raw stdout (line 298)
#   ✅ cwd forwarded to subprocess.run for both _run and _run_bytes (lines 870, 891)
#
# ---------------------------------------------------------------------------
# clone (line 22)
# ---------------------------------------------------------------------------
#   ✅ Returns correct path (line 498)
#   ✅ Forwards URL to _run (line 856)
#
# ---------------------------------------------------------------------------
# add_local_exclude (line 27)
# ---------------------------------------------------------------------------
#   ✅ Creates file when missing (line 181)
#   ✅ Appends to existing (line 191)
#   ✅ Dedup skips existing pattern (line 202)
#   ✅ Normalizes missing trailing newline (line 213)
#   ✅ Strips whitespace from pattern (line 807)
#
# ---------------------------------------------------------------------------
# verify_remote_branch_exists (line 40)
# ---------------------------------------------------------------------------
#   ✅ Success path (line 474)
#   ✅ Raises ValueError when branch missing (line 484)
#
# ---------------------------------------------------------------------------
# checkout_base (line 45)
# ---------------------------------------------------------------------------
#   ✅ Delegates to git checkout (line 557)
#   🟡 GAP: cwd forwarding — test at line 557 uses TrackingFake that
#        discards cwd. Should verify cwd=repo_path reaches _run.
#
# ---------------------------------------------------------------------------
# create_task_branch (line 48)
# ---------------------------------------------------------------------------
#   ✅ Remote exists — tracks it (line 417)
#   ✅ New branch — creates locally (line 433)
#   ✅ Returns True when remote exists (line 448)
#   ✅ Returns False when new (line 460)
#   🔴 GAP: Checkout error on remote-exists path (line 55) — existing error
#        propagation test (line 1683) only exercises the new-branch path
#        (line 57) because RaisingGitClient returns "" for ls-remote.
#
# ---------------------------------------------------------------------------
# try_merge_base (line 57) — calls subprocess.run directly, NOT _run
# ---------------------------------------------------------------------------
#   ✅ Merge succeeds (line 574)
#   ✅ Merge conflict with files (line 590)
#   ✅ Merge conflict with no unmerged paths (line 821)
#   🔴 GAP: git diff command itself fails (returncode != 0 at line 72).
#        Code never checks status.returncode — silently reads stdout.
#        This is also a LATENT BUG in client.py: should it raise or log?
#        Test should document current behavior (returns (False, [])).
#   🔴 GAP: cwd forwarding — both subprocess.run calls (merge at line 65,
#        diff at line 72) should pass cwd=repo_path. No test verifies this.
#        Unlike other methods, try_merge_base bypasses _run entirely.
#   🟡 GAP: Whitespace-only lines in conflict output — the `if f.strip()`
#        filter at line 76 is a defensive guard. Test with stdout containing
#        blank/whitespace lines to confirm they're dropped.
#
# ---------------------------------------------------------------------------
# recent_commits (line 79)
# ---------------------------------------------------------------------------
#   ✅ Normal output (line 315)
#   ✅ Empty output (line 325)
#   ✅ Custom max_count (line 334)
#
# ---------------------------------------------------------------------------
# recent_changed_files (line 86)
# ---------------------------------------------------------------------------
#   ✅ Deduplication (line 348)
#   ✅ Path normalization (line 358)
#   ✅ Empty output (line 368)
#   ✅ Custom max_count (line 377)
#
# ---------------------------------------------------------------------------
# set_identity (line 99)
# ---------------------------------------------------------------------------
#   ✅ Calls both config commands (line 522)
#
# ---------------------------------------------------------------------------
# changed_files (line 103)
# ---------------------------------------------------------------------------
#   ✅ Rename, delete, and untracked (line 19)
#   ✅ Empty when no changes (line 39)
#   ✅ Dedup overlap between diff and untracked (line 788)
#
# ---------------------------------------------------------------------------
# diff_stat (line 114)
# ---------------------------------------------------------------------------
#   ✅ Empty when no changes (line 50)
#   ✅ Includes untracked files (line 61)
#   🟡 GAP: Untracked-only (no tracked changes) — tests cover "both" and
#        "neither" but not the case where only untracked files exist.
#
# ---------------------------------------------------------------------------
# diff_patch (line 123)
# ---------------------------------------------------------------------------
#   ✅ Returns diff output (line 508)
#
# ---------------------------------------------------------------------------
# commit_all (line 126)
# ---------------------------------------------------------------------------
#   ✅ Returns False when no changes (line 392)
#   ✅ Returns True when changes exist (line 402)
#   ✅ Forwards message to git commit (line 840)
#
# ---------------------------------------------------------------------------
# push_branch (line 134)
# ---------------------------------------------------------------------------
#   ✅ Calls push with correct args (line 540)
#
# ---------------------------------------------------------------------------
# _parse_name_status_output (line 137)
# ---------------------------------------------------------------------------
#   ✅ Empty input (line 79)
#   ✅ Simple statuses M/D/A (line 84)
#   ✅ Rename and Copy combined (line 90)
#   ✅ Truncated input — status with no path (line 96)
#   ✅ Isolated Copy returns destination (line 800)
#   🟡 GAP: Truncated rename — e.g. b"R100\x00old.py\x00" where destination
#        is missing. Exercises the guard at line 149 (idx+1 >= len(parts)),
#        which is a DIFFERENT branch from the existing truncation test at
#        line 96 (which exercises line 156).
#
# ---------------------------------------------------------------------------
# _parse_null_delimited_paths (line 162)
# ---------------------------------------------------------------------------
#   ✅ Empty input (line 106)
#   ✅ Single path (line 110)
#   ✅ Multiple paths (line 116)
#   ✅ Trailing null produces no empty strings (line 121)
#
# ---------------------------------------------------------------------------
# _normalize_repo_relative_path (line 171)
# ---------------------------------------------------------------------------
#   ✅ Leading dot-slash (line 132)
#   ✅ Backslashes (line 137)
#   ✅ Clean passthrough (line 142)
#   🟡 GAP: lstrip("./") is a char-level strip, not substring removal.
#        Dotfiles like ".gitignore" → "gitignore", ".env" → "env".
#        This is a LATENT BUG. Need tests documenting current behavior
#        for dotfile paths and parent-traversal paths like "../foo.py".
#
# ---------------------------------------------------------------------------
# branch_allowed (standalone, line 175)
# ---------------------------------------------------------------------------
#   ✅ Empty patterns (line 151)
#   ✅ Exact match (line 155)
#   ✅ Glob wildcard (line 159)
#   ✅ No match (line 163)
#   ✅ Matches later pattern (line 167)
#   ✅ Star pattern matches everything (line 172)
#
# ===========================================================================
# SUMMARY OF REMAINING GAPS (for next stage)
# ===========================================================================
#   ✅ DONE:   try_merge_base — git diff fails (returncode != 0) [+ latent bug]
#   ✅ DONE:   try_merge_base — cwd forwarding for both subprocess.run calls
#   ✅ DONE:   checkout_base — cwd forwarding to _run
#   ✅ DONE:   _parse_name_status_output — truncated rename (missing dest)
#   ✅ DONE:   try_merge_base — whitespace lines in conflict output
#   ✅ DONE:   diff_stat — untracked-only (no tracked changes)
#   ✅ DONE:   try_merge_base — diff_filter fails, warning logged
#
# --- GAPS FOUND IN SECOND AUDIT PASS ---
#
#   ✅ DONE:   create_task_branch line 55 — checkout failure on remote-exists
#              branch. test_create_task_branch_propagates_checkout_error (line
#              1683) uses RaisingGitClient but ls-remote returns "" so it
#              always takes the new-branch path (line 57), never the
#              remote-exists checkout at line 55. Need a test where ls-remote
#              returns non-empty AND the subsequent checkout -b ... origin/X
#              raises RuntimeError.
#
#   ✅ DONE:   _normalize_repo_relative_path line 181 — lstrip("./") strips
#              individual chars not the substring "./", so dotfiles like
#              ".gitignore" become "gitignore" and ".env" becomes "env".
#              This is a LATENT BUG. Need tests documenting current (buggy)
#              behavior for paths like ".gitignore", ".env", "../foo.py".
# ===========================================================================


# ===========================================================================
# Tests for coverage gaps
# ===========================================================================


def test_changed_files_dedup_overlap_diff_and_untracked() -> None:
    """Same file in both diff output and untracked output appears only once."""
    client = FakeGitClient(
        {
            ("git", "diff", "--name-status", "-z", "HEAD"): b"M\x00src/main.py\x00",
            ("git", "ls-files", "--others", "--exclude-standard", "-z"): b"src/main.py\x00",
        }
    )
    result = client.changed_files(Path("."))
    assert result == ["src/main.py"]


def test_parse_name_status_output_copy_returns_destination() -> None:
    """Isolated Copy entry returns only destination, not source."""
    client = GitClient()
    raw = b"C100\x00src.py\x00dest.py\x00"
    assert client._parse_name_status_output(raw) == ["dest.py"]


def test_add_local_exclude_strips_whitespace_from_pattern(tmp_path: Path) -> None:
    """Whitespace-padded pattern is stripped on write and dedup still works."""
    client = GitClient()

    client.add_local_exclude(tmp_path, "  pattern  ")

    exclude_path = tmp_path / ".git" / "info" / "exclude"
    assert exclude_path.read_text() == "pattern\n"

    # Call again — should be deduped against the already-written stripped value
    client.add_local_exclude(tmp_path, "  pattern  ")
    assert exclude_path.read_text() == "pattern\n"


def test_try_merge_base_conflict_no_unmerged_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """Merge fails but git diff returns no unmerged paths → (False, [])."""
    import subprocess as sp
    from types import SimpleNamespace

    def fake_run(args: list[str], **kwargs: object) -> SimpleNamespace:
        if args[:2] == ["git", "merge"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="merge conflict")
        if args[:2] == ["git", "diff"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(sp, "run", fake_run)
    client = GitClient()
    success, conflicts = client.try_merge_base(Path("/repo"), "main")
    assert success is False
    assert conflicts == []


def test_commit_all_forwards_message_to_git_commit() -> None:
    """The exact commit message is forwarded to the git commit command."""
    calls: list[tuple[str, ...]] = []

    class TrackingFake(GitClient):
        def _run(self, args: list[str], cwd: Path | None = None) -> str:
            calls.append(tuple(args))
            if args == ["git", "status", "--porcelain"]:
                return "M  src/main.py"
            return ""

    client = TrackingFake()
    client.commit_all(Path("."), "my message")
    assert ("git", "commit", "-m", "my message") in calls


def test_clone_forwards_url_to_run() -> None:
    """clone_url is forwarded as an argument to _run."""
    calls: list[tuple[str, ...]] = []

    class TrackingFake(GitClient):
        def _run(self, args: list[str], cwd: Path | None = None) -> str:
            calls.append(tuple(args))
            return ""

    client = TrackingFake()
    client.clone("https://example.com/repo.git", Path("/ws"))
    assert ("git", "clone", "https://example.com/repo.git", "/ws/repo") in calls


def test_run_forwards_cwd_to_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    """_run passes the cwd keyword argument through to subprocess.run."""
    import subprocess as sp

    captured: dict[str, object] = {}

    class FakeProc:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(*args: object, **kwargs: object) -> FakeProc:
        captured.update(kwargs)
        return FakeProc()

    monkeypatch.setattr(sp, "run", fake_run)
    client = GitClient()
    client._run(["git", "status"], cwd=Path("/myrepo"))
    assert captured["cwd"] == Path("/myrepo")


def test_run_bytes_forwards_cwd_to_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    """_run_bytes passes the cwd keyword argument through to subprocess.run."""
    import subprocess as sp

    captured: dict[str, object] = {}

    class FakeProc:
        returncode = 0
        stdout = b""
        stderr = b""

    def fake_run(*args: object, **kwargs: object) -> FakeProc:
        captured.update(kwargs)
        return FakeProc()

    monkeypatch.setattr(sp, "run", fake_run)
    client = GitClient()
    client._run_bytes(["git", "diff"], cwd=Path("/myrepo"))
    assert captured["cwd"] == Path("/myrepo")


# ===========================================================================
# Stage 2: Edge-case and error-path tests
# ===========================================================================


# ---------------------------------------------------------------------------
# checkout_base — cwd forwarding
# ---------------------------------------------------------------------------

def test_checkout_base_forwards_cwd_to_run() -> None:
    """checkout_base passes repo_path as cwd to _run."""
    captured_cwd: list[Path | None] = []

    class CwdTrackingFake(GitClient):
        def _run(self, args: list[str], cwd: Path | None = None) -> str:
            captured_cwd.append(cwd)
            return ""

    client = CwdTrackingFake()
    client.checkout_base(Path("/my/repo"), "main")
    assert captured_cwd == [Path("/my/repo"), Path("/my/repo")]


# ---------------------------------------------------------------------------
# try_merge_base — git diff itself fails (returncode != 0)
# ---------------------------------------------------------------------------

def test_try_merge_base_diff_command_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the git diff command itself fails (returncode != 0), the current
    code does NOT check status.returncode — it silently reads stdout.
    Document this behavior: returns (False, []) because stdout is empty."""
    import subprocess as sp
    from types import SimpleNamespace

    def fake_run(args: list[str], **kwargs: object) -> SimpleNamespace:
        if args[:2] == ["git", "merge"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="merge conflict")
        if args[:2] == ["git", "diff"]:
            return SimpleNamespace(returncode=128, stdout="", stderr="fatal: bad object")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(sp, "run", fake_run)
    client = GitClient()
    success, conflicts = client.try_merge_base(Path("/repo"), "main")
    assert success is False
    assert conflicts == []


def test_try_merge_base_diff_failure_with_partial_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Even when git diff fails, any stdout paths are still returned as conflicts."""
    import subprocess as sp
    from types import SimpleNamespace

    def fake_run(args: list[str], **kwargs: object) -> SimpleNamespace:
        if args[:2] == ["git", "merge"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="merge conflict")
        if args[:2] == ["git", "diff"]:
            return SimpleNamespace(returncode=128, stdout="src/partial.py\n", stderr="fatal: bad object")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(sp, "run", fake_run)
    client = GitClient()
    success, conflicts = client.try_merge_base(Path("/repo"), "main")
    assert success is False
    assert conflicts == ["src/partial.py"]


# ---------------------------------------------------------------------------
# try_merge_base — cwd forwarding for both subprocess.run calls
# ---------------------------------------------------------------------------

def test_try_merge_base_forwards_cwd_to_both_subprocess_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """try_merge_base bypasses _run and calls subprocess.run directly.
    Verify cwd=repo_path is passed to both the merge and the diff calls."""
    import subprocess as sp
    from types import SimpleNamespace

    captured_cwds: list[Path | None] = []

    def fake_run(args: list[str], **kwargs: object) -> SimpleNamespace:
        captured_cwds.append(kwargs.get("cwd"))
        if args[:2] == ["git", "merge"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="conflict")
        return SimpleNamespace(returncode=0, stdout="src/a.py\n", stderr="")

    monkeypatch.setattr(sp, "run", fake_run)
    client = GitClient()
    client.try_merge_base(Path("/the/repo"), "main")
    assert captured_cwds == [Path("/the/repo"), Path("/the/repo")]


# ---------------------------------------------------------------------------
# try_merge_base — whitespace/blank lines in conflict output
# ---------------------------------------------------------------------------

def test_try_merge_base_filters_blank_lines_from_conflict_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Blank and whitespace-only lines in git diff output are filtered out."""
    import subprocess as sp
    from types import SimpleNamespace

    def fake_run(args: list[str], **kwargs: object) -> SimpleNamespace:
        if args[:2] == ["git", "merge"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="conflict")
        if args[:2] == ["git", "diff"]:
            return SimpleNamespace(
                returncode=0,
                stdout="\n  \nsrc/a.py\n\n  \nsrc/b.py\n  \n",
                stderr="",
            )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(sp, "run", fake_run)
    client = GitClient()
    success, conflicts = client.try_merge_base(Path("/repo"), "main")
    assert success is False
    assert conflicts == ["src/a.py", "src/b.py"]


# ---------------------------------------------------------------------------
# _parse_name_status_output — truncated rename (missing destination)
# ---------------------------------------------------------------------------

def test_parse_name_status_output_truncated_rename_missing_destination() -> None:
    """Rename entry with source but no destination exercises the guard at
    line 149 (idx+1 >= len(parts)), different from the existing truncation
    test which exercises line 156."""
    client = GitClient()
    raw = b"R100\x00old.py\x00"
    assert client._parse_name_status_output(raw) == []


# ---------------------------------------------------------------------------
# branch_allowed — edge patterns
# ---------------------------------------------------------------------------

def test_branch_allowed_empty_string_branch() -> None:
    """Empty string branch does not match non-wildcard patterns."""
    assert branch_allowed("", ["main", "develop"]) is False


def test_branch_allowed_empty_string_matches_star() -> None:
    """Empty string branch matches the '*' wildcard."""
    assert branch_allowed("", ["*"]) is True


def test_branch_allowed_question_mark_glob() -> None:
    """'?' glob matches exactly one character."""
    assert branch_allowed("v1", ["v?"]) is True
    assert branch_allowed("v12", ["v?"]) is False


# ---------------------------------------------------------------------------
# add_local_exclude — edge cases
# ---------------------------------------------------------------------------

def test_add_local_exclude_empty_string_pattern(tmp_path: Path) -> None:
    """Empty string pattern (after stripping) is still written."""
    client = GitClient()
    client.add_local_exclude(tmp_path, "")
    exclude_path = tmp_path / ".git" / "info" / "exclude"
    assert exclude_path.exists()
    assert exclude_path.read_text() == "\n"


def test_add_local_exclude_pattern_with_newline_chars(tmp_path: Path) -> None:
    """Pattern containing embedded newline chars: strip() collapses them,
    so only the inner content is written as a single line."""
    client = GitClient()
    client.add_local_exclude(tmp_path, "\nmy_pattern\n")
    exclude_path = tmp_path / ".git" / "info" / "exclude"
    # strip() removes leading/trailing newlines, so "my_pattern" is written
    assert exclude_path.read_text() == "my_pattern\n"


# ---------------------------------------------------------------------------
# diff_stat — untracked-only (no tracked changes)
# ---------------------------------------------------------------------------

def test_diff_stat_untracked_only_no_tracked_changes() -> None:
    """When there are no tracked changes but untracked files exist,
    diff_stat returns only the untracked lines."""
    client = FakeGitClient(
        {
            ("git", "diff", "--stat", "HEAD"): b"",
            ("git", "ls-files", "--others", "--exclude-standard", "-z"): (
                b"new_file.py\x00another.txt\x00"
            ),
        }
    )
    result = client.diff_stat(Path("."))
    assert "untracked | new_file.py" in result
    assert "untracked | another.txt" in result
    lines = result.strip().splitlines()
    assert len(lines) == 2


def test_diff_stat_normalizes_untracked_paths() -> None:
    client = FakeGitClient(
        {
            ("git", "diff", "--stat", "HEAD"): b"",
            ("git", "ls-files", "--others", "--exclude-standard", "-z"): (
                b"./tmp/new_file.py\x00src\\windows\\path.py\x00"
            ),
        }
    )

    result = client.diff_stat(Path("."))

    assert "untracked | tmp/new_file.py" in result
    assert "untracked | src/windows/path.py" in result


# ---------------------------------------------------------------------------
# recent_commits — blank lines in output
# ---------------------------------------------------------------------------

def test_recent_commits_filters_blank_lines() -> None:
    """Blank lines interspersed in git log output are filtered out."""
    client = FakeGitClient(
        {
            ("git", "log", "-n5", "--pretty=format:%h %s"): (
                b"abc1234 First\n\n\ndef5678 Second\n  \n"
            ),
        }
    )
    result = client.recent_commits(Path("."))
    assert result == ["abc1234 First", "def5678 Second"]


# ---------------------------------------------------------------------------
# recent_changed_files — blank lines in output
# ---------------------------------------------------------------------------

def test_recent_changed_files_filters_blank_lines() -> None:
    """Blank lines in git log --name-only output are filtered out."""
    client = FakeGitClient(
        {
            ("git", "log", "-n3", "--name-only", "--pretty=format:"): (
                b"\n\nsrc/a.py\n\n\nsrc/b.py\n\n"
            ),
        }
    )
    result = client.recent_changed_files(Path("."))
    assert result == ["src/a.py", "src/b.py"]


# ---------------------------------------------------------------------------
# changed_files — paths needing normalization
# ---------------------------------------------------------------------------

def test_changed_files_normalizes_backslashes_and_dot_prefixes() -> None:
    """Paths with backslashes or leading ./ are normalized in changed_files."""
    client = FakeGitClient(
        {
            ("git", "diff", "--name-status", "-z", "HEAD"): (
                b"M\x00./src/main.py\x00M\x00src\\utils\\helper.py\x00"
            ),
            ("git", "ls-files", "--others", "--exclude-standard", "-z"): b"",
        }
    )
    result = client.changed_files(Path("."))
    assert "src/main.py" in result
    assert "src/utils/helper.py" in result


# ---------------------------------------------------------------------------
# Edge-case tests — Stage 3
# ---------------------------------------------------------------------------

def test_add_local_exclude_dedup_with_whitespace_in_existing_file(tmp_path: Path) -> None:
    """When the exclude file already contains a whitespace-padded line,
    adding the stripped version should be treated as a duplicate."""
    client = GitClient()
    exclude_path = tmp_path / ".git" / "info" / "exclude"
    exclude_path.parent.mkdir(parents=True, exist_ok=True)
    exclude_path.write_text("  my_pattern  \n")

    # Adding the stripped version should be detected as duplicate
    client.add_local_exclude(tmp_path, "my_pattern")

    # strip() removes leading/trailing whitespace from existing lines,
    # so "my_pattern" matches and nothing is appended
    assert exclude_path.read_text() == "  my_pattern  \n"


def test_changed_files_deduplicates_tracked_and_untracked_overlap() -> None:
    """A file appearing in both git diff (tracked) and ls-files (untracked)
    should appear only once in the result."""
    client = FakeGitClient(
        {
            ("git", "diff", "--name-status", "-z", "HEAD"): (
                b"M\x00src/shared.py\x00M\x00src/only_tracked.py\x00"
            ),
            ("git", "ls-files", "--others", "--exclude-standard", "-z"): (
                b"src/shared.py\x00src/only_untracked.py\x00"
            ),
        }
    )
    result = client.changed_files(Path("."))
    # sorted(set(...)) ensures no duplicates
    assert result == ["src/only_tracked.py", "src/only_untracked.py", "src/shared.py"]
    assert result.count("src/shared.py") == 1


def test_parse_name_status_output_truncated_rename_entry() -> None:
    """A rename (R) status followed by only one path (instead of two)
    should be handled gracefully — the parser breaks out of the loop."""
    client = GitClient()
    # R100 followed by old.py but missing new.py — truncated input
    raw = b"M\x00src/ok.py\x00R100\x00old.py\x00"
    result = client._parse_name_status_output(raw)
    # The M entry is parsed fine; the R entry has only one path so parsing stops
    assert result == ["src/ok.py"]


def test_recent_changed_files_preserves_insertion_order_across_commits() -> None:
    """Files from earlier commits should appear after files from later commits,
    preserving the insertion order (first-seen wins for dedup)."""
    client = FakeGitClient(
        {
            ("git", "log", "-n3", "--name-only", "--pretty=format:"): (
                # Commit 1 (most recent): c.py, a.py
                b"\nc.py\na.py\n"
                # Commit 2: a.py (duplicate), b.py
                b"\na.py\nb.py\n"
                # Commit 3 (oldest): d.py
                b"\nd.py\n"
            ),
        }
    )
    result = client.recent_changed_files(Path("."))
    # c.py first seen in commit 1, a.py first seen in commit 1,
    # b.py first seen in commit 2, d.py first seen in commit 3
    assert result == ["c.py", "a.py", "b.py", "d.py"]


# ===========================================================================
# Stage 3: Gap-filling tests
# ===========================================================================


# ---------------------------------------------------------------------------
# 1. Error propagation tests — RaisingGitClient
# ---------------------------------------------------------------------------


class RaisingGitClient(GitClient):
    """GitClient subclass where _run raises RuntimeError for specific command prefixes."""

    def __init__(self, failing_prefixes: list[tuple[str, ...]]) -> None:
        self.failing_prefixes = failing_prefixes

    def _run(self, args: list[str], cwd: Path | None = None) -> str:
        for prefix in self.failing_prefixes:
            if tuple(args[: len(prefix)]) == prefix:
                raise RuntimeError(f"simulated failure: {' '.join(args)}")
        return ""

    def _run_bytes(self, args: list[str], cwd: Path | None = None) -> bytes:
        for prefix in self.failing_prefixes:
            if tuple(args[: len(prefix)]) == prefix:
                raise RuntimeError(f"simulated failure: {' '.join(args)}")
        return b""


def test_clone_propagates_runtime_error() -> None:
    client = RaisingGitClient([("git", "clone")])
    with pytest.raises(RuntimeError, match="simulated failure"):
        client.clone("https://example.com/repo.git", Path("/ws"))


def test_push_branch_propagates_runtime_error() -> None:
    client = RaisingGitClient([("git", "push")])
    with pytest.raises(RuntimeError, match="simulated failure"):
        client.push_branch(Path("."), "feature-1")


def test_diff_patch_propagates_runtime_error() -> None:
    client = RaisingGitClient([("git", "diff")])
    with pytest.raises(RuntimeError, match="simulated failure"):
        client.diff_patch(Path("."))


def test_checkout_base_propagates_runtime_error() -> None:
    client = RaisingGitClient([("git", "checkout")])
    with pytest.raises(RuntimeError, match="simulated failure"):
        client.checkout_base(Path("."), "main")


def test_commit_all_raises_when_git_add_fails() -> None:
    """commit_all raises RuntimeError when git add -A (the first _run call) fails."""
    client = RaisingGitClient([("git", "add")])
    with pytest.raises(RuntimeError, match="simulated failure"):
        client.commit_all(Path("."), "msg")


def test_set_identity_raises_when_name_config_fails() -> None:
    """set_identity raises RuntimeError when the user.name config command fails."""
    client = RaisingGitClient([("git", "config", "user.name")])
    with pytest.raises(RuntimeError, match="simulated failure"):
        client.set_identity(Path("."), "Bot", "bot@example.com")


def test_set_identity_raises_when_email_config_fails() -> None:
    """set_identity raises RuntimeError when the user.email config command fails."""
    client = RaisingGitClient([("git", "config", "user.email")])
    with pytest.raises(RuntimeError, match="simulated failure"):
        client.set_identity(Path("."), "Bot", "bot@example.com")


# ---------------------------------------------------------------------------
# 2. verify_remote_branch_exists — error message propagation
# ---------------------------------------------------------------------------


def test_verify_remote_branch_exists_propagates_runtime_error_message() -> None:
    """When _run raises RuntimeError with a specific stderr message, it propagates intact."""
    client = RaisingGitClient([("git", "ls-remote")])
    with pytest.raises(RuntimeError, match="simulated failure.*git ls-remote"):
        client.verify_remote_branch_exists(Path("."), "main")


# ---------------------------------------------------------------------------
# 3. branch_allowed with fnmatch special chars
# ---------------------------------------------------------------------------


def test_branch_allowed_bracket_pattern_matching() -> None:
    """Bracket patterns like [abc]* match branches starting with a, b, or c."""
    assert branch_allowed("alpha", ["[abc]*"]) is True
    assert branch_allowed("bravo", ["[abc]*"]) is True
    assert branch_allowed("charlie", ["[abc]*"]) is True
    assert branch_allowed("delta", ["[abc]*"]) is False


def test_branch_allowed_bracket_pattern_no_match() -> None:
    """Bracket patterns don't match characters outside the set."""
    assert branch_allowed("xyz", ["[abc]*"]) is False


def test_branch_allowed_question_mark_single_char() -> None:
    """'?' matches exactly one character, no more, no less."""
    assert branch_allowed("a", ["?"]) is True
    assert branch_allowed("ab", ["?"]) is False
    assert branch_allowed("", ["?"]) is False


def test_branch_allowed_escaped_bracket_literal() -> None:
    """Escaped bracket patterns match literal brackets (fnmatch behavior)."""
    assert branch_allowed("[abc]test", ["[[]abc]test"]) is True
    assert branch_allowed("atest", ["[[]abc]test"]) is False


# ---------------------------------------------------------------------------
# 4. changed_files sort stability with mixed-case paths
# ---------------------------------------------------------------------------


def test_changed_files_sort_stability_mixed_case() -> None:
    """Mixed-case paths that normalize should produce deterministic sorted output."""
    client = FakeGitClient(
        {
            ("git", "diff", "--name-status", "-z", "HEAD"): (
                b"M\x00src/Zebra.py\x00"
                b"M\x00src/alpha.py\x00"
                b"M\x00src/Beta.py\x00"
                b"M\x00src/gamma.py\x00"
            ),
            ("git", "ls-files", "--others", "--exclude-standard", "-z"): b"",
        }
    )
    result = client.changed_files(Path("."))
    assert result == sorted(result)
    # Capital letters sort before lowercase in default sort
    assert result == ["src/Beta.py", "src/Zebra.py", "src/alpha.py", "src/gamma.py"]


# ---------------------------------------------------------------------------
# 5. add_local_exclude idempotency
# ---------------------------------------------------------------------------


def test_add_local_exclude_idempotency(tmp_path: Path) -> None:
    """Calling add_local_exclude twice with the same pattern writes it only once."""
    client = GitClient()
    client.add_local_exclude(tmp_path, "mypattern")
    client.add_local_exclude(tmp_path, "mypattern")

    exclude_path = tmp_path / ".git" / "info" / "exclude"
    content = exclude_path.read_text()
    assert content.count("mypattern") == 1


def test_add_local_exclude_whitespace_pattern_idempotency(tmp_path: Path) -> None:
    """Pattern with leading/trailing whitespace is stripped; dedup works across calls."""
    client = GitClient()
    client.add_local_exclude(tmp_path, "  spaced  ")
    client.add_local_exclude(tmp_path, "spaced")

    exclude_path = tmp_path / ".git" / "info" / "exclude"
    content = exclude_path.read_text()
    assert content.count("spaced") == 1


def test_add_local_exclude_appends_to_file_without_trailing_newline(tmp_path: Path) -> None:
    """Adding to a file that doesn't end with newline inserts one before the pattern."""
    client = GitClient()
    exclude_path = tmp_path / ".git" / "info" / "exclude"
    exclude_path.parent.mkdir(parents=True, exist_ok=True)
    exclude_path.write_text("existing")

    client.add_local_exclude(tmp_path, "new")

    content = exclude_path.read_text()
    assert content == "existing\nnew\n"


# ---------------------------------------------------------------------------
# 6. recent_commits with whitespace-only lines (tab-only)
# ---------------------------------------------------------------------------


def test_recent_commits_filters_tab_only_lines() -> None:
    """Tab-only lines in git log output are filtered out."""
    client = FakeGitClient(
        {
            ("git", "log", "-n5", "--pretty=format:%h %s"): (
                b"abc1234 First\n\t\t\ndef5678 Second\n\t\n"
            ),
        }
    )
    result = client.recent_commits(Path("."))
    assert result == ["abc1234 First", "def5678 Second"]


# ---------------------------------------------------------------------------
# 7. recent_changed_files order preservation
# ---------------------------------------------------------------------------


def test_recent_changed_files_preserves_first_occurrence_order() -> None:
    """Deduplication preserves first-occurrence order, not last."""
    client = FakeGitClient(
        {
            ("git", "log", "-n3", "--name-only", "--pretty=format:"): (
                b"\nsrc/c.py\nsrc/a.py\nsrc/b.py\n\nsrc/a.py\nsrc/c.py\n"
            ),
        }
    )
    result = client.recent_changed_files(Path("."))
    # First occurrence order: c, a, b
    assert result == ["src/c.py", "src/a.py", "src/b.py"]


# ---------------------------------------------------------------------------
# 8. _parse_name_status_output with mixed R/C/M/D/A
# ---------------------------------------------------------------------------


def test_parse_name_status_output_mixed_all_status_codes() -> None:
    """Sequence containing all status codes: A, M, D, R, C."""
    client = GitClient()
    raw = (
        b"A\x00added.py\x00"
        b"M\x00modified.py\x00"
        b"D\x00deleted.py\x00"
        b"R100\x00old_name.py\x00new_name.py\x00"
        b"C050\x00source.py\x00copied.py\x00"
    )
    result = client._parse_name_status_output(raw)
    assert result == ["added.py", "modified.py", "deleted.py", "new_name.py", "copied.py"]


# ---------------------------------------------------------------------------
# 9. _parse_name_status_output with malformed/truncated input
# ---------------------------------------------------------------------------


def test_parse_name_status_output_rename_with_only_one_path() -> None:
    """R status with only source path but no destination — should break out safely."""
    client = GitClient()
    raw = b"R100\x00only_source.py\x00"
    result = client._parse_name_status_output(raw)
    assert result == []


def test_parse_name_status_output_copy_with_only_one_path() -> None:
    """C status with only source path but no destination — should break out safely."""
    client = GitClient()
    raw = b"C100\x00only_source.py\x00"
    result = client._parse_name_status_output(raw)
    assert result == []


def test_parse_name_status_output_status_code_only_no_paths() -> None:
    """Status code with no following paths — should break out safely."""
    client = GitClient()
    raw = b"M\x00"
    result = client._parse_name_status_output(raw)
    assert result == []


def test_parse_name_status_output_valid_then_truncated() -> None:
    """Valid entries followed by a truncated rename — valid entries are returned."""
    client = GitClient()
    raw = b"A\x00good.py\x00R100\x00orphan.py\x00"
    result = client._parse_name_status_output(raw)
    assert result == ["good.py"]


# ---------------------------------------------------------------------------
# 10. try_merge_base when diff --name-only also fails
# ---------------------------------------------------------------------------


def test_try_merge_base_both_merge_and_diff_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    """When merge fails (returncode=1) and diff --name-only also fails
    (returncode != 0), verify it returns (False, [])."""
    import subprocess as sp
    from types import SimpleNamespace

    def fake_run(args: list[str], **kwargs: object) -> SimpleNamespace:
        if args[:2] == ["git", "merge"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="merge conflict")
        if args[:2] == ["git", "diff"]:
            return SimpleNamespace(returncode=128, stdout="", stderr="fatal: error")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(sp, "run", fake_run)
    client = GitClient()
    success, conflicts = client.try_merge_base(Path("/repo"), "main")
    assert success is False
    assert conflicts == []


# ---------------------------------------------------------------------------
# 11. _parse_null_delimited_paths and _parse_name_status_output with
#     non-UTF-8 bytes (surrogateescape handling)
# ---------------------------------------------------------------------------


def test_parse_null_delimited_paths_non_utf8_bytes() -> None:
    """Non-UTF-8 bytes like 0xff are decoded via surrogateescape."""
    client = GitClient()
    raw = b"\xff_file.py\x00normal.py\x00"
    result = client._parse_null_delimited_paths(raw)
    assert len(result) == 2
    # The 0xff byte is decoded using surrogateescape
    assert result[0] == "\udcff_file.py"
    assert result[1] == "normal.py"


def test_parse_name_status_output_non_utf8_bytes() -> None:
    """Non-UTF-8 bytes in filenames are decoded via surrogateescape."""
    client = GitClient()
    raw = b"M\x00\xff_weird.py\x00"
    result = client._parse_name_status_output(raw)
    assert len(result) == 1
    assert result[0] == "\udcff_weird.py"


def test_parse_name_status_output_non_utf8_in_rename() -> None:
    """Non-UTF-8 bytes in rename source/destination are handled via surrogateescape."""
    client = GitClient()
    raw = b"R100\x00old_\xff.py\x00new_\xfe.py\x00"
    result = client._parse_name_status_output(raw)
    assert len(result) == 1
    assert result[0] == "new_\udcfe.py"


# ── Section 12: Stage-3 gap-filling tests ─────────────────────────────────


# ---------------------------------------------------------------------------
# 12a. create_task_branch error propagation
# ---------------------------------------------------------------------------


def test_create_task_branch_propagates_ls_remote_error() -> None:
    """RuntimeError from ls-remote propagates out of create_task_branch."""
    client = RaisingGitClient([("git", "ls-remote")])
    with pytest.raises(RuntimeError, match="simulated failure"):
        client.create_task_branch(Path("/repo"), "feature-x")


def test_create_task_branch_propagates_checkout_error() -> None:
    """RuntimeError from checkout -b propagates when branch is new (ls-remote empty)."""
    client = RaisingGitClient([("git", "checkout")])
    with pytest.raises(RuntimeError, match="simulated failure"):
        client.create_task_branch(Path("/repo"), "feature-x")


# ---------------------------------------------------------------------------
# 12b. commit_all error paths
# ---------------------------------------------------------------------------


def test_commit_all_propagates_commit_error() -> None:
    """When git commit fails (after status returns non-empty), RuntimeError propagates."""

    class _CommitRaisingClient(RaisingGitClient):
        def _run(self, args: list[str], cwd: Path | None = None) -> str:
            if tuple(args[:2]) == ("git", "status"):
                return "M  file.py"
            return super()._run(args, cwd)

    client = _CommitRaisingClient([("git", "commit")])
    with pytest.raises(RuntimeError, match="simulated failure"):
        client.commit_all(Path("."), "msg")


def test_commit_all_propagates_status_error() -> None:
    """When git status itself fails after add, RuntimeError propagates."""
    client = RaisingGitClient([("git", "status")])
    with pytest.raises(RuntimeError, match="simulated failure"):
        client.commit_all(Path("."), "msg")


# ---------------------------------------------------------------------------
# 12c. diff_stat combined output
# ---------------------------------------------------------------------------


def test_diff_stat_combines_tracked_and_untracked() -> None:
    """diff_stat returns tracked diff stats AND untracked file lines together."""
    client = FakeGitClient(
        {
            ("git", "diff", "--stat", "HEAD"): (
                b" src/main.py | 3 +++\n 1 file changed, 3 insertions(+)\n"
            ),
            ("git", "ls-files", "--others", "--exclude-standard", "-z"): (
                b"new_file.py\x00docs/readme.txt\x00"
            ),
        }
    )
    result = client.diff_stat(Path("."))
    assert "src/main.py | 3 +++" in result
    assert "1 file changed, 3 insertions(+)" in result
    assert " untracked | new_file.py" in result
    assert " untracked | docs/readme.txt" in result


# ---------------------------------------------------------------------------
# 12d. _normalize_repo_relative_path edge cases
# ---------------------------------------------------------------------------


def test_normalize_repo_relative_path_empty_string() -> None:
    """Empty string input normalises to empty string."""
    client = GitClient()
    assert client._normalize_repo_relative_path("") == ""


def test_normalize_repo_relative_path_multiple_leading_dot_slash() -> None:
    """Multiple leading ./ like ././foo.py normalises to foo.py."""
    client = GitClient()
    assert client._normalize_repo_relative_path("././foo.py") == "foo.py"


def test_normalize_repo_relative_path_deeply_nested() -> None:
    """Deeply nested path passes through unchanged."""
    client = GitClient()
    assert client._normalize_repo_relative_path("a/b/c/d.py") == "a/b/c/d.py"


# ---------------------------------------------------------------------------
# 12e. recent_commits and recent_changed_files error propagation
# ---------------------------------------------------------------------------


def test_recent_commits_propagates_runtime_error() -> None:
    """When _run raises during recent_commits, the error propagates."""
    client = RaisingGitClient([("git", "log")])
    with pytest.raises(RuntimeError, match="simulated failure"):
        client.recent_commits(Path("."))


def test_recent_changed_files_propagates_runtime_error() -> None:
    """When _run raises during recent_changed_files, the error propagates."""
    client = RaisingGitClient([("git", "log")])
    with pytest.raises(RuntimeError, match="simulated failure"):
        client.recent_changed_files(Path("."))


# ---------------------------------------------------------------------------
# 12f. verify_remote_branch_exists ValueError message
# ---------------------------------------------------------------------------


def test_verify_remote_branch_exists_error_contains_branch_name() -> None:
    """ValueError message includes the missing branch name."""
    client = FakeGitClient(
        {
            ("git", "ls-remote", "--heads", "origin", "nonexistent-branch"): b"",
        }
    )
    with pytest.raises(ValueError, match="nonexistent-branch"):
        client.verify_remote_branch_exists(Path("."), "nonexistent-branch")


# ---------------------------------------------------------------------------
# try_merge_base: git-diff also fails (non-zero returncode)
# ---------------------------------------------------------------------------


def test_try_merge_base_diff_filter_fails(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """When merge fails AND git diff --diff-filter=U also fails, return (False, []) and log a warning."""
    import subprocess as sp
    from types import SimpleNamespace

    def fake_run(args: list[str], **kwargs: object) -> SimpleNamespace:
        if args[:2] == ["git", "merge"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="merge conflict")
        if args[:2] == ["git", "diff"]:
            return SimpleNamespace(returncode=128, stdout="", stderr="fatal: bad revision")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(sp, "run", fake_run)
    client = GitClient()

    import logging

    with caplog.at_level(logging.WARNING):
        success, conflicts = client.try_merge_base(Path("/repo"), "main")

    assert success is False
    assert conflicts == []
    assert any("git diff --diff-filter=U failed" in rec.message for rec in caplog.records)


def test_create_task_branch_propagates_checkout_error_remote_exists() -> None:
    """RuntimeError from checkout -b propagates when remote branch exists."""

    class _RemoteExistsCheckoutRaisingClient(RaisingGitClient):
        def _run(self, args: list[str], cwd: Path | None = None) -> str:
            if tuple(args[:2]) == ("git", "ls-remote"):
                return "deadbeef\trefs/heads/feature-x"
            return super()._run(args, cwd)

    client = _RemoteExistsCheckoutRaisingClient([("git", "checkout")])
    with pytest.raises(RuntimeError, match="simulated failure"):
        client.create_task_branch(Path("/repo"), "feature-x")


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (".gitignore", "gitignore"),
        (".env", "env"),
    ],
)
def test_normalize_repo_relative_path_dotfile_lstrip_bug(path: str, expected: str) -> None:
    """Document current lstrip('./') bug where dotfiles lose their leading dot."""
    client = GitClient()
    assert client._normalize_repo_relative_path(path) == expected


def test_normalize_repo_relative_path_parent_traversal() -> None:
    """Document current lstrip('./') bug where ../foo.py becomes foo.py."""
    client = GitClient()
    assert client._normalize_repo_relative_path("../foo.py") == "foo.py"


# ---------------------------------------------------------------------------
# dotfile lstrip bug propagation through changed_files()
# ---------------------------------------------------------------------------


def test_changed_files_dotfile_lstrip_bug_propagation() -> None:
    """Document that dotfiles lose their leading dot when flowing through changed_files().

    .gitignore and .env are real dotfiles that git tracks/reports but
    _normalize_repo_relative_path(.gitignore) returns 'gitignore' because
    lstrip('./') strips the leading dot as well as './' prefixes.
    """
    client = FakeGitClient(
        {
            ("git", "diff", "--name-status", "-z", "HEAD"): b"M\x00.gitignore\x00",
            ("git", "ls-files", "--others", "--exclude-standard", "-z"): b".env\x00",
        }
    )
    result = client.changed_files(Path("."))
    # Bug documented: dotfiles lose their leading dot
    assert "gitignore" in result
    assert "env" in result
    # Originals must NOT be present — confirms the bug is active, not silently fixed
    assert ".gitignore" not in result
    assert ".env" not in result


# ---------------------------------------------------------------------------
# dotfile lstrip bug propagation through recent_changed_files()
# ---------------------------------------------------------------------------


def test_recent_changed_files_dotfile_lstrip_bug_propagation() -> None:
    """Document that dotfiles lose their leading dot in recent_changed_files().

    The FakeGitClient key uses the exact args from client.py line 101:
        ["git", "log", f"-n{max_count}", "--name-only", "--pretty=format:"]
    with the default max_count=3, giving "-n3".
    """
    client = FakeGitClient(
        {
            # Exact args used by recent_changed_files(repo_path, max_count=3)
            ("git", "log", "-n3", "--name-only", "--pretty=format:"): b".gitignore\n.env\n",
        }
    )
    result = client.recent_changed_files(Path("."))
    # Bug documented: dotfiles lose their leading dot
    assert "gitignore" in result
    assert "env" in result
    # Originals must NOT be present — confirms the bug is active
    assert ".gitignore" not in result

    assert ".env" not in result


# ---------------------------------------------------------------------------
# dotfile lstrip bug propagation through diff_stat()
# ---------------------------------------------------------------------------


def test_diff_stat_dotfile_lstrip_bug_propagation() -> None:
    """Document that untracked dotfiles lose their leading dot in diff_stat() output.

    diff_stat passes untracked paths through _normalize_repo_relative_path,
    so .gitignore becomes 'gitignore' in the 'untracked | <path>' line.
    """
    client = FakeGitClient(
        {
            ("git", "diff", "--stat", "HEAD"): b"",
            ("git", "ls-files", "--others", "--exclude-standard", "-z"): b".gitignore\x00",
        }
    )
    stat = client.diff_stat(Path("."))
    # Bug documented: dot is stripped, so the line reads "untracked | gitignore"
    assert "untracked | gitignore" in stat
    # Confirm the correct dotfile path is NOT present — bug is active
    assert "untracked | .gitignore" not in stat


# ---------------------------------------------------------------------------
# changed_files: untracked only, no diff changes

# ---------------------------------------------------------------------------


def test_changed_files_untracked_only_no_diff_changes() -> None:
    """changed_files returns only untracked files when git diff produces no output."""

    client = FakeGitClient(
        {
            ("git", "diff", "--name-status", "-z", "HEAD"): b"",
            ("git", "ls-files", "--others", "--exclude-standard", "-z"): (
                b"src/new_file.py\x00docs/readme.txt\x00"
            ),
        }
    )
    result = client.changed_files(Path("."))
    # Result is sorted and de-duped; no diff entries should contaminate it
    assert result == ["docs/readme.txt", "src/new_file.py"]


# ---------------------------------------------------------------------------
# try_merge_base: verify exact subprocess args and cwd forwarding

# ---------------------------------------------------------------------------


def test_try_merge_base_passes_correct_merge_args(monkeypatch: pytest.MonkeyPatch) -> None:
    """try_merge_base calls subprocess.run with --no-edit, origin/<branch>, and cwd=repo_path."""
    import subprocess as sp
    from types import SimpleNamespace

    captured: list[tuple[list[str], object]] = []

    def fake_run(args: list[str], **kwargs: object) -> SimpleNamespace:
        captured.append((list(args), kwargs.get("cwd")))

        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(sp, "run", fake_run)
    client = GitClient()
    repo = Path("/repo")

    success, conflicts = client.try_merge_base(repo, "main")

    assert success is True
    assert conflicts == []
    # Exactly one subprocess call on the happy path
    assert len(captured) == 1
    merge_args, cwd = captured[0]
    assert merge_args == ["git", "merge", "--no-edit", "origin/main"]
    assert cwd == repo


# ---------------------------------------------------------------------------
# diff_stat: whitespace-only tracked lines are filtered

# ---------------------------------------------------------------------------


def test_diff_stat_filters_whitespace_only_tracked_lines() -> None:
    """diff_stat drops whitespace-only lines from git diff --stat output."""
    client = FakeGitClient(
        {
            # Inject blank and tab-only lines between real content lines
            ("git", "diff", "--stat", "HEAD"): (
                b" src/main.py | 2 +-\n   \n\t\n 1 file changed\n"

            ),
            ("git", "ls-files", "--others", "--exclude-standard", "-z"): b"",
        }
    )
    stat = client.diff_stat(Path("."))
    # Real content lines must survive
    assert "src/main.py | 2 +-" in stat
    assert "1 file changed" in stat
    # No line in the output may be blank / whitespace-only
    assert all(line.strip() for line in stat.splitlines())


# ---------------------------------------------------------------------------
# clone: does not forward cwd to _run

# ---------------------------------------------------------------------------


def test_clone_does_not_pass_cwd() -> None:
    """clone() never forwards a cwd argument to _run (git clone must run in the shell CWD)."""

    class _CwdTrackingClient(GitClient):
        def __init__(self) -> None:
            self.recorded_cwd: list[Path | None] = []

        def _run(self, args: list[str], cwd: Path | None = None) -> str:
            self.recorded_cwd.append(cwd)
            return ""

    client = _CwdTrackingClient()
    client.clone("https://example.com/repo.git", Path("/ws"))

    assert len(client.recorded_cwd) == 1, "clone() should call _run exactly once"
    assert client.recorded_cwd[0] is None, "clone() must not pass a cwd to _run"


# ---------------------------------------------------------------------------
# checkout_base: pull failure is silently swallowed
# ---------------------------------------------------------------------------


def test_checkout_base_silently_ignores_pull_failure() -> None:
    """checkout_base swallows RuntimeError from 'git pull --ff-only' (client.py lines 50-53).

    The try/except ensures that a failed pull does not abort the checkout flow —
    we proceed with whatever local state is available.
    """

    class _PullFailingClient(GitClient):
        def __init__(self) -> None:
            self.attempted: list[list[str]] = []

        def _run(self, args: list[str], cwd: Path | None = None) -> str:
            self.attempted.append(args)
            if args[:2] == ["git", "pull"]:
                raise RuntimeError("network unreachable")
            return ""

    client = _PullFailingClient()
    # Must NOT raise — the RuntimeError from pull is swallowed
    client.checkout_base(Path("/repo"), "main")

    # Both checkout and pull must have been attempted
    assert len(client.attempted) == 2
    assert client.attempted[0] == ["git", "checkout", "main"]
    assert client.attempted[1] == ["git", "pull", "--ff-only"]


def test_checkout_base_pull_succeeds() -> None:
    """Happy path: checkout_base calls both checkout and pull when pull succeeds."""
    client = TrackingGitClient()
    client.checkout_base(Path("/repo"), "main")

    assert len(client.calls) == 2
    assert client.calls[0][0] == ["git", "checkout", "main"]
    assert client.calls[1][0] == ["git", "pull", "--ff-only"]


# ---------------------------------------------------------------------------
# _normalize_repo_relative_path: single-dot edge case
# ---------------------------------------------------------------------------


def test_normalize_repo_relative_path_single_dot() -> None:
    """_normalize_repo_relative_path('.') returns '' because Path('.') is '.' and lstrip('./') strips everything."""
    client = GitClient()
    assert client._normalize_repo_relative_path(".") == ""


# ---------------------------------------------------------------------------
# changed_files: result ordering
# ---------------------------------------------------------------------------


def test_changed_files_result_is_always_sorted() -> None:
    """changed_files returns paths in sorted order regardless of input order."""
    client = FakeGitClient(
        {
            ("git", "diff", "--name-status", "-z", "HEAD"): (
                b"M\x00z_last.py\x00"
                b"M\x00m_middle.py\x00"
                b"M\x00a_first.py\x00"
            ),
            ("git", "ls-files", "--others", "--exclude-standard", "-z"): b"",
        }
    )
    result = client.changed_files(Path("."))
    assert result == sorted(result)


# ---------------------------------------------------------------------------
# create_task_branch: return type contract
# ---------------------------------------------------------------------------


def test_create_task_branch_returns_bool_types() -> None:
    """create_task_branch returns actual bool values, not just truthy/falsy."""
    # Branch exists on remote → returns True
    client_exists = FakeGitClient(
        {
            ("git", "ls-remote", "--heads", "origin", "feat-123"): b"abc123\trefs/heads/feat-123\n",
            ("git", "checkout", "-b", "feat-123", "origin/feat-123"): b"",
        }
    )
    result_exists = client_exists.create_task_branch(Path("/repo"), "feat-123")
    assert isinstance(result_exists, bool)
    assert result_exists is True

    # Branch does not exist → returns False
    client_new = FakeGitClient(
        {
            ("git", "ls-remote", "--heads", "origin", "feat-456"): b"",
            ("git", "checkout", "-b", "feat-456"): b"",
        }
    )
    result_new = client_new.create_task_branch(Path("/repo"), "feat-456")
    assert isinstance(result_new, bool)
    assert result_new is False


# ---------------------------------------------------------------------------
# diff_patch: empty diff
# ---------------------------------------------------------------------------


def test_diff_patch_returns_empty_string_when_no_diff() -> None:
    """diff_patch returns an empty string when git diff --binary HEAD produces no output."""
    client = FakeGitClient(
        {
            ("git", "diff", "--binary", "HEAD"): b"",
        }
    )
    result = client.diff_patch(Path("/repo"))
    assert result == ""


# ---------------------------------------------------------------------------
# add_local_exclude: multiple patterns in order
# ---------------------------------------------------------------------------


def test_add_local_exclude_multiple_patterns_in_order(tmp_path: Path) -> None:
    """Adding three patterns sequentially writes each on its own line, in order."""
    repo = tmp_path / "repo"
    git_info = repo / ".git" / "info"
    git_info.mkdir(parents=True)

    client = GitClient()
    client.add_local_exclude(repo, "*.pyc")
    client.add_local_exclude(repo, "__pycache__/")
    client.add_local_exclude(repo, ".env")

    exclude_path = git_info / "exclude"
    lines = exclude_path.read_text().splitlines()
    assert lines == ["*.pyc", "__pycache__/", ".env"]


# ---------------------------------------------------------------------------
# Stage 2: Additional edge-case tests
# ---------------------------------------------------------------------------


def test_commit_all_preserves_special_characters_in_message() -> None:
    class TrackingFake(GitClient):
        def __init__(self) -> None:
            self.calls: list[tuple[list[str], Path | None]] = []

        def _run(self, args: list[str], cwd: Path | None = None) -> str:
            self.calls.append((args, cwd))
            if args == ["git", "status", "--porcelain"]:
                return "M  src/main.py"
            return ""

    client = TrackingFake()
    message = 'fix: "quoted" line\nsecond line\n✓ done'

    assert client.commit_all(Path("/repo"), message) is True
    assert client.calls[2] == (["git", "commit", "-m", message], Path("/repo"))


def test_rebase_onto_origin_fetch_failure_skips_rebase() -> None:
    client = SelectiveRaisingGitClient(
        failing_commands={("git", "fetch", "origin", "main")},
    )

    result = client.rebase_onto_origin(Path("/ws/repo"), "main")

    assert result is False
    assert len(client.calls) == 1


def test_checkout_base_returns_none_even_when_pull_fails() -> None:
    class PullFailingClient(GitClient):
        def _run(self, args: list[str], cwd: Path | None = None) -> str:
            if args == ["git", "pull", "--ff-only"]:
                raise RuntimeError("simulated pull failure")
            return ""

    client = PullFailingClient()

    result = client.checkout_base(Path("/repo"), "main")

    assert result is None


def test_changed_files_copy_status_returns_destination() -> None:
    client = FakeGitClient(
        {
            ("git", "diff", "--name-status", "-z", "HEAD"): (
                b"C100\x00original.py\x00copy.py\x00"
            ),
            ("git", "ls-files", "--others", "--exclude-standard", "-z"): b"",
        }
    )

    result = client.changed_files(Path("."))

    assert "copy.py" in result
    assert "original.py" not in result


def test_try_merge_base_conflict_paths_are_stripped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import subprocess as sp
    from types import SimpleNamespace

    def fake_run(args: list[str], **kwargs: object) -> SimpleNamespace:
        if args[:2] == ["git", "merge"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="conflict")
        if args[:2] == ["git", "diff"]:
            return SimpleNamespace(
                returncode=0,
                stdout="  src/a.py  \n  src/b.py  \n",
                stderr="",
            )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(sp, "run", fake_run)
    client = GitClient()

    success, conflicts = client.try_merge_base(Path("/repo"), "main")

    assert success is False
    assert conflicts == ["src/a.py", "src/b.py"]


def test_run_error_message_includes_command_args(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess as sp

    class FakeProc:
        returncode = 1
        stdout = ""
        stderr = "fatal: not a git repository"

    monkeypatch.setattr(sp, "run", lambda *a, **kw: FakeProc())  # type: ignore[attr-defined]
    client = GitClient()
    with pytest.raises(RuntimeError, match=r"git status --porcelain"):
        client._run(["git", "status", "--porcelain"])


# ---------------------------------------------------------------------------
# push_branch_force tests
# ---------------------------------------------------------------------------


def test_push_branch_force_delegates_correct_args() -> None:
    client = TrackingGitClient()
    client.push_branch_force(Path("/repos/myproject"), "my-branch")

    assert len(client.calls) == 1
    args, cwd = client.calls[0]
    assert args == ["git", "push", "--force-with-lease", "origin", "my-branch"]
    assert cwd == Path("/repos/myproject")


def test_push_branch_force_propagates_error() -> None:
    client = RaisingGitClient([("git", "push")])
    with pytest.raises(RuntimeError, match="simulated failure"):
        client.push_branch_force(Path("."), "my-branch")


# ---------------------------------------------------------------------------
# checkout_branch tests
# ---------------------------------------------------------------------------


def test_checkout_branch_delegates_correct_args() -> None:
    client = TrackingGitClient()
    client.checkout_branch(Path("/ws/repo"), "feature-x")

    assert len(client.calls) == 1
    args, cwd = client.calls[0]
    assert args == ["git", "checkout", "feature-x"]
    assert cwd == Path("/ws/repo")


def test_checkout_branch_propagates_error() -> None:
    client = RaisingGitClient([("git", "checkout")])
    with pytest.raises(RuntimeError, match="simulated failure"):
        client.checkout_branch(Path("."), "feature-x")


# ---------------------------------------------------------------------------
# rebase_onto_origin tests
# ---------------------------------------------------------------------------


class SelectiveRaisingGitClient(GitClient):
    """GitClient that raises RuntimeError for commands matching exact arg lists."""

    def __init__(self, failing_commands: set[tuple[str, ...]]) -> None:
        self.failing_commands = failing_commands
        self.calls: list[tuple[list[str], Path | None]] = []

    def _run(self, args: list[str], cwd: Path | None = None) -> str:
        self.calls.append((args, cwd))
        if tuple(args) in self.failing_commands:
            raise RuntimeError(f"simulated failure: {' '.join(args)}")
        return ""


def test_rebase_onto_origin_success_returns_true() -> None:
    client = TrackingGitClient()
    result = client.rebase_onto_origin(Path("/ws/repo"), "main")

    assert result is True
    assert len(client.calls) == 2
    assert client.calls[0] == (["git", "fetch", "origin", "main"], Path("/ws/repo"))
    assert client.calls[1] == (["git", "rebase", "origin/main"], Path("/ws/repo"))


def test_rebase_onto_origin_conflict_aborts_and_returns_false() -> None:
    """Rebase conflict triggers abort and returns False."""
    client = SelectiveRaisingGitClient(
        failing_commands={("git", "rebase", "origin/main")},
    )
    result = client.rebase_onto_origin(Path("/ws/repo"), "main")

    assert result is False
    # Should have called: fetch, rebase (failed), rebase --abort
    assert len(client.calls) == 3
    assert client.calls[0] == (["git", "fetch", "origin", "main"], Path("/ws/repo"))
    assert client.calls[1] == (["git", "rebase", "origin/main"], Path("/ws/repo"))
    assert client.calls[2] == (["git", "rebase", "--abort"], Path("/ws/repo"))


def test_rebase_onto_origin_fetch_failure_returns_false() -> None:
    client = RaisingGitClient([("git", "fetch")])
    result = client.rebase_onto_origin(Path("/ws/repo"), "main")

    assert result is False


def test_rebase_onto_origin_abort_failure_silently_caught() -> None:
    """When both rebase and rebase --abort fail, returns False without raising."""
    client = SelectiveRaisingGitClient(
        failing_commands={
            ("git", "rebase", "origin/main"),
            ("git", "rebase", "--abort"),
        },
    )
    result = client.rebase_onto_origin(Path("/ws/repo"), "main")

    assert result is False
    assert len(client.calls) == 3
