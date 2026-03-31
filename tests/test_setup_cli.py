from control_plane.entrypoints.setup.main import SetupAnswers, render_env_file, render_settings_yaml


def test_render_settings_yaml_contains_local_repo_bootstrap_defaults() -> None:
    answers = SetupAnswers(
        plane_base_url="http://plane.local",
        plane_workspace_slug="engineering",
        plane_project_id="project-123",
        plane_api_token_env="PLANE_API_TOKEN",
        plane_api_token_value="plane-secret",
        git_provider="github",
        git_token_env="GITHUB_TOKEN",
        git_token_value="gh-secret",
        git_author_name="Control Plane Bot",
        git_author_email="bot@example.com",
        kodo_binary="kodo",
        kodo_team="full",
        kodo_cycles=3,
        kodo_exchanges=20,
        kodo_orchestrator="api",
        kodo_effort="medium",
        provider_mode="codex_subscription",
        provider_secret_env=None,
        provider_secret_value=None,
        repo_key="control-plane",
        repo_clone_url="git@github.com:you/control-plane.git",
        repo_default_branch="main",
        repo_allowed_base_branches=["main", "develop"],
        repo_validation_commands=[".venv/bin/pytest -q", ".venv/bin/ruff check ."],
        repo_bootstrap_enabled=True,
        repo_python_binary="python3",
        repo_venv_dir=".venv",
        repo_install_dev_command=".venv/bin/pip install -e .[dev]",
    )

    rendered = render_settings_yaml(answers)

    assert "api_token_env: PLANE_API_TOKEN" in rendered
    assert "token_env: GITHUB_TOKEN" in rendered
    assert "bootstrap_enabled: true" in rendered
    assert "venv_dir: .venv" in rendered
    assert "install_dev_command: .venv/bin/pip install -e .[dev]" in rendered
    assert "- .venv/bin/pytest -q" in rendered


def test_render_env_file_for_subscription_mode_skips_provider_secret_export() -> None:
    answers = SetupAnswers(
        plane_base_url="http://plane.local",
        plane_workspace_slug="engineering",
        plane_project_id="project-123",
        plane_api_token_env="PLANE_API_TOKEN",
        plane_api_token_value="plane-secret",
        git_provider="github",
        git_token_env="GITHUB_TOKEN",
        git_token_value="gh-secret",
        git_author_name="Control Plane Bot",
        git_author_email="bot@example.com",
        kodo_binary="kodo",
        kodo_team="full",
        kodo_cycles=3,
        kodo_exchanges=20,
        kodo_orchestrator="api",
        kodo_effort="medium",
        provider_mode="codex_subscription",
        provider_secret_env=None,
        provider_secret_value=None,
        repo_key="control-plane",
        repo_clone_url="git@github.com:you/control-plane.git",
        repo_default_branch="main",
        repo_allowed_base_branches=["main"],
        repo_validation_commands=[".venv/bin/pytest -q"],
        repo_bootstrap_enabled=True,
        repo_python_binary="python3",
        repo_venv_dir=".venv",
        repo_install_dev_command=".venv/bin/pip install -e .[dev]",
    )

    rendered = render_env_file(answers)

    assert "export PLANE_API_TOKEN='plane-secret'" in rendered
    assert "export GITHUB_TOKEN='gh-secret'" in rendered
    assert "export KODO_PROVIDER_MODE='codex_subscription'" in rendered
    assert "Codex subscription-backed mode selected." in rendered
    assert "OPENAI_API_KEY" not in rendered
