# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Phase 14 — operations-center-upstream CLI.

Forks-as-default dependency management. See
docs/architecture/backend_control_audit.md (Phase 14).

Subcommands:
  install   — install registered forks per --mode
  verify    — CI gate: confirm installed SHAs match registry
  status    — per-fork report
"""
from __future__ import annotations

import sys
from pathlib import Path

import typer

from operations_center.upstream.cli import (
    cmd_bump, cmd_drop, cmd_install, cmd_poll, cmd_push, cmd_rebase,
    cmd_status, cmd_sync, cmd_verify,
)
from operations_center.upstream.registry import InstallMode

app = typer.Typer(
    help="Fork-first dependency management (registry/install/verify/status).",
    no_args_is_help=True,
)


def _resolve_registry(path: Path | None) -> Path | None:
    if path is None:
        return None
    return path.expanduser().resolve()


@app.command("install")
def install(
    fork_id: str = typer.Argument(None, help="Fork id to install (omit if --all)"),
    mode: str = typer.Option("dev", "--mode", "-m", help="Install mode: dev | ci | prod"),
    all_forks: bool = typer.Option(False, "--all", help="Install every registered fork"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print commands without running"),
    registry: Path = typer.Option(None, "--registry", help="Override path to registry.yaml"),
) -> None:
    try:
        install_mode = InstallMode(mode)
    except ValueError:
        typer.echo(f"ERROR: invalid mode {mode!r}; valid: {[m.value for m in InstallMode]}", err=True)
        raise typer.Exit(2)
    code = cmd_install(
        fork_id=fork_id, mode=install_mode, all_forks=all_forks,
        dry_run=dry_run, registry_path=_resolve_registry(registry),
    )
    raise typer.Exit(code)


@app.command("verify")
def verify(
    registry: Path = typer.Option(None, "--registry", help="Override path to registry.yaml"),
) -> None:
    code = cmd_verify(registry_path=_resolve_registry(registry))
    raise typer.Exit(code)


@app.command("status")
def status(
    registry: Path = typer.Option(None, "--registry", help="Override path to registry.yaml"),
) -> None:
    code = cmd_status(registry_path=_resolve_registry(registry))
    raise typer.Exit(code)


@app.command("bump")
def bump(
    fork_id: str = typer.Argument(..., help="Fork id to bump"),
    to_sha: str = typer.Option(None, "--to", help="SHA to pin (omit to use HEAD)"),
    registry: Path = typer.Option(None, "--registry"),
) -> None:
    code = cmd_bump(fork_id=fork_id, to_sha=to_sha, registry_path=_resolve_registry(registry))
    raise typer.Exit(code)


@app.command("rebase")
def rebase(
    fork_id: str = typer.Argument(..., help="Fork id to rebase"),
    upstream_remote: str = typer.Option("upstream", "--upstream-remote"),
    registry: Path = typer.Option(None, "--registry"),
) -> None:
    code = cmd_rebase(fork_id=fork_id, upstream_remote=upstream_remote,
                     registry_path=_resolve_registry(registry))
    raise typer.Exit(code)


@app.command("sync")
def sync(
    fork_id: str = typer.Argument(..., help="Fork id to rebase + bump + reinstall"),
    mode: str = typer.Option("dev", "--mode", "-m"),
    skip_install: bool = typer.Option(False, "--skip-install"),
    registry: Path = typer.Option(None, "--registry"),
) -> None:
    code = cmd_sync(fork_id=fork_id, mode_str=mode, skip_install=skip_install,
                   registry_path=_resolve_registry(registry))
    raise typer.Exit(code)


@app.command("poll")
def poll(
    json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of human text"),
    registry: Path = typer.Option(None, "--registry"),
) -> None:
    """Poll upstream + emit reconcile suggestions. Exits non-zero on findings."""
    code = cmd_poll(json_output=json_output, registry_path=_resolve_registry(registry))
    raise typer.Exit(code)


@app.command("push")
def push(
    patch_id: str = typer.Argument(..., help="Patch id (fork:PATCH-NNN)"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    registry: Path = typer.Option(None, "--registry"),
) -> None:
    """Push a patch's branch as an upstream PR (auto_pr_push opt-in required)."""
    code = cmd_push(patch_id=patch_id, dry_run=dry_run,
                    registry_path=_resolve_registry(registry))
    raise typer.Exit(code)


@app.command("drop")
def drop(
    patch_id: str = typer.Argument(..., help="Patch id (fork:PATCH-NNN)"),
) -> None:
    """Mark a patch as dropped (after upstream merge). Removes the yaml."""
    code = cmd_drop(patch_id=patch_id)
    raise typer.Exit(code)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
