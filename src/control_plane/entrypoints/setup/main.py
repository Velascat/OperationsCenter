from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
import webbrowser

import typer
import yaml

from control_plane.entrypoints.setup.providers import (
    PROVIDER_SPECS,
    ProviderStatus,
    choose_preferred_provider,
    detect_all_provider_statuses,
    install_provider,
    run_interactive_provider_login,
    summarize_provider_statuses,
    write_provider_summary,
)

app = typer.Typer(help="Interactive local setup for Control Plane.")

DEFAULT_PLANE_URL = "http://localhost:8080"
DEFAULT_PLANE_START_COMMAND = "./deployment/plane/manage.sh up"
DEFAULT_SSH_KEY_PATH = Path.home() / ".ssh" / "id_ed25519"
SSH_KEY_CANDIDATES = [
    Path.home() / ".ssh" / "id_ed25519",
    Path.home() / ".ssh" / "id_rsa",
]


@dataclass
class RepoSetupAnswers:
    repo_key: str
    repo_clone_url: str
    repo_default_branch: str
    repo_allowed_base_branches: list[str]
    repo_validation_commands: list[str]
    repo_bootstrap_enabled: bool
    repo_python_binary: str
    repo_venv_dir: str
    repo_install_dev_command: str | None


@dataclass
class SetupAnswers:
    plane_base_url: str
    plane_workspace_slug: str
    plane_project_id: str
    plane_api_token_env: str
    plane_api_token_value: str
    plane_start_command: str | None
    plane_open_browser: bool
    git_provider: str
    git_token_env: str
    git_token_value: str
    git_author_name: str
    git_author_email: str
    git_sign_commits: bool
    git_signing_key: str | None
    kodo_binary: str
    kodo_team: str
    kodo_cycles: int
    kodo_exchanges: int
    kodo_orchestrator: str
    kodo_effort: str
    preferred_smart_provider: str | None
    preferred_fast_provider: str | None
    allowed_providers: list[str]
    headless_required: bool
    repos: list[RepoSetupAnswers]
    default_repo_key: str


def split_multiline(value: str) -> list[str]:
    return [line.strip() for line in value.splitlines() if line.strip()]


def split_csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def print_section(title: str, detail: str | None = None) -> None:
    typer.echo("")
    typer.secho(f"[{title}]", fg=typer.colors.CYAN, bold=True)
    if detail:
        typer.secho(detail, fg=typer.colors.BRIGHT_BLACK)


def run_local_command(command: str) -> None:
    proc = subprocess.run(command, shell=True, check=False)
    if proc.returncode != 0:
        raise typer.BadParameter(f"Plane start command failed with exit code {proc.returncode}: {command}")


def maybe_open_url(url: str) -> None:
    try:
        webbrowser.open(url)
    except Exception:
        return


def load_env_exports(env_path: Path) -> dict[str, str]:
    if not env_path.exists():
        return {}
    exports: dict[str, str] = {}
    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line.startswith("export ") or "=" not in line:
            continue
        body = line[len("export ") :]
        key, value = body.split("=", 1)
        parsed = value.strip()
        if parsed.startswith("'") and parsed.endswith("'"):
            parsed = parsed[1:-1].replace("'\"'\"'", "'")
        exports[key.strip()] = parsed
    return exports


def load_existing_config(config_path: Path) -> dict[str, object]:
    if not config_path.exists():
        return {}
    raw = yaml.safe_load(config_path.read_text())
    if isinstance(raw, dict):
        return raw
    return {}


def existing_config_value(config: dict[str, object], *keys: str) -> str | None:
    current: object = config
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    if current is None:
        return None
    return str(current)


def find_ssh_key_pair() -> tuple[Path, Path] | None:
    for private_key in SSH_KEY_CANDIDATES:
        public_key = Path(f"{private_key}.pub")
        if private_key.exists() and public_key.exists():
            return private_key, public_key
    return None


def generate_ssh_key(email: str) -> tuple[Path, Path]:
    DEFAULT_SSH_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            "ssh-keygen",
            "-t",
            "ed25519",
            "-C",
            email,
            "-f",
            str(DEFAULT_SSH_KEY_PATH),
            "-N",
            "",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise typer.BadParameter(f"ssh-keygen failed: {proc.stderr.strip()}")
    return DEFAULT_SSH_KEY_PATH, Path(f"{DEFAULT_SSH_KEY_PATH}.pub")


