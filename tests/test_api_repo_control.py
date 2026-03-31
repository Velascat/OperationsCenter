from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from control_plane.entrypoints.api.main import app


def write_settings(tmp_path: Path) -> Path:
    path = tmp_path / "control_plane.local.yaml"
    path.write_text(
        "\n".join(
            [
                "plane:",
                "  base_url: http://localhost:8080",
                "  api_token_env: PLANE_API_TOKEN",
                "  workspace_slug: control-plane",
                "  project_id: project-123",
                "git:",
                "  provider: github",
                "kodo:",
                "  binary: kodo",
                "repos:",
                "  code_youtube_shorts:",
                "    clone_url: git@github.com:Velascat/code_youtube_shorts.git",
                "    default_branch: new-feature",
                "    allowed_base_branches:",
                "    - new-feature",
            ]
        )
    )
    return path


def test_repos_endpoint_returns_repo_registry(tmp_path: Path, monkeypatch) -> None:
    config_path = write_settings(tmp_path)
    monkeypatch.setenv("CONTROL_PLANE_CONFIG", str(config_path))
    monkeypatch.setattr(
        "control_plane.config.repo_policies.fetch_github_repositories",
        lambda owner, github_token=None: [
            {
                "name": "code_youtube_shorts",
                "default_branch": "new-feature",
                "clone_url": "git@github.com:Velascat/code_youtube_shorts.git",
            },
            {
                "name": "another_repo",
                "default_branch": "main",
                "clone_url": "git@github.com:Velascat/another_repo.git",
            },
        ],
    )
    monkeypatch.setattr(
        "control_plane.config.repo_policies.fetch_github_branches",
        lambda _slug, github_token=None: ["new-feature", "main"],
    )

    client = TestClient(app)
    response = client.get("/repos")

    assert response.status_code == 200
    payload = response.json()
    assert payload["repos"][0]["repo_key"] == "code_youtube_shorts"
    assert payload["repos"][0]["branch_options"] == ["new-feature", "main"]
    assert payload["repos"][0]["propose_enabled"] is True
    assert payload["repos"][0]["branch_source"] == "github"
    assert payload["repos"][0]["configured"] is True
    assert payload["repos"][1]["repo_key"] == "another_repo"
    assert payload["repos"][1]["configured"] is False
    assert payload["repos"][1]["propose_enabled"] is False


def test_repo_policy_update_persists_toggle(tmp_path: Path, monkeypatch) -> None:
    config_path = write_settings(tmp_path)
    monkeypatch.setenv("CONTROL_PLANE_CONFIG", str(config_path))

    client = TestClient(app)
    response = client.put(
        "/repo-policies",
        json={
            "policies": [
                {"repo_key": "code_youtube_shorts", "propose_enabled": False},
            ]
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["policies"][0]["propose_enabled"] is False
    policy_path = Path(payload["policy_path"])
    assert policy_path.exists()


def test_import_repo_writes_discovered_repo_into_config(tmp_path: Path, monkeypatch) -> None:
    config_path = write_settings(tmp_path)
    monkeypatch.setenv("CONTROL_PLANE_CONFIG", str(config_path))
    monkeypatch.setattr(
        "control_plane.config.repo_policies.fetch_github_repositories",
        lambda owner, github_token=None: [
            {
                "name": "another_repo",
                "default_branch": "main",
                "clone_url": "git@github.com:Velascat/another_repo.git",
            },
        ],
    )
    monkeypatch.setattr(
        "control_plane.config.repo_policies.fetch_github_branches",
        lambda _slug, github_token=None: ["dev", "main", "new-feature"],
    )

    client = TestClient(app)
    response = client.post("/repos/import", json={"repo_key": "another_repo"})

    assert response.status_code == 200
    payload = response.json()
    imported = next(item for item in payload["repos"] if item["repo_key"] == "another_repo")
    assert imported["configured"] is True
    written = config_path.read_text()
    assert "another_repo:" in written
    assert "clone_url: git@github.com:Velascat/another_repo.git" in written
    assert "allowed_base_branches:" in written
    assert "- new-feature" in written
