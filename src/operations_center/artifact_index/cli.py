# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Phase 7 — historical artifact index CLI.

Mounted into ``operations-center-audit`` as flat top-level commands:
``index``, ``index-show``, ``get-artifact``.

Conventions match the existing audit CLI (Typer, Rich tables, distinct exit
codes per failure class):

  exit 0  — success
  exit 1  — not-found (run, artifact)
  exit 2  — empty (no runs in index, ambiguous prefix)
  exit 3  — load error on the requested run
  exit 4  — path resolution failed
  exit 5  — file resolved but missing on disk
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from .errors import (
    ArtifactNotFoundError,
    ArtifactPathUnresolvableError,
)
from .multi_run import (
    IndexedRun,
    build_multi_run_index,
)
from .retrieval import read_json_artifact, read_text_artifact

app = typer.Typer(
    help="Historical artifact index commands.",
    no_args_is_help=False,
)
console = Console()


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _path_default(value: Any) -> str:
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"unserializable {type(value).__name__}: {value!r}")


def _run_summary(run: IndexedRun) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "repo_id": run.repo_id,
        "audit_type": run.audit_type,
        "producer": run.producer,
        "manifest_path": str(run.manifest_path),
        "run_status": run.run_status.value if run.run_status else None,
        "manifest_status": run.manifest_status.value if run.manifest_status else None,
        "finalized_at": run.finalized_at.isoformat() if run.finalized_at else None,
        "artifact_count": run.artifact_count,
        "is_partial": run.is_partial,
        "load_error": run.load_error,
    }


# ---------------------------------------------------------------------------
# index
# ---------------------------------------------------------------------------


