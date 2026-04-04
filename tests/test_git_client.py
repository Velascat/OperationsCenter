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

def test_checkout_base_delegates_to_run_with_correct_args() -> None:
    calls: list[tuple[str, ...]] = []

    class TrackingFake(GitClient):
        def _run(self, args: list[str], cwd: Path | None = None) -> str:
            calls.append(tuple(args))
            return ""

    client = TrackingFake()
    client.checkout_base(Path("/repo"), "main")
    assert ("git", "checkout", "main") in calls


# ---------------------------------------------------------------------------
# changed_files — empty diff output
# ---------------------------------------------------------------------------

def test_changed_files_returns_empty_list_when_both_diff_and_ls_files_are_empty() -> None:
    client = FakeGitClient(
        {
            ("git", "diff", "--name-status", "-z", "HEAD"): b"",
            ("git", "ls-files", "--others", "--exclude-standard", "-z"): b"",
        }
    )
    assert client.changed_files(Path(".")) == []


def test_changed_files_deduplicates_via_normalization() -> None:
    """./src/a.py and src/a.py should collapse to one entry after normalization."""
    client = FakeGitClient(
        {
            ("git", "diff", "--name-status", "-z", "HEAD"): b"M\x00./src/a.py\x00",
            ("git", "ls-files", "--others", "--exclude-standard", "-z"): b"src/a.py\x00",
        }
    )
    result = client.changed_files(Path("."))
    assert result == ["src/a.py"]


# ---------------------------------------------------------------------------
# diff_stat — no untracked files
# ---------------------------------------------------------------------------

def test_diff_stat_returns_only_tracked_stat_when_no_untracked() -> None:
    client = FakeGitClient(
        {
            ("git", "diff", "--stat", "HEAD"): b" src/main.py | 2 +-\n 1 file changed\n",
            ("git", "ls-files", "--others", "--exclude-standard", "-z"): b"",
        }
    )
    stat = client.diff_stat(Path("."))
    assert "src/main.py | 2 +-" in stat
    assert "untracked" not in stat


# ---------------------------------------------------------------------------
# recent_changed_files — custom max_count
# ---------------------------------------------------------------------------

def test_recent_changed_files_respects_custom_max_count() -> None:
    client = FakeGitClient(
        {
            ("git", "log", "-n7", "--name-only", "--pretty=format:"): b"\nfile1.py\nfile2.py\n",
        }
    )
    result = client.recent_changed_files(Path("."), max_count=7)
    assert result == ["file1.py", "file2.py"]


# ---------------------------------------------------------------------------
# add_local_exclude — whitespace-padded pattern
# ---------------------------------------------------------------------------

def test_add_local_exclude_strips_whitespace_from_pattern_before_dedup(tmp_path: Path) -> None:
    client = GitClient()
    exclude_path = tmp_path / ".git" / "info" / "exclude"
    exclude_path.parent.mkdir(parents=True, exist_ok=True)
    exclude_path.write_text("mypattern\n")

    # Pattern with surrounding whitespace should be recognized as duplicate
    client.add_local_exclude(tmp_path, "  mypattern  ")

    assert exclude_path.read_text() == "mypattern\n"


# ---------------------------------------------------------------------------
# branch_allowed — multi-pattern where later pattern matches
# ---------------------------------------------------------------------------

def test_branch_allowed_returns_true_when_second_pattern_matches() -> None:
    assert branch_allowed("release/v1.0", ["main", "release/*"]) is True


# ---------------------------------------------------------------------------
# _parse_name_status_output — truncated rename entry
# ---------------------------------------------------------------------------

def test_parse_name_status_output_truncated_rename_breaks_early() -> None:
    """A rename status with only the old path (no new path) triggers the
    idx+1 >= len(parts) guard and returns files collected so far."""
    client = GitClient()
    # M file.py is complete; R100 old.py is truncated (missing destination)
    raw = b"M\x00file.py\x00R100\x00old.py"
    result = client._parse_name_status_output(raw)
    assert result == ["file.py"]


def test_parse_name_status_output_truncated_rename_only() -> None:
    """When the only entry is a truncated rename, return empty list."""
    client = GitClient()
    raw = b"R100\x00old.py"
    assert client._parse_name_status_output(raw) == []


# ---------------------------------------------------------------------------
# _run_bytes — non-UTF8 stderr decoding
# ---------------------------------------------------------------------------

def test_run_bytes_error_readable_with_non_utf8_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    """RuntimeError message should be readable even when stderr contains
    non-UTF8 bytes (decoded with errors='replace')."""
    import subprocess as sp

    class FakeProc:
        returncode = 128
        stdout = b""
        stderr = b"fatal: \xff\xfe bad encoding"

    monkeypatch.setattr(sp, "run", lambda *a, **kw: FakeProc())
    client = GitClient()
    with pytest.raises(RuntimeError, match="git command failed") as exc_info:
        client._run_bytes(["git", "diff", "--stat"])
    msg = str(exc_info.value)
    # The replacement character proves non-UTF8 bytes were handled gracefully
    assert "\ufffd" in msg or "bad encoding" in msg


# ---------------------------------------------------------------------------
# _run — error message contents
# ---------------------------------------------------------------------------

def test_run_error_message_contains_command_and_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    """RuntimeError from _run should include both the command string and stderr."""
    import subprocess as sp

    class FakeProc:
        returncode = 1
        stdout = ""
        stderr = "permission denied"

    monkeypatch.setattr(sp, "run", lambda *a, **kw: FakeProc())
    client = GitClient()
    with pytest.raises(RuntimeError) as exc_info:
        client._run(["git", "push", "origin", "main"])
    msg = str(exc_info.value)
    assert "git push origin main" in msg
    assert "permission denied" in msg


# ---------------------------------------------------------------------------
# clone — argument verification
# ---------------------------------------------------------------------------

def test_clone_passes_correct_args_to_run() -> None:
    """clone should pass ['git', 'clone', url, str(repo_path)] to _run."""
    calls: list[tuple[str, ...]] = []

    class TrackingFake(GitClient):
        def _run(self, args: list[str], cwd: Path | None = None) -> str:
            calls.append(tuple(args))
            return ""

    client = TrackingFake()
    workspace = Path("/workspace")
    client.clone("https://github.com/org/repo.git", workspace)
    assert len(calls) == 1
    assert calls[0] == ("git", "clone", "https://github.com/org/repo.git", str(workspace / "repo"))


# ---------------------------------------------------------------------------
# commit_all — cwd forwarding
# ---------------------------------------------------------------------------

def test_commit_all_forwards_repo_path_as_cwd() -> None:
    """Every _run call within commit_all should receive repo_path as cwd."""
    cwds: list[Path | None] = []

    class TrackingFake(GitClient):
        def _run(self, args: list[str], cwd: Path | None = None) -> str:
            cwds.append(cwd)
            if args[:2] == ["git", "status"]:
                return "M  src/main.py"
            return ""

    client = TrackingFake()
    repo = Path("/my/repo")
    client.commit_all(repo, "commit msg")
    # All three calls (add, status, commit) should have cwd=repo
    assert all(c == repo for c in cwds)
    assert len(cwds) == 3