def start_ssh_agent() -> None:
    proc = subprocess.run(["ssh-agent", "-s"], check=False, capture_output=True, text=True)
    if proc.returncode != 0:
        raise typer.BadParameter(f"ssh-agent failed: {proc.stderr.strip()}")

    for line in proc.stdout.splitlines():
        if "=" not in line:
            continue
        key, remainder = line.split("=", 1)
        value = remainder.split(";", 1)[0]
        if key in {"SSH_AUTH_SOCK", "SSH_AGENT_PID"}:
            os.environ[key] = value


def add_ssh_key_to_agent(private_key: Path) -> None:
    start_ssh_agent()
    proc = subprocess.run(["ssh-add", str(private_key)], check=False, capture_output=True, text=True)
    if proc.returncode != 0:
        raise typer.BadParameter(f"ssh-add failed: {proc.stderr.strip()}")


def verify_github_ssh() -> tuple[bool, str]:
    proc = subprocess.run(
        ["ssh", "-o", "StrictHostKeyChecking=accept-new", "-T", "git@github.com"],
        check=False,
        capture_output=True,
        text=True,
    )
    output = "\n".join(part for part in [proc.stdout.strip(), proc.stderr.strip()] if part).strip()
    success = "successfully authenticated" in output.lower()
    return success, output


def github_https_to_ssh(url: str) -> str | None:
    prefixes = ["https://github.com/", "http://github.com/"]
    for prefix in prefixes:
        if url.startswith(prefix):
            path = url[len(prefix) :]
            return f"git@github.com:{path}"
    return None


def get_origin_remote_url(repo_root: Path) -> str | None:
    proc = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def set_origin_remote_url(repo_root: Path, remote_url: str) -> None:
    proc = subprocess.run(
        ["git", "remote", "set-url", "origin", remote_url],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise typer.BadParameter(f"git remote set-url failed: {proc.stderr.strip()}")


def ensure_github_ssh_setup(git_email: str, repo_root: Path) -> None:
    print_section("GitHub SSH")
    typer.echo("[git] Checking SSH setup...")
    key_pair = find_ssh_key_pair()
    if key_pair is None:
        typer.echo("[git] No SSH key found -> generating one")
        key_pair = generate_ssh_key(git_email)
        typer.echo(f"[git] SSH key created: {key_pair[0]}")
    else:
        typer.echo(f"[git] SSH key found -> {key_pair[0]}")

    private_key, public_key = key_pair
    add_ssh_key_to_agent(private_key)

    success, output = verify_github_ssh()
    if not success:
        typer.echo("[git] Add this SSH key to GitHub:")
        typer.echo("https://github.com/settings/keys")
        typer.echo("")
        typer.echo("--- BEGIN SSH KEY ---")
        typer.echo(public_key.read_text().strip())
        typer.echo("--- END SSH KEY ---")
        typer.prompt("Press ENTER after adding the SSH key to GitHub", default="", show_default=False)
        success, output = verify_github_ssh()

    if not success:
        raise typer.BadParameter(
            "[git] SSH verification failed. Please ensure the key was added to GitHub correctly.\n"
            f"{output}"
        )

    typer.echo("[git] SSH authentication successful")

    current_remote = get_origin_remote_url(repo_root)
    if current_remote:
        ssh_remote = github_https_to_ssh(current_remote)
        if ssh_remote and ssh_remote != current_remote:
            switch_remote = typer.confirm(
                f"Switch origin remote to SSH? [{ssh_remote}]",
                default=True,
            )
            if switch_remote:
                set_origin_remote_url(repo_root, ssh_remote)
                typer.echo(f"[git] origin updated to {ssh_remote}")


def read_saved_plane_start_command(env_path: Path) -> str | None:
    return load_env_exports(env_path).get("CONTROL_PLANE_PLANE_START_COMMAND")


def resolve_default_plane_start_command(env_path: Path) -> str | None:
    env_value = os.environ.get("CONTROL_PLANE_PLANE_START_COMMAND")
    if env_value:
        return env_value
    saved_value = read_saved_plane_start_command(env_path)
    if saved_value:
        return saved_value
    return DEFAULT_PLANE_START_COMMAND


def render_settings_yaml(answers: SetupAnswers) -> str:
    repos: dict[str, dict[str, object]] = {}
    for repo in answers.repos:
        repos[repo.repo_key] = {
            "clone_url": repo.repo_clone_url,
            "default_branch": repo.repo_default_branch,
            "validation_commands": repo.repo_validation_commands or [".venv/bin/pytest -q"],
            "allowed_base_branches": repo.repo_allowed_base_branches or [repo.repo_default_branch],
            "bootstrap_enabled": repo.repo_bootstrap_enabled,
            "python_binary": repo.repo_python_binary,
            "venv_dir": repo.repo_venv_dir,
            "install_dev_command": repo.repo_install_dev_command or f"{repo.repo_venv_dir}/bin/pip install -e .[dev]",
        }

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
            "sign_commits": answers.git_sign_commits,
            "signing_key": answers.git_signing_key,
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
        "repos": repos,
        "report_root": "tools/report/kodo_plane",
    }
    return yaml.safe_dump(config, sort_keys=False, default_flow_style=False)