@app.command("index")
def cmd_index(
    search_root: str = typer.Argument(..., help="Directory to walk for audit_manifest.json files."),
    repo: str | None = typer.Option(None, "--repo", help="Filter to one repo_id."),
    audit_type: str | None = typer.Option(None, "--audit-type", help="Filter to one audit_type."),
    repo_root: str | None = typer.Option(None, "--repo-root", help="Override managed-repo root."),
    max_depth: int = typer.Option(6, "--max-depth", help="Walk depth bound."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    """List every audit run discovered under ``search_root``."""
    idx = build_multi_run_index(
        search_root,
        repo_root=repo_root,
        repo_filter=repo,
        audit_type_filter=audit_type,
        max_depth=max_depth,
    )

    if json_output:
        typer.echo(
            json.dumps(
                {
                    "search_root": str(idx.search_root),
                    "runs": [_run_summary(r) for r in idx.runs],
                    "skipped": [(str(p), reason) for p, reason in idx.skipped_paths],
                },
                indent=2,
                default=_path_default,
                ensure_ascii=False,
            )
        )
        if not idx.runs:
            raise typer.Exit(code=2)
        return

    if not idx.runs:
        console.print(f"[yellow]No audit runs discovered under {search_root}[/yellow]")
        raise typer.Exit(code=2)

    t = Table(title=f"Audit Runs — {search_root}")
    t.add_column("run_id")
    t.add_column("repo")
    t.add_column("audit_type")
    t.add_column("status")
    t.add_column("manifest")
    t.add_column("artifacts")
    t.add_column("finalized_at")
    t.add_column("note")
    for run in idx.runs:
        note_parts: list[str] = []
        if run.is_partial:
            note_parts.append("[yellow]partial[/yellow]")
        if run.load_error:
            note_parts.append("[red]load error[/red]")
        t.add_row(
            run.run_id or "[dim]?[/dim]",
            run.repo_id or "—",
            run.audit_type or "—",
            run.run_status.value if run.run_status else "—",
            run.manifest_status.value if run.manifest_status else "—",
            str(run.artifact_count),
            run.finalized_at.isoformat() if run.finalized_at else "—",
            " ".join(note_parts) if note_parts else "",
        )
    console.print(t)


# ---------------------------------------------------------------------------
# index-show
# ---------------------------------------------------------------------------


@app.command("index-show")
def cmd_index_show(
    search_root: str = typer.Argument(...),
    run_id: str = typer.Argument(..., help="Exact run_id or unique prefix."),
    repo_root: str | None = typer.Option(None, "--repo-root"),
    kind: str | None = typer.Option(None, "--kind", help="Filter by artifact_kind."),
    stage: str | None = typer.Option(None, "--stage", help="Filter by source_stage."),
    location: str | None = typer.Option(None, "--location", help="Filter by location."),
    missing_only: bool = typer.Option(False, "--missing-only"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Show artifacts for one run (by exact run_id or unique prefix)."""
    idx = build_multi_run_index(search_root, repo_root=repo_root)
    try:
        run = idx.find_run_by_prefix(run_id)
    except ArtifactNotFoundError as exc:
        console.print(f"[red]Not found:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    except ValueError as exc:
        console.print(f"[red]Ambiguous prefix:[/red] {exc}")
        raise typer.Exit(code=2) from exc

    if run.load_error or run.index is None:
        console.print(f"[red]Run {run.run_id!r} failed to load:[/red] {run.load_error}")
        raise typer.Exit(code=3)

    artifacts = list(run.index.artifacts)
    if kind:
        artifacts = [a for a in artifacts if a.artifact_kind == kind]
    if stage:
        artifacts = [a for a in artifacts if a.source_stage == stage]
    if location:
        artifacts = [a for a in artifacts if a.location.value == location]
    if missing_only:
        artifacts = [a for a in artifacts if a.status.value == "missing"]

    if json_output:
        typer.echo(
            json.dumps(
                {
                    "run": _run_summary(run),
                    "artifacts": [
                        {
                            "artifact_id": a.artifact_id,
                            "artifact_kind": a.artifact_kind,
                            "location": a.location.value,
                            "path_role": a.path_role.value,
                            "source_stage": a.source_stage,
                            "status": a.status.value,
                            "path": a.path,
                            "resolved_path": str(a.resolved_path) if a.resolved_path else None,
                            "exists_on_disk": a.exists_on_disk,
                            "is_repo_singleton": a.is_repo_singleton,
                            "size_bytes": a.size_bytes,
                        }
                        for a in artifacts
                    ],
                },
                indent=2,
                default=_path_default,
                ensure_ascii=False,
            )
        )
        return

    t = Table(title=f"Run {run.run_id} — {len(artifacts)} artifact(s)")
    t.add_column("artifact_id")
    t.add_column("kind")
    t.add_column("stage")
    t.add_column("location")
    t.add_column("status")
    t.add_column("on disk")
    t.add_column("path")
    for a in artifacts:
        on_disk = "—" if a.exists_on_disk is None else ("✓" if a.exists_on_disk else "✗")
        t.add_row(
            a.artifact_id,
            a.artifact_kind,
            a.source_stage or "—",
            a.location.value,
            a.status.value,
            on_disk,
            a.path,
        )
    console.print(t)


# ---------------------------------------------------------------------------
# get-artifact
# ---------------------------------------------------------------------------


@app.command("get-artifact")
def cmd_get_artifact(
    search_root: str = typer.Argument(...),
    run_id: str = typer.Argument(..., help="Exact run_id or unique prefix."),
    artifact_id: str = typer.Argument(...),
    repo_root: str | None = typer.Option(None, "--repo-root"),
    no_recheck: bool = typer.Option(False, "--no-recheck", help="Skip exists-on-disk recheck."),
    print_content: bool = typer.Option(
        False, "--print-content", help="Print file content instead of just the path."
    ),
    max_bytes: int = typer.Option(
        65536, "--max-bytes", help="Truncate printed content to this size."
    ),
) -> None:
    """Resolve an artifact's absolute path; optionally print the content."""
    idx = build_multi_run_index(search_root, repo_root=repo_root)
    try:
        run = idx.find_run_by_prefix(run_id)
    except ArtifactNotFoundError as exc:
        console.print(f"[red]Not found:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    except ValueError as exc:
        console.print(f"[red]Ambiguous prefix:[/red] {exc}")
        raise typer.Exit(code=2) from exc

    if run.load_error or run.index is None:
        console.print(f"[red]Run {run.run_id!r} failed to load:[/red] {run.load_error}")
        raise typer.Exit(code=3)

    try:
        path = idx.resolve(run.run_id, artifact_id, recheck_exists=not no_recheck)
    except ArtifactNotFoundError as exc:
        console.print(f"[red]Artifact not found:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    except ArtifactPathUnresolvableError as exc:
        # Distinguish "no such file" from "couldn't resolve at all" by the message.
        msg = str(exc)
        if "no longer exists" in msg:
            console.print(f"[red]Resolved but missing:[/red] {exc}")
            raise typer.Exit(code=5) from exc
        console.print(f"[red]Unresolvable:[/red] {exc}")
        raise typer.Exit(code=4) from exc

    if not print_content:
        typer.echo(str(path))
        return

    # Print content. Use read_json_artifact for json content_type, else text.
    indexed = run.index.get_by_id(artifact_id)
    if indexed is None:
        console.print(f"[red]Artifact {artifact_id!r} disappeared from index[/red]")
        raise typer.Exit(code=1)
    if indexed.content_type == "application/json":
        try:
            data = read_json_artifact(run.index, artifact_id)
            text = json.dumps(data, indent=2, default=_path_default, ensure_ascii=False)
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red]read_json_artifact failed:[/red] {exc}")
            raise typer.Exit(code=4) from exc
    else:
        try:
            text = read_text_artifact(run.index, artifact_id)
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red]read_text_artifact failed:[/red] {exc}")
            raise typer.Exit(code=4) from exc

    if len(text) > max_bytes:
        truncated = text[:max_bytes]
        typer.echo(truncated)
        typer.echo(f"\n[... truncated {len(text) - max_bytes} bytes — pass --max-bytes to widen ...]")
    else:
        typer.echo(text)


__all__ = ["app"]
