from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import typer
import yaml


app = typer.Typer(help="Interactive local setup for Control Plane.")

PROVIDER_MODES = [
    "codex_subscription",
    "claude_subscription",
    "openai_api_key",
    "anthropic_api_key",
    "custom",
]


@dataclass
class SetupAnswers:
    plane_base_url: str
    plane_workspace_slug: str
    plane_project_id: str
    plane_api_token_env: str
    plane_api_token_value: str
    git_provider: str
    git_token_env: str
    git_token_value: str
    git_author_name: str
    git_author_email: str
    kodo_binary: str
    kodo_team: str
    kodo_cycles: int
    kodo_exchanges: int
    kodo_orchestrator: str
    kodo_effort: str
    provider_mode: str
    provider_secret_env: str | None
    provider_secret_value: str | None
    repo_key: str
    repo_clone_url: str
    repo_default_branch: str
    repo_allowed_base_branches: list[str]
    repo_validation_commands: list[str]
    repo_bootstrap_enabled: bool
    repo_python_binary: str
    repo_venv_dir: str
    repo_install_dev_command: str | None


def split_multiline(value: str) -> list[str]:
    return [line.strip() for line in value.splitlines() if line.strip()]


def split_csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def render_settings_yaml(answers: SetupAnswers) -> str:
    allowed_base_branches = answers.repo_allowed_base_branches or [answers.repo_default_branch]
    validation_commands = answers.repo_validation_commands or [".venv/bin/pytest -q"]
    install_dev_command = answers.repo_install_dev_command or ".venv/bin/pip install -e .[dev]"
    config = {
        "plane": {
            "base_url": answers.plane_base_url,
            "api_token_env": answers.plane_api_token_env,
            "workspace_slug": answers.plane_workspace_slug,
            "project_id": answers.plane_project_id,
        },
        "git": {
            "provider": answers.git_provider,
            "token_env": answers.git_token_env,
            "open_pr_default": False,
            "push_on_validation_failure": True,
            "author_name": answers.git_author_name,
            "author_email": answers.git_author_email,
        },
        "kodo": {
            "binary": answers.kodo_binary,
            "team": answers.kodo_team,
            "cycles": answers.kodo_cycles,
            "exchanges": answers.kodo_exchanges,
            "orchestrator": answers.kodo_orchestrator,
            "effort": answers.kodo_effort,
            "timeout_seconds": 3600,
        },
        "repos": {
            answers.repo_key: {
                "clone_url": answers.repo_clone_url,
                "default_branch": answers.repo_default_branch,
                "validation_commands": validation_commands,
                "allowed_base_branches": allowed_base_branches,
                "bootstrap_enabled": answers.repo_bootstrap_enabled,
                "python_binary": answers.repo_python_binary,
                "venv_dir": answers.repo_venv_dir,
                "install_dev_command": install_dev_command,
            }
        },
        "report_root": "tools/report/kodo_plane",
    }
    return yaml.safe_dump(
        config,
        sort_keys=False,
        default_flow_style=False,
    )


def render_env_file(answers: SetupAnswers) -> str:
    lines = [
        "# Local Control Plane environment",
        "# Source this file before running worker/api/smoke commands.",
        "",
        f"export {answers.plane_api_token_env}={shell_quote(answers.plane_api_token_value)}",
        f"export {answers.git_token_env}={shell_quote(answers.git_token_value)}",
        f"export KODO_PROVIDER_MODE={shell_quote(answers.provider_mode)}",
    ]
    if answers.provider_mode == "codex_subscription":
        lines.extend(
            [
                "# Codex subscription-backed mode selected.",
                "# Ensure your local Codex tooling is already installed and logged in on this machine.",
            ]
        )
    elif answers.provider_mode == "claude_subscription":
        lines.extend(
            [
                "# Claude subscription-backed mode selected.",
                "# Ensure your local Claude tooling is already installed and logged in on this machine.",
            ]
        )
    elif answers.provider_secret_env:
        lines.append(
            f"export {answers.provider_secret_env}={shell_quote(answers.provider_secret_value or '')}"
        )
    else:
        lines.append("# Custom provider mode selected. Add any required env vars below.")
    lines.append("")
    return "\n".join(lines)


def shell_quote(value: str) -> str:
    escaped = value.replace("'", "'\"'\"'")
    return f"'{escaped}'"


