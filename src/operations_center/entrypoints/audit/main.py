# SPDX-License-Identifier: AGPL-3.0-or-later
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
from typing import NoReturn

import typer
from rich.console import Console
from rich.table import Table

from operations_center.audit_dispatch import (
    AuditDispatchConfigError,
    ManagedAuditDispatchRequest,
    RepoLockAlreadyHeldError,
    dispatch_managed_audit,
)
from operations_center.audit_dispatch.locks import get_global_registry
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
) -> NoReturn:
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


@app.command("dispatch")
def cmd_dispatch(
    repo_id: str = typer.Argument(..., help="Managed repo ID."),
    audit_type: str = typer.Argument(..., help="Audit type."),
    allow_unverified: bool = typer.Option(False, "--allow-unverified"),
    timeout: float | None = typer.Option(None, "--timeout"),
    requested_by: str | None = typer.Option(None, "--requested-by"),
    log_dir: str | None = typer.Option(None, "--log-dir"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Positional alias for ``run`` (matches the Phase 6 spec invocation form)."""
    cmd_run(
        repo=repo_id,
        audit_type=audit_type,
        allow_unverified=allow_unverified,
        timeout=timeout,
        requested_by=requested_by,
        log_dir=log_dir,
        json_output=json_output,
    )


@app.command("list-active")
def cmd_list_active(
    json_output: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    """List currently-held audit dispatch locks across all OpsCenter processes."""
    import json as _json
    from datetime import UTC, datetime

    store = get_global_registry().store
    active = store.list_active()

    if json_output:
        typer.echo(
            _json.dumps(
                [
                    {
                        **p.to_json(),
                        **p.liveness_summary(),
                    }
                    for p in active
                ],
                indent=2,
            )
        )
        return

    if not active:
        console.print("[dim]no active audit locks[/dim]")
        return

    t = Table(title="Active Audit Locks")
    t.add_column("repo_id")
    t.add_column("audit_type")
    t.add_column("run_id")
    t.add_column("oc_pid")
    t.add_column("audit_pid")
    t.add_column("liveness")
    t.add_column("started_at")
    t.add_column("age")
    t.add_column("expected_output_dir")
    now = datetime.now(UTC)
    for p in active:
        liveness = p.liveness_summary()
        live_str = (
            f"oc={'✓' if liveness['oc_pid_alive'] else '✗'} "
            f"audit={'✓' if liveness['audit_pid_alive'] else '✗'}"
        )
        try:
            started = datetime.fromisoformat(p.started_at.replace("Z", "+00:00"))
            age = f"{(now - started).total_seconds():.0f}s"
        except ValueError:
            age = "?"
        t.add_row(
            p.repo_id,
            p.audit_type,
            p.run_id,
            str(p.oc_pid),
            str(p.audit_pid) if p.audit_pid is not None else "—",
            live_str,
            p.started_at,
            age,
            p.expected_run_status_path,
        )
    console.print(t)


@app.command("watch")
def cmd_watch(
    repo: str = typer.Option(..., "--repo", "-r", help="Managed repo ID."),
    poll_interval: float = typer.Option(2.0, "--interval", help="Poll interval in seconds."),
    timeout: float | None = typer.Option(
        None, "--timeout", help="Stop watching after this many seconds."
    ),
) -> None:
    """Stream run_status.json transitions for the audit currently held by ``repo``.

    Reads the held lock to discover ``expected_run_status_path`` (the parent
    output dir) and ``run_id``, then polls the bucket for status changes.
    """
    from pathlib import Path as _Path

    from operations_center.audit_dispatch.watcher import poll_run_status

    store = get_global_registry().store
    payload = store.read(repo)
    if payload is None:
        console.print(f"[yellow]No audit lock held for {repo!r}.[/yellow]")
        raise typer.Exit(code=1)

    output_dir = _Path(payload.expected_run_status_path)
    console.print(
        f"[dim]watching run_id={payload.run_id} under {output_dir}[/dim]"
    )
    for snapshot in poll_run_status(
        output_dir,
        payload.run_id,
        poll_interval_s=poll_interval,
        timeout_s=timeout,
    ):
        console.print(
            f"[bold]{snapshot.status}[/bold] "
            f"phase={snapshot.current_phase or '—'} "
            f"path={snapshot.path}"
        )
        if snapshot.is_terminal:
            break


@app.command("unlock")
def cmd_unlock(
    repo: str = typer.Option(..., "--repo", "-r", help="Managed repo ID to unlock."),
    force: bool = typer.Option(
        False,
        "--force",
        help="Force-release even if a recorded PID is still alive.",
    ),
) -> None:
    """Release a held audit dispatch lock for ``repo``.

    Without ``--force``, the lock is only released if all recorded PIDs are
    dead (i.e., the lock is genuinely stale). With ``--force``, the lock is
    released regardless — use only when an operator has confirmed the held
    audit is not running.
    """
    store = get_global_registry().store
    payload = store.read(repo)
    if payload is None:
        console.print(f"[yellow]No lock held for repo {repo!r}.[/yellow]")
        raise typer.Exit(code=0)

    if force:
        store.release(repo)
        console.print(f"[green]Force-released lock for {repo}.[/green]")
        raise typer.Exit(code=0)

    if payload.is_alive():
        liveness = payload.liveness_summary()
        console.print(
            f"[red]Lock for {repo!r} is still alive[/red] "
            f"(oc_pid={payload.oc_pid} alive={liveness['oc_pid_alive']}, "
            f"audit_pid={payload.audit_pid} alive={liveness['audit_pid_alive']}). "
            "Re-run with --force to override."
        )
        raise typer.Exit(code=1)

    store.release(repo)
    console.print(f"[green]Released stale lock for {repo}.[/green]")


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
