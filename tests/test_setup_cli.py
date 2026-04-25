import os
from pathlib import Path

from operations_center.entrypoints.setup.main import (
    RepoSetupAnswers,
    SetupAnswers,
    check_command_installed,
    default_orchestrator_for_statuses,
    infer_repo_key_from_clone_url,
    prepend_local_bin_to_path,
    github_https_to_ssh,
    parse_remote_branches,
    provider_default_orchestrator,
    render_env_file,
    render_settings_yaml,
    render_task_template,
)
from operations_center.entrypoints.setup.providers import ProviderStatus, summarize_provider_statuses


def test_render_settings_yaml_contains_local_repo_bootstrap_defaults() -> None:
    answers = SetupAnswers(
        plane_base_url="http://plane.local",
        plane_workspace_slug="engineering",
        plane_project_id="project-123",
        plane_api_token_env="PLANE_API_TOKEN",
        plane_api_token_value="plane-secret",
        plane_version=None,
        plane_setup_url=None,
        plane_start_command="docker compose up -d",
        plane_open_browser=True,
        git_provider="github",
        git_token_env="GITHUB_TOKEN",
        git_token_value="gh-secret",
        git_author_name="Operations Center Bot",
        git_author_email="bot@example.com",
        git_sign_commits=True,
        git_signing_key="ABC12345",
        kodo_binary="kodo",
        kodo_install_ref=None,
        kodo_team="full",
        kodo_cycles=3,
        kodo_exchanges=20,
        kodo_orchestrator="api",
        kodo_effort="standard",
        preferred_smart_provider="claude",
        preferred_fast_provider="codex",
        allowed_providers=["claude", "codex"],
        headless_required=False,
        provider_versions={},
        repos=[
            RepoSetupAnswers(
                repo_key="operations-center",
                repo_clone_url="git@github.com:you/operations-center.git",
                repo_default_branch="main",
                repo_allowed_base_branches=["main", "develop"],
                repo_validation_commands=[".venv/bin/pytest -q", ".venv/bin/ruff check ."],
                repo_bootstrap_enabled=True,
                repo_python_binary="python3",
                repo_venv_dir=".venv",
                repo_install_dev_command=".venv/bin/pip install -e .[dev]",
            )
        ],
        default_repo_key="operations-center",
    )

    rendered = render_settings_yaml(answers)

    assert "api_token_env: PLANE_API_TOKEN" in rendered
    assert "token_env: GITHUB_TOKEN" in rendered
    assert "sign_commits: true" in rendered
    assert "signing_key: ABC12345" in rendered
    assert "bootstrap_enabled: true" in rendered
    assert "venv_dir: .venv" in rendered
    assert "install_dev_command: .venv/bin/pip install -e .[dev]" in rendered
    assert "- .venv/bin/pytest -q" in rendered


def test_provider_default_orchestrator_prefers_cli_sessions() -> None:
    assert provider_default_orchestrator("claude") == "claude-code:opus"
    assert provider_default_orchestrator("codex") == "codex:gpt-5.4"


def test_default_orchestrator_for_statuses_uses_preferred_smart_provider() -> None:
    statuses = [
        ProviderStatus(
            key="claude",
            label="Claude Code",
            installed=True,
            version="2.1.88",
            auth_mode="browser_login",
            interactive_ready=True,
            headless_ready=False,
            authenticated=True,
            detail="ok",
        ),
        ProviderStatus(
            key="codex",
            label="OpenAI Codex CLI",
            installed=True,
            version="0.117.0",
            auth_mode="browser_login",
            interactive_ready=True,
            headless_ready=False,
            authenticated=True,
            detail="ok",
        ),
    ]
    assert default_orchestrator_for_statuses(statuses, preferred_smart_provider="claude") == "claude-code:opus"
    assert default_orchestrator_for_statuses(statuses, preferred_smart_provider="codex") == "codex:gpt-5.4"


