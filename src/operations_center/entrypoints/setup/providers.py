# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

__all__ = ["https_remote_to_ssh"]

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import shlex
import subprocess

import typer
from rich.console import Console
from rich.table import Table

console = Console()


@dataclass
class ProviderSpec:
    key: str
    label: str
    binary: str
    version_args: list[str]
    install_method: str
    install_command: str | None
    auth_env_var: str | None
    interactive_login_command: str | None
    installable: bool
    npm_package: str | None = None


@dataclass
class ProviderStatus:
    key: str
    label: str
    installed: bool
    version: str | None
    auth_mode: str | None
    interactive_ready: bool
    headless_ready: bool
    authenticated: bool
    detail: str


PROVIDER_SPECS = {
    "claude": ProviderSpec(
        key="claude",
        label="Claude Code",
        binary="claude",
        version_args=["--version"],
        install_method="Native installer (preferred)",
        install_command="curl -fsSL https://claude.ai/install.sh | bash",
        auth_env_var=None,
        interactive_login_command="claude auth login",
        installable=True,
        npm_package="@anthropic-ai/claude-code",
    ),
    "codex": ProviderSpec(
        key="codex",
        label="OpenAI Codex CLI",
        binary="codex",
        version_args=["--version"],
        install_method="npm",
        install_command="npm install -g @openai/codex",
        auth_env_var="CODEX_API_KEY",
        interactive_login_command="codex login",
        installable=True,
        npm_package="@openai/codex",
    ),
    "gemini": ProviderSpec(
        key="gemini",
        label="Gemini CLI",
        binary="gemini",
        version_args=["--version"],
        install_method="npm",
        install_command="npm install -g @google/gemini-cli",
        auth_env_var="GEMINI_API_KEY",
        interactive_login_command="gemini --help",
        installable=True,
        npm_package="@google/gemini-cli",
    ),
    "cursor": ProviderSpec(
        key="cursor",
        label="Cursor Agent",
        binary="cursor-agent",
        version_args=["--help"],
        install_method="Manual install",
        install_command=None,
        auth_env_var=None,
        interactive_login_command=None,
        installable=False,
    ),
}


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, check=False, capture_output=True, text=True)


def _provider_logged_in(spec: ProviderSpec) -> tuple[bool, str | None]:
    if spec.key == "codex":
        proc = _run(["codex", "login", "status"])
        output = "\n".join(part for part in [proc.stdout.strip(), proc.stderr.strip()] if part).strip()
        return proc.returncode == 0 and "logged in" in output.lower(), output or None
    if spec.key == "claude":
        proc = _run(["claude", "auth", "status"])
        output = "\n".join(part for part in [proc.stdout.strip(), proc.stderr.strip()] if part).strip()
        return '"loggedIn": true' in output or '"loggedIn":true' in output, output or None
    return False, None


def detect_provider_status(spec: ProviderSpec) -> ProviderStatus:
    binary_path = shutil.which(spec.binary)
    installed = binary_path is not None
    version: str | None = None
    detail = ""
    if installed:
        proc = _run([spec.binary, *spec.version_args])
        output = "\n".join(part for part in [proc.stdout.strip(), proc.stderr.strip()] if part).strip()
        version = output.splitlines()[0] if output else "installed"
        detail = output or "installed"
    else:
        detail = "binary not found on PATH"

    env_present = bool(spec.auth_env_var and os.environ.get(spec.auth_env_var))
    authenticated = False
    auth_detail: str | None = None
    if installed:
        authenticated, auth_detail = _provider_logged_in(spec)
    if spec.key == "cursor":
        interactive_ready = installed
        headless_ready = installed
        auth_mode = "local_binary" if installed else None
    elif spec.key == "claude":
        interactive_ready = installed and authenticated
        headless_ready = False
        auth_mode = "browser_login" if installed else None
    elif spec.key in {"codex", "gemini"}:
        interactive_ready = installed and (authenticated or spec.key == "gemini")
        headless_ready = installed and env_present
        if env_present:
            auth_mode = "api_key"
        elif authenticated:
            auth_mode = "browser_login"
        elif installed:
            auth_mode = "browser_login"
        else:
            auth_mode = None
    else:
        interactive_ready = installed
        headless_ready = env_present
        auth_mode = "api_key" if env_present else ("browser_login" if installed else None)

    return ProviderStatus(
        key=spec.key,
        label=spec.label,
        installed=installed,
        version=version,
        auth_mode=auth_mode,
        interactive_ready=interactive_ready,
        headless_ready=headless_ready,
        authenticated=authenticated,
        detail=auth_detail or detail,
    )