def render_env_file(answers: SetupAnswers) -> str:
    lines = [
        "# Local Control Plane environment",
        "# Source this file before running worker/api/smoke commands.",
        "",
        f"export {answers.plane_api_token_env}={shell_quote(answers.plane_api_token_value)}",
        f"export CONTROL_PLANE_PLANE_URL={shell_quote(answers.plane_base_url)}",
        f"export {answers.git_token_env}={shell_quote(answers.git_token_value)}",
        f"export CONTROL_PLANE_PROVIDER_PREFERRED_SMART={shell_quote(answers.preferred_smart_provider or '')}",
        f"export CONTROL_PLANE_PROVIDER_PREFERRED_FAST={shell_quote(answers.preferred_fast_provider or '')}",
        f"export CONTROL_PLANE_ALLOWED_PROVIDERS={shell_quote(','.join(answers.allowed_providers))}",
        f"export CONTROL_PLANE_PROVIDER_HEADLESS_REQUIRED={'1' if answers.headless_required else '0'}",
    ]
    if answers.plane_start_command:
        lines.append(f"export CONTROL_PLANE_PLANE_START_COMMAND={shell_quote(answers.plane_start_command)}")
    if answers.plane_open_browser:
        lines.append("export CONTROL_PLANE_PLANE_OPEN_BROWSER='1'")
    lines.append("# Provider auth is handled by provider-specific CLIs or env vars on this machine.")
    lines.extend(
        [
            f"export CONTROL_PLANE_DEFAULT_REPO={shell_quote(answers.default_repo_key)}",
            "",
        ]
    )
    return "\n".join(lines)


def render_task_template(answers: SetupAnswers) -> str:
    repo = next(repo for repo in answers.repos if repo.repo_key == answers.default_repo_key)
    allowed_paths = "\n".join(["  - src/"])
    allowed_branches = ", ".join(repo.repo_allowed_base_branches or [repo.repo_default_branch])
    return "\n".join(
        [
            "## Execution",
            f"repo: {repo.repo_key}",
            f"base_branch: {repo.repo_default_branch}",
            "mode: goal",
            "allowed_paths:",
            allowed_paths,
            "",
            "## Goal",
            "Describe the code change you want Kodo to make.",
            "",
            "## Constraints",
            f"- Keep the selected base branch within: {allowed_branches}",
            "- Limit edits to the listed allowed paths.",
            "- Leave deployment and infrastructure files alone unless explicitly requested.",
            "",
        ]
    )


def shell_quote(value: str) -> str:
    escaped = value.replace("'", "'\"'\"'")
    return f"'{escaped}'"