def test_render_env_file_for_subscription_mode_skips_provider_secret_export() -> None:
    answers = SetupAnswers(
        plane_base_url="http://plane.local",
        plane_workspace_slug="engineering",
        plane_project_id="project-123",
        plane_api_token_env="PLANE_API_TOKEN",
        plane_api_token_value="plane-secret",
        plane_version="v1.2.3",
        plane_setup_url=None,
        plane_start_command="docker compose up -d",
        plane_open_browser=True,
        git_provider="github",
        git_token_env="GITHUB_TOKEN",
        git_token_value="gh-secret",
        git_author_name="Operations Center Bot",
        git_author_email="bot@example.com",
        git_sign_commits=False,
        git_signing_key=None,
        kodo_binary="kodo",
        kodo_install_ref="v0.4.272",
        kodo_team="full",
        kodo_cycles=3,
        kodo_exchanges=20,
        kodo_orchestrator="api",
        kodo_effort="standard",
        preferred_smart_provider="claude",
        preferred_fast_provider="codex",
        allowed_providers=["claude", "codex"],
        headless_required=False,
        provider_versions={"codex": "0.117.0"},
        repos=[
            RepoSetupAnswers(
                repo_key="operations-center",
                repo_clone_url="git@github.com:you/operations-center.git",
                repo_default_branch="main",
                repo_allowed_base_branches=["main"],
                repo_validation_commands=[".venv/bin/pytest -q"],
                repo_bootstrap_enabled=True,
                repo_python_binary="python3",
                repo_venv_dir=".venv",
                repo_install_dev_command=".venv/bin/pip install -e .[dev]",
            )
        ],
        default_repo_key="operations-center",
    )

    rendered = render_env_file(answers)

    assert "export PLANE_API_TOKEN='plane-secret'" in rendered
    assert "export OPERATIONS_CENTER_PLANE_URL='http://plane.local'" in rendered
    assert "export OPERATIONS_CENTER_PLANE_START_COMMAND='docker compose up -d'" in rendered
    assert "export OPERATIONS_CENTER_PLANE_VERSION='v1.2.3'" in rendered
    assert "export OPERATIONS_CENTER_PLANE_OPEN_BROWSER='1'" in rendered
    assert "export OPERATIONS_CENTER_KODO_INSTALL_REF='v0.4.272'" in rendered
    assert "export GITHUB_TOKEN='gh-secret'" in rendered
    assert "export OPERATIONS_CENTER_PROVIDER_CODEX_VERSION='0.117.0'" in rendered
    assert "export OPERATIONS_CENTER_PROVIDER_PREFERRED_SMART='claude'" in rendered
    assert "export OPERATIONS_CENTER_PROVIDER_PREFERRED_FAST='codex'" in rendered
    assert "export OPERATIONS_CENTER_ALLOWED_PROVIDERS='claude,codex'" in rendered
    assert "export OPERATIONS_CENTER_PROVIDER_HEADLESS_REQUIRED=0" in rendered
    assert "export OPERATIONS_CENTER_DEFAULT_REPO='operations-center'" in rendered
    assert "OPENAI_API_KEY" not in rendered


def test_render_settings_yaml_supports_multiple_repos() -> None:
    answers = SetupAnswers(
        plane_base_url="http://plane.local",
        plane_workspace_slug="engineering",
        plane_project_id="project-123",
        plane_api_token_env="PLANE_API_TOKEN",
        plane_api_token_value="plane-secret",
        plane_version=None,
        plane_setup_url=None,
        plane_start_command=None,
        plane_open_browser=False,
        git_provider="github",
        git_token_env="GITHUB_TOKEN",
        git_token_value="gh-secret",
        git_author_name="Operations Center Bot",
        git_author_email="bot@example.com",
        git_sign_commits=False,
        git_signing_key=None,
        kodo_binary="kodo",
        kodo_install_ref=None,
        kodo_team="full",
        kodo_cycles=3,
        kodo_exchanges=20,
        kodo_orchestrator="api",
        kodo_effort="standard",
        preferred_smart_provider="claude",
        preferred_fast_provider="codex",
        allowed_providers=["claude", "codex"],
        headless_required=False,
        provider_versions={},
        repos=[
            RepoSetupAnswers(
                repo_key="operations-center",
                repo_clone_url="git@github.com:you/operations-center.git",
                repo_default_branch="main",
                repo_allowed_base_branches=["main"],
                repo_validation_commands=[".venv/bin/pytest -q"],
                repo_bootstrap_enabled=True,
                repo_python_binary="python3",
                repo_venv_dir=".venv",
                repo_install_dev_command=".venv/bin/pip install -e .[dev]",
            ),
            RepoSetupAnswers(
                repo_key="other-repo",
                repo_clone_url="git@github.com:you/other-repo.git",
                repo_default_branch="develop",
                repo_allowed_base_branches=["develop", "feature/*"],
                repo_validation_commands=[".venv/bin/pytest -q"],
                repo_bootstrap_enabled=False,
                repo_python_binary="python3",
                repo_venv_dir=".venv",
                repo_install_dev_command=".venv/bin/pip install -e .[dev]",
            ),
        ],
        default_repo_key="operations-center",
    )

    rendered = render_settings_yaml(answers)

    assert "operations-center:" in rendered
    assert "other-repo:" in rendered
    assert "bootstrap_enabled: false" in rendered
    assert "- feature/*" in rendered


