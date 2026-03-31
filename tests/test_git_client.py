from pathlib import Path

from control_plane.adapters.git.client import GitClient


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
