from pathlib import Path

import pytest

from control_plane.adapters.git.client import GitClient, branch_allowed


class FakeGitClient(GitClient):
    def __init__(self, outputs: dict[tuple[str, ...], bytes]) -> None:
        self.outputs = outputs

    def _run(self, args: list[str], cwd: Path | None = None) -> str:  # noqa: ARG002
        return self.outputs.get(tuple(args), b"").decode("utf-8", errors="replace").strip()

    def _run_bytes(self, args: list[str], cwd: Path | None = None) -> bytes:  # noqa: ARG002
        return self.outputs.get(tuple(args), b"")


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
# set_identity
# ---------------------------------------------------------------------------

def test_set_identity_calls_both_config_commands() -> None:
    calls: list[tuple[str, ...]] = []

    class TrackingFake(GitClient):
        def _run(self, args: list[str], cwd: Path | None = None) -> str:
            calls.append(tuple(args))
            return ""

    client = TrackingFake()
    client.set_identity(Path("."), "Bot", "bot@example.com")
    assert ("git", "config", "user.name", "Bot") in calls
    assert ("git", "config", "user.email", "bot@example.com") in calls


# ---------------------------------------------------------------------------
# push_branch
# ---------------------------------------------------------------------------

def test_push_branch_calls_push() -> None:
    calls: list[tuple[str, ...]] = []

    class TrackingFake(GitClient):
        def _run(self, args: list[str], cwd: Path | None = None) -> str:
            calls.append(tuple(args))
            return ""

    client = TrackingFake()
    client.push_branch(Path("."), "feature-1")
    assert ("git", "push", "-u", "origin", "feature-1") in calls


# ---------------------------------------------------------------------------
# checkout_base
# ---------------------------------------------------------------------------

def test_checkout_base_delegates_to_git_checkout() -> None:
    calls: list[tuple[str, ...]] = []

    class TrackingFake(GitClient):
        def _run(self, args: list[str], cwd: Path | None = None) -> str:
            calls.append(tuple(args))
            return ""

    client = TrackingFake()
    client.checkout_base(Path("/repo"), "main")
    assert ("git", "checkout", "main") in calls


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


# ===========================================================================
# COVERAGE GAP AUDIT — untested behaviours per method in client.py
# ===========================================================================
#
# changed_files (line 103)
#   GAP: Deduplication when the same file appears in BOTH the tracked diff
#        output and untracked ls-files output.  The method uses sorted(set(...))
#        but no test exercises this overlap path.
#
# _parse_name_status_output (line 137)
#   GAP: Copy status ("C") tested only alongside Rename in a combined test.
#        No isolated test for a Copy entry verifying only the destination file
#        is returned (not the source).
#
# add_local_exclude (line 27)
#   GAP: Leading/trailing whitespace in pattern argument.  The method calls
#        pattern.strip() for both the dedup check and the written value, but no
#        test passes e.g. "  pattern  " to verify whitespace is stripped on
#        write and dedup still works against a clean existing line.
#
# try_merge_base (line 57)
#   GAP: Merge fails (returncode != 0) but git diff returns NO unmerged paths
#        (empty stdout).  Current conflict test always supplies file names.
#        This edge case should return (False, []).
#
# commit_all (line 126)
#   GAP: The exact commit message string is forwarded to _run.  Existing test
#        only checks the return value (True); no assertion that the message
#        argument reaches the git commit command.
#
# clone (line 22)
#   GAP: clone_url is correctly forwarded as an argument to _run.  Existing
#        test only checks the returned Path; no assertion that _run received
#        ["git", "clone", <url>, ...].
#
# _run / _run_bytes (lines 9, 15)
#   GAP: cwd parameter is forwarded to subprocess.run.  Current monkeypatch
#        tests ignore kwargs; no assertion verifies cwd reaches subprocess.
#
# checkout_base (line 45)
#   GAP: cwd parameter is forwarded to _run.  The TrackingFake records args
#        but ignores the cwd keyword; no assertion that cwd=repo_path is
#        passed through.


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
