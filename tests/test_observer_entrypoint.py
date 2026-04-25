from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from operations_center.entrypoints.observer import main as observer_main


def write_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "plane:",
                "  base_url: http://plane.local",
                "  api_token_env: PLANE_API_TOKEN",
                "  workspace_slug: ws",
                "  project_id: proj",
                "git: {}",
                "kodo: {}",
                "repos:",
                "  operations-center:",
                "    clone_url: git@github.com:Velascat/OperationsCenter.git",
                "    default_branch: main",
                f"report_root: {tmp_path / 'reports'}",
            ]
        )
    )
    return config_path


def init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    (path / "README.md").write_text("hello\n")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True, text=True)


def test_observe_repo_cli_writes_artifact_and_prints_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    config_path = write_config(tmp_path)

    monkeypatch.chdir(repo)
    monkeypatch.setattr(
        "sys.argv",
        [
            "observe-repo",
            "--config",
            str(config_path),
        ],
    )

    observer_main.main()

    captured = capsys.readouterr().out
    assert "Observer snapshot written:" in captured
    snapshot_path = Path(captured.split("Observer snapshot written:", 1)[1].strip().splitlines()[0])
    assert snapshot_path.exists()


def test_observe_repo_cli_returns_nonzero_for_missing_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = write_config(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        [
            "observe-repo",
            "--config",
            str(config_path),
            "--repo",
            "/does/not/exist",
        ],
    )

    with pytest.raises(ValueError):
        observer_main.main()
