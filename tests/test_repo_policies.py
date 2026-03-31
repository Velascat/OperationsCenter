from __future__ import annotations

from pathlib import Path
from control_plane.config import RepoPolicy, RepoPolicyDocument, RepoPolicyStore, load_settings
from control_plane.config.repo_policies import (
    configured_branch_options,
    fetch_github_repositories,
    github_owners_from_settings,
    github_repo_slug,
    resolve_branch_options,
)


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
                "  repo_a:",
                "    clone_url: git@github.com:example/repo_a.git",
                "    default_branch: main",
                "    allowed_base_branches:",
                "    - main",
                "    - develop",
                "  repo_b:",
                "    clone_url: git@github.com:example/repo_b.git",
                "    default_branch: release",
                "    allowed_base_branches:",
                "    - release",
            ]
        )
    )
    return path


def test_repo_policy_store_describes_repos_with_saved_toggles(tmp_path: Path, monkeypatch) -> None:
    config_path = write_settings(tmp_path)
    settings = load_settings(config_path)
    store = RepoPolicyStore(tmp_path / "repo_policies.json")
    monkeypatch.setattr(
        "control_plane.config.repo_policies.fetch_github_repositories",
        lambda owner, github_token=None: [],
    )
    store.save(
        RepoPolicyDocument(
            policies=[
                RepoPolicy(repo_key="repo_a", propose_enabled=False),
                RepoPolicy(repo_key="repo_b", propose_enabled=True),
            ]
        )
    )

    rows = store.describe_repos(settings)

    assert [row.repo_key for row in rows] == ["repo_a", "repo_b"]
    assert rows[0].branch_options == ["main", "develop"]
    assert rows[0].propose_enabled is False
    assert rows[1].branch_options == ["release"]
    assert rows[1].propose_enabled is True
    assert rows[0].branch_source in {"config", "github"}


def test_repo_policy_store_returns_only_enabled_repos(tmp_path: Path, monkeypatch) -> None:
    config_path = write_settings(tmp_path)
    settings = load_settings(config_path)
    store = RepoPolicyStore(tmp_path / "repo_policies.json")
    monkeypatch.setattr(
        "control_plane.config.repo_policies.fetch_github_repositories",
        lambda owner, github_token=None: [],
    )
    store.save(RepoPolicyDocument(policies=[RepoPolicy(repo_key="repo_a", propose_enabled=False)]))

    assert store.enabled_propose_repo_keys(settings) == ["repo_b"]


def test_github_owners_from_settings_extracts_unique_owners(tmp_path: Path) -> None:
    config_path = write_settings(tmp_path)
    settings = load_settings(config_path)

    assert github_owners_from_settings(settings) == ["example"]


def test_github_repo_slug_parses_common_github_urls() -> None:
    assert github_repo_slug("git@github.com:Velascat/ControlPlane.git") == "Velascat/ControlPlane"
    assert github_repo_slug("https://github.com/Velascat/ControlPlane.git") == "Velascat/ControlPlane"


def test_resolve_branch_options_falls_back_to_config_when_fetch_fails(monkeypatch) -> None:
    monkeypatch.setattr(
        "control_plane.config.repo_policies.fetch_github_branches",
        lambda _slug, github_token=None: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    branches, source = resolve_branch_options(
        clone_url="git@github.com:Velascat/ControlPlane.git",
        default_branch="main",
        allowed_base_branches=["main", "develop"],
    )

    assert branches == configured_branch_options("main", ["main", "develop"])
    assert source == "config"


def test_resolve_branch_options_uses_github_results(monkeypatch) -> None:
    monkeypatch.setattr(
        "control_plane.config.repo_policies.fetch_github_branches",
        lambda _slug, github_token=None: ["main", "release", "new-feature"],
    )

    branches, source = resolve_branch_options(
        clone_url="git@github.com:Velascat/ControlPlane.git",
        default_branch="main",
        allowed_base_branches=["main", "develop"],
    )

    assert branches == ["main", "release", "new-feature", "develop"]
    assert source == "github"


def test_fetch_github_repositories_uses_authenticated_user_repo_list(monkeypatch) -> None:
    calls: list[str] = []

    class FakeResponse:
        def __init__(self, payload, status_code: int = 200) -> None:
            self._payload = payload
            self.status_code = status_code

        def raise_for_status(self) -> None:
            return None

        def json(self):
            return self._payload

    def fake_get(url: str, headers=None, params=None, timeout=None):  # noqa: ANN001
        calls.append(url)
        if url.endswith("/user/repos"):
            return FakeResponse(
                [
                    {
                        "name": "SecretRepo",
                        "default_branch": "main",
                        "ssh_url": "git@github.com:Velascat/SecretRepo.git",
                        "owner": {"login": "Velascat"},
                    },
                    {
                        "name": "OtherOwnerRepo",
                        "default_branch": "main",
                        "ssh_url": "git@github.com:Other/OtherOwnerRepo.git",
                        "owner": {"login": "Other"},
                    },
                ]
            )
        return FakeResponse([], status_code=404)

    monkeypatch.setattr("control_plane.config.repo_policies.httpx.get", fake_get)

    repos = fetch_github_repositories("Velascat", github_token="secret-token")

    assert calls[0].endswith("/user/repos")
    assert repos == [
        {
            "name": "SecretRepo",
            "default_branch": "main",
            "clone_url": "git@github.com:Velascat/SecretRepo.git",
        }
    ]
