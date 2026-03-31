from pathlib import Path

from control_plane.adapters.git.client import GitClient


class FakeGitClient(GitClient):
    def __init__(self, output: str) -> None:
        self.output = output

    def _run(self, args: list[str], cwd: Path | None = None) -> str:  # noqa: ARG002
        return self.output


def test_changed_files_handles_rename_output() -> None:
    output = "R100\x00old/path.py\x00new/path.py\x00M\x00src/main.py\x00"
    client = FakeGitClient(output)

    assert client.changed_files(Path(".")) == ["new/path.py", "src/main.py"]