@app.command()
def init(
    config_path: Path = typer.Option(
        Path("config/control_plane.local.yaml"),
        help="Path to write the local YAML config.",
    ),
    env_path: Path = typer.Option(
        Path(".env.control-plane.local"),
        help="Path to write the local env exports file.",
    ),
) -> None:
    typer.echo("Control Plane local setup wizard")
    plane_base_url = typer.prompt("Plane base URL", default="http://plane.local")
    plane_workspace_slug = typer.prompt("Plane workspace slug", default="engineering")
    plane_project_id = typer.prompt("Plane project id")
    plane_api_token_env = typer.prompt("Plane token env var name", default="PLANE_API_TOKEN")
    plane_api_token_value = typer.prompt("Plane API token", hide_input=True, default="")

    git_provider = typer.prompt("Git provider", default="github")
    git_token_env = typer.prompt("Git token env var name", default="GITHUB_TOKEN")
    git_token_value = typer.prompt("Git token", hide_input=True, default="")
    git_author_name = typer.prompt("Git bot author name", default="Control Plane Bot")
    git_author_email = typer.prompt("Git bot author email", default="control-plane-bot@example.com")

    kodo_binary = typer.prompt("Kodo binary", default="kodo")
    kodo_team = typer.prompt("Kodo team", default="full")
    kodo_cycles = typer.prompt("Kodo cycles", default="3")
    kodo_exchanges = typer.prompt("Kodo exchanges", default="20")
    kodo_orchestrator = typer.prompt("Kodo orchestrator", default="api")
    kodo_effort = typer.prompt("Kodo effort", default="medium")

    typer.echo(f"Provider modes: {', '.join(PROVIDER_MODES)}")
    provider_mode = typer.prompt("Preferred Kodo provider mode", default="codex_subscription")
    if provider_mode not in PROVIDER_MODES:
        raise typer.BadParameter(f"Unsupported provider mode '{provider_mode}'")

    provider_secret_env: str | None = None
    provider_secret_value: str | None = None
    if provider_mode == "openai_api_key":
        provider_secret_env = typer.prompt("OpenAI API key env var name", default="OPENAI_API_KEY")
        provider_secret_value = typer.prompt("OpenAI API key", hide_input=True, default="")
    elif provider_mode == "anthropic_api_key":
        provider_secret_env = typer.prompt("Anthropic API key env var name", default="ANTHROPIC_API_KEY")
        provider_secret_value = typer.prompt("Anthropic API key", hide_input=True, default="")

    repo_key = typer.prompt("Default repo key", default="control-plane")
    repo_clone_url = typer.prompt("Repo clone URL", default="git@github.com:you/control-plane.git")
    repo_default_branch = typer.prompt("Repo default branch", default="main")
    repo_allowed_base_branches = split_csv(
        typer.prompt("Allowed base branches (comma separated)", default=repo_default_branch)
    )
    repo_validation_commands = split_multiline(
        typer.prompt(
            "Validation commands (use ';' between commands)",
            default=".venv/bin/pytest -q;.venv/bin/ruff check .",
        )
        .replace(";", "\n")
    )
    repo_bootstrap_enabled = typer.confirm("Bootstrap repo-local .venv for cloned repos?", default=True)
    repo_python_binary = typer.prompt("Python binary for bootstrap", default="python3")
    repo_venv_dir = typer.prompt("Repo-local venv directory", default=".venv")
    repo_install_dev_command = typer.prompt(
        "Install dev command",
        default=f"{repo_venv_dir}/bin/pip install -e .[dev]",
    ).strip()

    answers = SetupAnswers(
        plane_base_url=plane_base_url,
        plane_workspace_slug=plane_workspace_slug,
        plane_project_id=plane_project_id,
        plane_api_token_env=plane_api_token_env,
        plane_api_token_value=plane_api_token_value,
        git_provider=git_provider,
        git_token_env=git_token_env,
        git_token_value=git_token_value,
        git_author_name=git_author_name,
        git_author_email=git_author_email,
        kodo_binary=kodo_binary,
        kodo_team=kodo_team,
        kodo_cycles=int(kodo_cycles),
        kodo_exchanges=int(kodo_exchanges),
        kodo_orchestrator=kodo_orchestrator,
        kodo_effort=kodo_effort,
        provider_mode=provider_mode,
        provider_secret_env=provider_secret_env,
        provider_secret_value=provider_secret_value,
        repo_key=repo_key,
        repo_clone_url=repo_clone_url,
        repo_default_branch=repo_default_branch,
        repo_allowed_base_branches=repo_allowed_base_branches,
        repo_validation_commands=repo_validation_commands,
        repo_bootstrap_enabled=repo_bootstrap_enabled,
        repo_python_binary=repo_python_binary,
        repo_venv_dir=repo_venv_dir,
        repo_install_dev_command=repo_install_dev_command,
    )

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(render_settings_yaml(answers))
    env_path.write_text(render_env_file(answers))

    typer.echo(f"Wrote config: {config_path}")
    typer.echo(f"Wrote env file: {env_path}")
    typer.echo("")
    typer.echo(f"Next: source {env_path} and use {config_path} with worker/api/smoke commands.")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