def detect_all_provider_statuses() -> list[ProviderStatus]:
    return [detect_provider_status(spec) for spec in PROVIDER_SPECS.values()]


def summarize_provider_statuses(statuses: list[ProviderStatus]) -> str:
    lines = []
    for status in statuses:
        if not status.installed:
            state = "not installed"
        elif status.headless_ready:
            state = "installed + headless ready"
        elif status.authenticated:
            state = "installed + logged in"
        elif status.interactive_ready:
            state = "installed + interactive ready"
        else:
            state = "installed + needs login"
        version = f" ({status.version})" if status.version else ""
        lines.append(f"- {status.label}: {state}{version}")
    return "\n".join(lines)


def ensure_command_available(binary: str) -> None:
    if shutil.which(binary) is None:
        raise typer.BadParameter(f"Required command '{binary}' is not available on PATH")


def install_provider(spec: ProviderSpec, version: str | None = None) -> None:
    if not spec.installable or not spec.install_command:
        raise typer.BadParameter(f"{spec.label} does not have an automated install path here")
    install_command = spec.install_command
    install_method = spec.install_method
    if version:
        if spec.npm_package:
            install_command = f"npm install -g {spec.npm_package}@{version}"
            install_method = "npm (pinned)"
        else:
            raise typer.BadParameter(f"{spec.label} does not support version pinning with the configured install method")
    if install_method.startswith("npm"):
        ensure_command_available("npm")
    elif spec.key == "claude":
        ensure_command_available("bash")
        ensure_command_available("curl")

    proc = subprocess.run(install_command, shell=True, check=False)
    if proc.returncode != 0:
        raise typer.BadParameter(f"{spec.label} install failed with exit code {proc.returncode}")


def run_interactive_provider_login(spec: ProviderSpec, cwd: Path | None = None) -> bool:
    if not spec.interactive_login_command:
        raise typer.BadParameter(f"{spec.label} does not support automated interactive login guidance here")
    proc = subprocess.run(shlex.split(spec.interactive_login_command), cwd=cwd, check=False)
    return proc.returncode == 0


def choose_preferred_provider(statuses: list[ProviderStatus], prompt_label: str, default: str | None = None) -> str | None:
    usable = [status for status in statuses if status.interactive_ready]
    if not usable:
        return None
    allowed = [status.key for status in usable]
    selected_default = default if default in allowed else usable[0].key
    selection = typer.prompt(f"{prompt_label} [{', '.join(allowed)}]", default=selected_default)
    if selection not in allowed:
        raise typer.BadParameter(f"Provider '{selection}' is not in usable set: {', '.join(allowed)}")
    return selection


def write_provider_summary(statuses: list[ProviderStatus]) -> None:
    table = Table(show_header=True, header_style="bold magenta", box=None, pad_edge=False)
    table.add_column("Provider", style="cyan")
    table.add_column("Status", style="white")
    table.add_column("Version", style="bright_black")
    for status in statuses:
        if not status.installed:
            state = "[red]not installed[/red]"
        elif status.headless_ready:
            state = "[green]headless ready[/green]"
        elif status.authenticated:
            state = "[green]logged in[/green]"
        else:
            state = "[yellow]needs login[/yellow]"
        table.add_row(status.label, state, status.version or "")
    console.print(table)


def https_remote_to_ssh(url: str) -> str | None:
    prefixes = ["https://github.com/", "http://github.com/"]
    for prefix in prefixes:
        if url.startswith(prefix):
            return f"git@github.com:{url[len(prefix):]}"
    return None
