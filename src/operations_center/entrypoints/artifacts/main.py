"""CLI entry point: operations-center-artifacts.

Commands
--------
  index    Build and summarize an artifact index from a manifest.
  list     List all indexed artifacts from a manifest.
  get      Show a specific artifact by ID.
  query    Filter artifacts from a manifest.

This CLI is read-only. It does not:
  - run audits
  - scan directories
  - modify manifests
  - harvest fixtures
  - import VideoFoundry code
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from operations_center.artifact_index import (
    ArtifactQuery,
    ManifestInvalidError,
    ManifestNotFoundError,
    build_artifact_index,
    load_artifact_manifest,
    query_artifacts,
)
from operations_center.audit_contracts.vocabulary import (
    ArtifactStatus,
    ConsumerType,
    Location,
)

app = typer.Typer(
    help="Managed repo artifact index and retrieval commands.",
    no_args_is_help=True,
)
console = Console()


def _load_index(manifest_path: str, repo_root: str | None = None):
    path = Path(manifest_path)
    try:
        manifest = load_artifact_manifest(path)
    except ManifestNotFoundError as exc:
        console.print(f"[red]Not found:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    except ManifestInvalidError as exc:
        console.print(f"[red]Invalid manifest:[/red] {exc}")
        raise typer.Exit(code=2) from exc

    root = Path(repo_root) if repo_root else None
    return build_artifact_index(manifest, path, repo_root=root)


@app.command("index")
def cmd_index(
    manifest: str = typer.Option(..., "--manifest", "-m", help="Path to artifact_manifest.json."),
    repo_root: str | None = typer.Option(None, "--repo-root", help="Override managed repo root for path resolution."),
) -> None:
    """Summarize the artifact index built from a manifest."""
    index = _load_index(manifest, repo_root)

    console.print(f"[bold]Artifact Index[/bold] — {index.source.repo_id}/{index.source.audit_type}")
    console.print(f"  run_id:          {index.source.run_id}")
    console.print(f"  manifest_status: {index.manifest_status.value}")
    console.print(f"  run_status:      {index.run_status.value}")
    console.print(f"  artifacts:       {len(index.artifacts)}")
    console.print(f"  singletons:      {len(index.singleton_artifacts)}")
    console.print(f"  excluded_paths:  {len(index.excluded_paths)}")
    if index.limitations:
        console.print(f"  limitations:     {', '.join(lim.value for lim in index.limitations)}")
    if index.warnings:
        for w in index.warnings:
            console.print(f"  [yellow]warn:[/yellow] {w}")
    if index.errors:
        for e in index.errors:
            console.print(f"  [red]error:[/red] {e}")


@app.command("list")
def cmd_list(
    manifest: str = typer.Option(..., "--manifest", "-m", help="Path to artifact_manifest.json."),
    repo_root: str | None = typer.Option(None, "--repo-root", help="Override managed repo root for path resolution."),
    singletons_only: bool = typer.Option(False, "--singletons", help="List only repo_singleton artifacts."),
) -> None:
    """List all indexed artifacts from a manifest."""
    index = _load_index(manifest, repo_root)

    artifacts = index.singleton_artifacts if singletons_only else index.artifacts
    if not artifacts:
        console.print("[dim]No artifacts.[/dim]")
        return

    t = Table(title=f"Artifacts — {index.source.repo_id}/{index.source.audit_type}")
    t.add_column("artifact_id")
    t.add_column("kind")
    t.add_column("location")
    t.add_column("status")
    t.add_column("exists")

    for a in artifacts:
        exists_str = "yes" if a.exists_on_disk else ("no" if a.exists_on_disk is False else "?")
        t.add_row(a.artifact_id, a.artifact_kind, a.location.value, a.status.value, exists_str)

    console.print(t)


@app.command("get")
def cmd_get(
    manifest: str = typer.Option(..., "--manifest", "-m", help="Path to artifact_manifest.json."),
    artifact_id: str = typer.Option(..., "--artifact-id", "-a", help="Artifact ID to retrieve."),
    repo_root: str | None = typer.Option(None, "--repo-root", help="Override managed repo root."),
) -> None:
    """Show details for a specific artifact by ID."""
    index = _load_index(manifest, repo_root)

    artifact = index.get_by_id(artifact_id)
    if artifact is None:
        console.print(f"[red]Not found:[/red] artifact '{artifact_id}' not in index.")
        raise typer.Exit(code=1)

    t = Table(title=f"Artifact: {artifact_id}")
    t.add_column("Field")
    t.add_column("Value")
    for fname, val in [
        ("artifact_kind", artifact.artifact_kind),
        ("location", artifact.location.value),
        ("path_role", artifact.path_role.value),
        ("source_stage", artifact.source_stage or "—"),
        ("status", artifact.status.value),
        ("path", artifact.path),
        ("resolved_path", str(artifact.resolved_path) if artifact.resolved_path else "unresolved"),
        ("exists_on_disk", str(artifact.exists_on_disk)),
        ("content_type", artifact.content_type),
        ("size_bytes", str(artifact.size_bytes) if artifact.size_bytes is not None else "—"),
        ("consumer_types", ", ".join(c.value for c in artifact.consumer_types) or "—"),
        ("valid_for", ", ".join(v.value for v in artifact.valid_for) or "—"),
        ("limitations", ", ".join(lim.value for lim in artifact.limitations) or "—"),
        ("is_repo_singleton", str(artifact.is_repo_singleton)),
        ("is_partial", str(artifact.is_partial)),
        ("description", artifact.description or "—"),
    ]:
        t.add_row(fname, val)

    console.print(t)


@app.command("query")
def cmd_query(
    manifest: str = typer.Option(..., "--manifest", "-m", help="Path to artifact_manifest.json."),
    kind: str | None = typer.Option(None, "--kind", help="Filter by artifact_kind."),
    location: str | None = typer.Option(None, "--location", help="Filter by location."),
    stage: str | None = typer.Option(None, "--stage", help="Filter by source_stage."),
    status: str | None = typer.Option(None, "--status", help="Filter by artifact status."),
    consumer: str | None = typer.Option(None, "--consumer", help="Filter: artifact must include this consumer_type."),
    repo_root: str | None = typer.Option(None, "--repo-root", help="Override managed repo root."),
    singletons: bool | None = typer.Option(None, "--singletons/--no-singletons", help="Filter by is_repo_singleton."),
) -> None:
    """Filter and list artifacts from a manifest."""
    index = _load_index(manifest, repo_root)

    def _parse_enum(cls, value, field_name):
        if value is None:
            return None
        try:
            return cls(value)
        except ValueError:
            valid = [e.value for e in cls]
            console.print(f"[red]Invalid {field_name}:[/red] '{value}'. Valid values: {valid}")
            raise typer.Exit(code=3)

    query_obj = ArtifactQuery(
        artifact_kind=kind,
        location=_parse_enum(Location, location, "location"),
        source_stage=stage,
        status=_parse_enum(ArtifactStatus, status, "status"),
        consumer_type=_parse_enum(ConsumerType, consumer, "consumer"),
        is_repo_singleton=singletons,
    )

    results = query_artifacts(index, query_obj)

    if not results:
        console.print("[dim]No matching artifacts.[/dim]")
        return

    t = Table(title=f"Query Results ({len(results)})")
    t.add_column("artifact_id")
    t.add_column("kind")
    t.add_column("location")
    t.add_column("status")

    for a in results:
        t.add_row(a.artifact_id, a.artifact_kind, a.location.value, a.status.value)

    console.print(t)