def test_render_task_template_uses_default_repo() -> None:
    answers = SetupAnswers(
        plane_base_url="http://plane.local",
        plane_workspace_slug="engineering",
        plane_project_id="project-123",
        plane_api_token_env="PLANE_API_TOKEN",
        plane_api_token_value="plane-secret",
        plane_version=None,
        plane_setup_url=None,
        plane_start_command=None,
        plane_open_browser=False,
        git_provider="github",
        git_token_env="GITHUB_TOKEN",
        git_token_value="gh-secret",
        git_author_name="Operations Center Bot",
        git_author_email="bot@example.com",
        git_sign_commits=False,
        git_signing_key=None,
        kodo_binary="kodo",
        kodo_install_ref=None,
        kodo_team="full",
        kodo_cycles=3,
        kodo_exchanges=20,
        kodo_orchestrator="api",
        kodo_effort="standard",
        preferred_smart_provider="claude",
        preferred_fast_provider="codex",
        allowed_providers=["claude", "codex"],
        headless_required=False,
        provider_versions={},
        repos=[
            RepoSetupAnswers(
                repo_key="operations-center",
                repo_clone_url="git@github.com:you/operations-center.git",
                repo_default_branch="main",
                repo_allowed_base_branches=["main", "develop"],
                repo_validation_commands=[".venv/bin/pytest -q"],
                repo_bootstrap_enabled=True,
                repo_python_binary="python3",
                repo_venv_dir=".venv",
                repo_install_dev_command=".venv/bin/pip install -e .[dev]",
            )
        ],
        default_repo_key="operations-center",
    )

    rendered = render_task_template(answers)

    assert "repo: operations-center" in rendered
    assert "base_branch: main" in rendered
    assert "## Goal" in rendered


def test_github_https_to_ssh_converts_github_remote() -> None:
    assert github_https_to_ssh("https://github.com/Velascat/OperationsCenter.git") == "git@github.com:Velascat/OperationsCenter.git"


def test_github_https_to_ssh_ignores_non_github_remote() -> None:
    assert github_https_to_ssh("git@gitlab.com:group/repo.git") is None


def test_parse_remote_branches_extracts_head_names() -> None:
    output = "\n".join(
        [
            "abc123\trefs/heads/main",
            "def456\trefs/heads/develop",
            "ghi789\trefs/tags/v1.0.0",
        ]
    )
    assert parse_remote_branches(output) == ["develop", "main"]


def test_infer_repo_key_from_clone_url_prefers_repo_name() -> None:
    assert infer_repo_key_from_clone_url("git@github.com:Velascat/OperationsCenter.git") == "OperationsCenter"


def test_prepend_local_bin_to_path_adds_home_local_bin(monkeypatch) -> None:
    monkeypatch.setenv("PATH", "/usr/bin")
    prepend_local_bin_to_path()
    assert str((Path.home() / ".local" / "bin")) in os.environ["PATH"]


def test_check_command_installed_uses_local_bin_path(monkeypatch) -> None:
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setattr("shutil.which", lambda command: "/home/dev/.local/bin/kodo" if command == "kodo" else None)
    assert check_command_installed("kodo") is True


def test_summarize_provider_statuses_distinguishes_states() -> None:
    summary = summarize_provider_statuses(
        [
            ProviderStatus(
                key="claude",
                label="Claude Code",
                installed=True,
                version="1.0.0",
                auth_mode="browser_login",
                interactive_ready=True,
                headless_ready=False,
                authenticated=True,
                detail="ok",
            ),
            ProviderStatus(
                key="codex",
                label="OpenAI Codex CLI",
                installed=True,
                version="1.0.0",
                auth_mode="api_key",
                interactive_ready=True,
                headless_ready=True,
                authenticated=True,
                detail="ok",
            ),
            ProviderStatus(
                key="gemini",
                label="Gemini CLI",
                installed=False,
                version=None,
                auth_mode=None,
                interactive_ready=False,
                headless_ready=False,
                authenticated=False,
                detail="missing",
            ),
        ]
    )

    assert "Claude Code: installed + logged in (1.0.0)" in summary
    assert "OpenAI Codex CLI: installed + headless ready (1.0.0)" in summary
    assert "Gemini CLI: not installed" in summary
