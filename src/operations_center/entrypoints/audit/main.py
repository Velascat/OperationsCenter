# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
"""CLI entry point: operations-center-audit.

Commands
--------
  run              Dispatch a managed audit and wait for completion.
  status           Read and display run_status.json at a known path.
  resolve-manifest Resolve artifact_manifest_path from run_status.json.

WARNING — GOVERNANCE BYPASS
  ``operations-center-audit run`` is a low-level Phase 6 escape hatch.  It
  calls dispatch_managed_audit() directly and bypasses ALL Phase 12 governance
  policy checks: no budget tracking, no cooldown enforcement, no
  mini-regression-first policy, no manual-approval gate.

  For production dispatch use ``operations-center-governance run`` instead,
  which enforces all governance policies and writes a durable audit trail.

This CLI does not:
  - scan directories
  - index artifacts
  - harvest fixtures
  - import VideoFoundry code
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from operations_center.audit_dispatch import (
    AuditDispatchConfigError,
    ManagedAuditDispatchRequest,
    RepoLockAlreadyHeldError,
    dispatch_managed_audit,
)
from operations_center.audit_toolset import (
    ArtifactManifestPathMissingError,
    RunStatusContractError,
    RunStatusNotFoundError,
    load_run_status_entrypoint,
    resolve_artifact_manifest_path,
)

app = typer.Typer(
    help="Managed repo audit dispatch commands.",
    no_args_is_help=True,
)
console = Console()


@app.command("run")
def cmd_run(
    repo: str = typer.Option(..., "--repo", "-r", help="Managed repo ID (e.g. 'videofoundry')."),
    audit_type: str = typer.Option(
        ..., "--type", "-t", help="Audit type (e.g. 'representative')."
    ),
    allow_unverified: bool = typer.Option(
        False,
        "--allow-unverified",
        help="Allow audit types with command_status='not_yet_run'.",
    ),
    timeout: float | None = typer.Option(
        None, "--timeout", help="Hard timeout in seconds. Omit for no timeout."
    ),
    requested_by: str | None = typer.Option(None, "--requested-by", help="Caller identity."),
    log_dir: str | None = typer.Option(None, "--log-dir", help="Override stdout/stderr log dir."),
    json_output: bool = typer.Option(False, "--json", help="Output result as JSON."),
) -> None:
    """Dispatch a managed audit and wait for completion."""
    request = ManagedAuditDispatchRequest(
        repo_id=repo,
        audit_type=audit_type,
        allow_unverified_command=allow_unverified,
        timeout_seconds=timeout,
        requested_by=requested_by,
    )

    try:
        result = dispatch_managed_audit(
            request,
            log_dir=Path(log_dir) if log_dir else None,
        )
    except RepoLockAlreadyHeldError as exc:
        console.print(f"[red]Lock conflict:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    except AuditDispatchConfigError as exc:
        console.print(f"[red]Configuration error:[/red] {exc}")
        raise typer.Exit(code=3) from exc

    if json_output:
        typer.echo(result.model_dump_json(indent=2))
    else:
        _print_dispatch_result(result)

    raise typer.Exit(code=0 if result.succeeded else 1)


@app.command("status")
def cmd_status(
    run_status_path: str = typer.Argument(help="Path to run_status.json."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    """Read and display run_status.json at a known path."""
    path = Path(run_status_path)
    try:
        run_status = load_run_status_entrypoint(path)
    except RunStatusNotFoundError as exc:
        console.print(f"[red]Not found:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    except RunStatusContractError as exc:
        console.print(f"[red]Contract violation:[/red] {exc}")
        raise typer.Exit(code=2) from exc

    if json_output:
        typer.echo(run_status.model_dump_json(indent=2))
    else:
        t = Table(title="Run Status")
        t.add_column("Field")
        t.add_column("Value")
        for field_name, value in run_status.model_dump().items():
            if value is not None:
                t.add_row(field_name, str(value))
        console.print(t)


@app.command("resolve-manifest")
def cmd_resolve_manifest(
    run_status_path: str = typer.Argument(help="Path to run_status.json."),
    base_dir: str | None = typer.Option(
        None,
        "--base-dir",
        help="Base directory for resolving relative artifact_manifest_path.",
    ),
) -> None:
    """Resolve and print artifact_manifest_path from run_status.json."""
    path = Path(run_status_path)
    try:
        run_status = load_run_status_entrypoint(path)
        manifest_path = resolve_artifact_manifest_path(
            run_status,
            base_dir=Path(base_dir) if base_dir else None,
        )
    except RunStatusNotFoundError as exc:
        console.print(f"[red]Not found:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    except RunStatusContractError as exc:
        console.print(f"[red]Contract violation:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    except (ArtifactManifestPathMissingError, Exception) as exc:
        console.print(f"[red]Manifest resolution failed:[/red] {exc}")
        raise typer.Exit(code=3) from exc

    typer.echo(str(manifest_path))


def _print_dispatch_result(result) -> None:
    status_style = "green" if result.succeeded else "red"
    t = Table(title=f"Dispatch Result — {result.repo_id}/{result.audit_type}")
    t.add_column("Field")
    t.add_column("Value")
    t.add_row("run_id", result.run_id or "—")
    t.add_row("status", f"[{status_style}]{result.status.value}[/{status_style}]")
    t.add_row("failure_kind", result.failure_kind.value if result.failure_kind else "—")
    t.add_row("exit_code", str(result.process_exit_code) if result.process_exit_code is not None else "—")
    t.add_row("duration", f"{result.duration_seconds:.1f}s")
    t.add_row("run_status_path", result.run_status_path or "—")
    t.add_row("artifact_manifest_path", result.artifact_manifest_path or "—")
    t.add_row("stdout", result.stdout_path or "—")
    t.add_row("stderr", result.stderr_path or "—")
    if result.error:
        t.add_row("error", f"[red]{result.error}[/red]")
    console.print(t)