def prompt_repo(repo_index: int) -> RepoSetupAnswers:
    suffix = "" if repo_index == 1 else f" #{repo_index}"
    print_section(f"Repo Setup{suffix}")
    repo_key = typer.prompt("Repo key", default="control-plane" if repo_index == 1 else f"repo_{repo_index}")
    repo_clone_url = typer.prompt(
        "Repo clone URL",
        default="git@github.com:you/control-plane.git" if repo_index == 1 else "git@github.com:you/repo.git",
    )
    repo_default_branch = typer.prompt("Repo default branch", default="main")
    repo_allowed_base_branches = split_csv(
        typer.prompt("Allowed base branches (comma separated)", default=repo_default_branch)
    )
    repo_validation_commands = split_multiline(
        typer.prompt(
            "Validation commands (use ';' between commands)",
            default=".venv/bin/pytest -q;.venv/bin/ruff check .",
        ).replace(";", "\n")
    )
    repo_bootstrap_enabled = typer.confirm("Bootstrap repo-local .venv for this repo?", default=True)
    repo_python_binary = typer.prompt("Python binary for bootstrap", default="python3")
    repo_venv_dir = typer.prompt("Repo-local venv directory", default=".venv")
    repo_install_dev_command = typer.prompt(
        "Install dev command",
        default=f"{repo_venv_dir}/bin/pip install -e .[dev]",
    ).strip()
    return RepoSetupAnswers(
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


@app.command()
def main(
    config_path: Path = typer.Option(
        Path("config/control_plane.local.yaml"),
        help="Path to write the local YAML config.",
    ),
    env_path: Path = typer.Option(
        Path(".env.control-plane.local"),
        help="Path to write the local env exports file.",
    ),
    task_template_path: Path = typer.Option(
        Path("config/plane_task_template.local.md"),
        help="Path to write a starter Plane task template.",
    ),
) -> None:
    typer.secho("Control Plane Setup", fg=typer.colors.GREEN, bold=True)
    existing_env = load_env_exports(env_path)
    existing_config = load_existing_config(config_path)

    print_section("Plane", "Local Plane service, workspace target, and API access.")
    advanced_mode = typer.confirm("Advanced setup?", default=False)
    plane_base_url = typer.prompt(
        "Plane base URL",
        default=existing_env.get("CONTROL_PLANE_PLANE_URL")
        or existing_config_value(existing_config, "plane", "base_url")
        or DEFAULT_PLANE_URL,
    )
    plane_workspace_slug = typer.prompt(
        "Plane workspace slug",
        default=existing_config_value(existing_config, "plane", "workspace_slug") or "engineering",
    )
    plane_project_id = typer.prompt(
        "Plane project id",
        default=existing_config_value(existing_config, "plane", "project_id") or "1",
    )
    plane_api_token_env = existing_config_value(existing_config, "plane", "api_token_env") or "PLANE_API_TOKEN"
    if advanced_mode:
        plane_api_token_env = typer.prompt("Plane token env var name (required)", default="PLANE_API_TOKEN")
    default_plane_start_command = resolve_default_plane_start_command(env_path)
    plane_start_command = default_plane_start_command
    plane_open_browser = False
    if advanced_mode and default_plane_start_command:
        typer.echo(f"Using saved Plane start command: {default_plane_start_command}")
        change_plane_start_command = typer.confirm(
            "Change the saved Plane start command?",
            default=False,
        )
        if change_plane_start_command:
            plane_start_command = typer.prompt(
                "Plane start command",
                default=default_plane_start_command,
            ).strip() or None
        plane_open_browser = typer.confirm(
            "Try to open the Plane URL in a browser after running `plane-up`?",
            default=True,
        )
    elif advanced_mode:
        save_plane_start_command = typer.confirm(
            "Save a local command that starts your Plane instance?",
            default=True,
        )
        if save_plane_start_command:
            plane_start_command = typer.prompt("Plane start command", default=DEFAULT_PLANE_START_COMMAND).strip() or None
            plane_open_browser = typer.confirm(
                "Try to open the Plane URL in a browser after running `plane-up`?",
                default=True,
            )

    if not advanced_mode:
        typer.echo("Using repo-managed local Plane deployment under deployment/plane/.")
        plane_open_browser = True

    if plane_start_command and not plane_open_browser and advanced_mode:
        plane_open_browser = typer.confirm(
            "Try to open the Plane URL in a browser after running `plane-up`?",
            default=True,
        )

    if plane_start_command:
        start_plane_now = typer.confirm(
            "Start/open Plane now before asking for the token?",
            default=True,
        )
    else:
        typer.echo("No Plane start command is configured. Start Plane separately, then paste the token.")
        start_plane_now = False

    if start_plane_now:
        typer.echo("Starting Plane...")
        run_local_command(plane_start_command)
        typer.echo("Plane start command finished.")
        if plane_open_browser:
            typer.echo(f"Opening {plane_base_url} ...")
            maybe_open_url(plane_base_url)
        typer.echo("Log into Plane in the browser, then create a personal access token.")
        typer.echo("Plane path: Profile Settings -> Personal Access Tokens")
    else:
        typer.echo("Plane was not started by setup.")

    existing_plane_token = existing_env.get(plane_api_token_env, "")
    if existing_plane_token:
        reuse_plane_token = typer.confirm("Reuse existing Plane API token from local env?", default=True)
        if reuse_plane_token:
            plane_api_token_value = existing_plane_token
        else:
            plane_api_token_value = typer.prompt(
                "Paste Plane API token",
                hide_input=True,
                default="",
            )
    else:
        plane_api_token_value = typer.prompt(
            "Paste Plane API token",
            hide_input=True,
            default="",
        )

    print_section("Git", "Remote host, authentication, commit identity, and SSH access.")
    git_provider = typer.prompt(
        "Git provider (remote host/service)",
        default=existing_config_value(existing_config, "git", "provider") or "github",
    )
    git_token_env = existing_config_value(existing_config, "git", "token_env") or "GITHUB_TOKEN"
    if advanced_mode:
        git_token_env = typer.prompt(
            "Git authentication key env var name (optional; used for HTTPS clone/push)",
            default=git_token_env,
        )
    typer.echo("HTTPS authentication is optional. If your remotes use SSH, leave the auth key blank.")
    existing_git_token = existing_env.get(git_token_env, "")
    if existing_git_token:
        reuse_git_token = typer.confirm("Reuse existing Git token from local env?", default=True)
        if reuse_git_token:
            git_token_value = existing_git_token
        else:
            git_token_value = typer.prompt(
                "Git authentication key/token (optional; leave blank if SSH will be used for git remotes)",
                hide_input=True,
                default="",
            )
    else:
        git_token_value = typer.prompt(
            "Git authentication key/token (optional; leave blank if SSH will be used for git remotes)",
            hide_input=True,
            default="",
        )
    git_author_name = typer.prompt(
        "Git bot author name",
        default=existing_config_value(existing_config, "git", "author_name") or "Control Plane Bot",
    )
    git_author_email = typer.prompt(
        "Git bot author email",
        default=existing_config_value(existing_config, "git", "author_email") or "control-plane-bot@example.com",
    )
    typer.echo("Commit signing is optional and only affects future signed-commit wiring.")
    git_sign_commits = typer.confirm(
        "Configure commit signing for the bot identity?",
        default=(existing_config_value(existing_config, "git", "sign_commits") or "false").lower() == "true",
    )
    existing_signing_key = existing_config_value(existing_config, "git", "signing_key") or ""
    git_signing_key: str | None = None
    if git_sign_commits:
        git_signing_key = typer.prompt(
            "Git signing key id/fingerprint (optional; leave blank to configure later)",
            default=existing_signing_key,
        ).strip() or None
    ensure_github_ssh_setup(git_author_email, Path.cwd())

    print_section("Kodo", "Execution defaults for the local coding engine.")
    kodo_binary = typer.prompt("Kodo binary", default=existing_config_value(existing_config, "kodo", "binary") or "kodo")
    kodo_team = typer.prompt("Kodo team", default=existing_config_value(existing_config, "kodo", "team") or "full")
    kodo_cycles = int(typer.prompt("Kodo cycles", default=existing_config_value(existing_config, "kodo", "cycles") or "3"))
    kodo_exchanges = int(
        typer.prompt("Kodo exchanges", default=existing_config_value(existing_config, "kodo", "exchanges") or "20")
    )
    kodo_orchestrator = typer.prompt(
        "Kodo orchestrator",
        default=existing_config_value(existing_config, "kodo", "orchestrator") or "api",
    )
    kodo_effort = typer.prompt("Kodo effort", default=existing_config_value(existing_config, "kodo", "effort") or "medium")

    print_section("Providers", "Supported Kodo backends detected on this machine.")
    statuses = detect_all_provider_statuses()
    write_provider_summary(statuses)

    for status in list(statuses):
        spec = PROVIDER_SPECS[status.key]
        if status.installed:
            continue
        if not spec.installable:
            typer.echo(f"[provider] {spec.label}: install manually if you want to use it.")
            continue
        should_install = typer.confirm(f"Install {spec.label} via {spec.install_method}?", default=status.key in {"codex", "claude"})
        if should_install:
            typer.echo(f"[provider] Installing {spec.label}...")
            install_provider(spec)
            typer.echo(f"[provider] Installed {spec.label}")
    statuses = detect_all_provider_statuses()

    for status in statuses:
        spec = PROVIDER_SPECS[status.key]
        if not status.installed:
            continue
        if status.key in {"codex", "gemini"} and not status.headless_ready:
            env_var = spec.auth_env_var
            if env_var:
                has_env = bool(os.environ.get(env_var))
                if not has_env:
                    prompt_api_key = typer.confirm(f"Use {env_var} for headless auth with {spec.label}?", default=False)
                    if prompt_api_key:
                        typer.echo(f"Set {env_var} in your shell or local env file before unattended runs.")
            if spec.interactive_login_command:
                run_login = typer.confirm(f"Launch {spec.label} login now?", default=status.key in {"codex"})
                if run_login:
                    typer.echo(f"[provider] Running: {spec.interactive_login_command}")
                    if not run_interactive_provider_login(spec, cwd=Path.cwd()):
                        typer.echo(f"[provider] {spec.label} login did not complete successfully. You can finish it later and rerun setup or providers-status.")
        elif status.key == "claude":
            typer.echo("Claude Code uses browser-based account login from the CLI.")
            run_login = typer.confirm("Launch Claude Code login now?", default=True)
            if run_login:
                typer.echo(f"[provider] Running: {spec.interactive_login_command}")
                if not run_interactive_provider_login(spec, cwd=Path.cwd()):
                    typer.echo("[provider] Claude Code login did not complete successfully. Finish it later with `claude auth login`.")

    statuses = detect_all_provider_statuses()
    typer.echo("[provider] Final provider summary:")
    typer.echo(summarize_provider_statuses(statuses))

    usable_providers = [status.key for status in statuses if status.interactive_ready]
    if not usable_providers:
        raise typer.BadParameter("No usable provider backend is ready. Install/authenticate at least one provider.")

    existing_headless_required = existing_env.get("CONTROL_PLANE_PROVIDER_HEADLESS_REQUIRED") == "1"
    headless_required = typer.confirm("Require unattended/headless provider readiness?", default=existing_headless_required)
    if headless_required and not any(status.headless_ready for status in statuses):
        raise typer.BadParameter("Headless mode requested, but no provider has API-key/headless readiness.")

    preferred_smart_provider = choose_preferred_provider(
        statuses,
        "Preferred smart provider",
        default=existing_env.get("CONTROL_PLANE_PROVIDER_PREFERRED_SMART") or "claude",
    )
    preferred_fast_provider = choose_preferred_provider(
        statuses,
        "Preferred fast provider",
        default=existing_env.get("CONTROL_PLANE_PROVIDER_PREFERRED_FAST") or "codex",
    )

    print_section("Repos", "Target repositories, branch policy, validation, and repo-local bootstrap.")
    repos: list[RepoSetupAnswers] = []
    repo_index = 1
    while True:
        repos.append(prompt_repo(repo_index))
        repo_index += 1
        if not typer.confirm("Add another repo?", default=False):
            break

    default_repo_keys = ", ".join(repo.repo_key for repo in repos)
    default_repo_key = typer.prompt("Default repo key for generated task template", default=repos[0].repo_key)
    if default_repo_key not in {repo.repo_key for repo in repos}:
        raise typer.BadParameter(
            f"Default repo key '{default_repo_key}' must match one of the configured repos: {default_repo_keys}"
        )

    answers = SetupAnswers(
        plane_base_url=plane_base_url,
        plane_workspace_slug=plane_workspace_slug,
        plane_project_id=plane_project_id,
        plane_api_token_env=plane_api_token_env,
        plane_api_token_value=plane_api_token_value,
        plane_start_command=plane_start_command,
        plane_open_browser=plane_open_browser,
        git_provider=git_provider,
        git_token_env=git_token_env,
        git_token_value=git_token_value,
        git_author_name=git_author_name,
        git_author_email=git_author_email,
        git_sign_commits=git_sign_commits,
        git_signing_key=git_signing_key,
        kodo_binary=kodo_binary,
        kodo_team=kodo_team,
        kodo_cycles=kodo_cycles,
        kodo_exchanges=kodo_exchanges,
        kodo_orchestrator=kodo_orchestrator,
        kodo_effort=kodo_effort,
        preferred_smart_provider=preferred_smart_provider,
        preferred_fast_provider=preferred_fast_provider,
        allowed_providers=usable_providers,
        headless_required=headless_required,
        repos=repos,
        default_repo_key=default_repo_key,
    )

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(render_settings_yaml(answers))
    env_path.write_text(render_env_file(answers))
    task_template_path.parent.mkdir(parents=True, exist_ok=True)
    task_template_path.write_text(render_task_template(answers))

    print_section("Done", "Local setup files were updated.")
    typer.echo(f"Config: {config_path}")
    typer.echo(f"Env:    {env_path}")
    typer.echo(f"Task:   {task_template_path}")
    typer.echo("")
    typer.echo(f"Next: source {env_path}")
    typer.echo(f"Then use {config_path} with worker/api/smoke commands.")


if __name__ == "__main__":
    app()
