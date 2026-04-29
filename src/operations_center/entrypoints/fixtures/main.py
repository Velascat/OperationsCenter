# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
"""CLI entry point: operations-center-fixtures.

Commands
--------
  harvest   Harvest a fixture pack from a managed artifact manifest.
  inspect   Inspect a fixture pack and display its contents.
  list      List fixture packs under a root directory.

This CLI is read-only with respect to source artifacts. It:
  - loads manifests
  - builds artifact indexes
  - selects artifacts by profile
  - writes fixture packs

This CLI does NOT:
  - run audits
  - run replay tests
  - modify source artifacts
  - apply recommendations
  - modify configs
  - import managed repo code
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from operations_center.artifact_index import (
    ManifestInvalidError,
    ManifestNotFoundError,
    build_artifact_index,
    load_artifact_manifest,
)
from operations_center.fixture_harvesting import (
    CopyPolicy,
    FixturePackLoadError,
    HarvestInputError,
    HarvestProfile,
    HarvestRequest,
    harvest_fixtures,
    load_fixture_pack,
)

app = typer.Typer(
    help="Managed repo fixture harvesting commands.",
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


@app.command("harvest")
def cmd_harvest(
    manifest: str = typer.Option(..., "--manifest", "-m", help="Path to artifact_manifest.json."),
    profile: HarvestProfile = typer.Option(
        HarvestProfile.MINIMAL_FAILURE,
        "--profile", "-p",
        help="Harvest profile.",
    ),
    artifact_id: list[str] = typer.Option(
        [],
        "--artifact-id",
        help="Explicit artifact IDs to include (for MANUAL_SELECTION profile).",
    ),
    source_stage: str | None = typer.Option(None, "--stage", help="Filter by source_stage (STAGE_SLICE profile)."),
    include_singletons: bool = typer.Option(False, "--include-singletons", help="Include repo singleton artifacts."),
    output_dir: str = typer.Option(
        "tools/audit/fixtures",
        "--output-dir", "-o",
        help="Root directory for fixture packs.",
    ),
    repo_root: str | None = typer.Option(None, "--repo-root", help="Override managed repo root for path resolution."),
    max_artifact_bytes: int = typer.Option(
        10 * 1024 * 1024,
        "--max-artifact-bytes",
        help="Maximum bytes per artifact (default 10 MiB).",
    ),
    rationale: str = typer.Option("", "--rationale", help="Selection rationale note."),
) -> None:
    """Harvest a fixture pack from a managed artifact manifest."""
    index = _load_index(manifest, repo_root)

    explicit_ids = list(artifact_id) or None

    request = HarvestRequest(
        index=index,
        harvest_profile=profile,
        artifact_ids=explicit_ids,
        source_stage=source_stage,
        include_repo_singletons=include_singletons,
        copy_policy=CopyPolicy(max_artifact_bytes=max_artifact_bytes),
        selection_rationale=rationale,
    )

    out = Path(output_dir)
    try:
        pack, pack_dir = harvest_fixtures(request, out)
    except HarvestInputError as exc:
        console.print(f"[red]Harvest error:[/red] {exc}")
        raise typer.Exit(code=3) from exc

    console.print(f"[green]Fixture pack written:[/green] {pack_dir}")
    console.print(f"  pack id:   {pack.fixture_pack_id}")
    console.print(f"  profile:   {pack.harvest_profile.value}")
    console.print(f"  artifacts: {pack.artifact_count} total, {pack.copied_count} copied")
    console.print(f"  metadata-only: {pack.metadata_only_count}")


@app.command("inspect")
def cmd_inspect(
    fixture_pack: str = typer.Option(..., "--fixture-pack", "-f", help="Path to fixture_pack.json or pack directory."),
) -> None:
    """Inspect a fixture pack and display its contents."""
    path = Path(fixture_pack)
    try:
        pack = load_fixture_pack(path)
    except FileNotFoundError as exc:
        console.print(f"[red]Not found:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    except FixturePackLoadError as exc:
        console.print(f"[red]Load error:[/red] {exc}")
        raise typer.Exit(code=2) from exc

    console.print(f"[bold]Fixture Pack[/bold] — {pack.fixture_pack_id}")
    console.print(f"  repo:      {pack.source_repo_id}/{pack.source_audit_type}")
    console.print(f"  run_id:    {pack.source_run_id}")
    console.print(f"  profile:   {pack.harvest_profile.value}")
    console.print(f"  created:   {pack.created_at.isoformat()}")
    console.print(f"  artifacts: {pack.artifact_count} ({pack.copied_count} copied, {pack.metadata_only_count} metadata-only)")

    if pack.findings:
        console.print(f"  findings:  {len(pack.findings)} referenced")

    if pack.limitations:
        console.print(f"  limitations: {', '.join(pack.limitations)}")

    table = Table(show_header=True, header_style="bold")
    table.add_column("Source Artifact ID", overflow="fold")
    table.add_column("Kind")
    table.add_column("Stage")
    table.add_column("Copied")
    table.add_column("Note")

    for fa in pack.artifacts:
        note = fa.copy_error if fa.copy_error else (fa.fixture_relative_path or "")
        table.add_row(
            fa.source_artifact_id,
            fa.artifact_kind,
            fa.source_stage or "",
            "[green]yes[/green]" if fa.copied else "[yellow]no[/yellow]",
            note,
        )
    console.print(table)


@app.command("list")
def cmd_list(
    root: str = typer.Option(
        "tools/audit/fixtures",
        "--root", "-r",
        help="Root directory to search for fixture packs.",
    ),
) -> None:
    """List fixture packs under a root directory."""
    root_path = Path(root)
    if not root_path.exists():
        console.print(f"[yellow]Directory not found:[/yellow] {root_path}")
        raise typer.Exit(code=0)

    packs_found = 0
    for pack_json in sorted(root_path.glob("**/fixture_pack.json")):
        try:
            pack = load_fixture_pack(pack_json)
            console.print(
                f"  [bold]{pack.fixture_pack_id}[/bold]  "
                f"{pack.source_repo_id}/{pack.source_run_id}  "
                f"profile={pack.harvest_profile.value}  "
                f"artifacts={pack.artifact_count}"
            )
            packs_found += 1
        except Exception as exc:
            console.print(f"  [red]error loading {pack_json}:[/red] {exc}")

    if packs_found == 0:
        console.print(f"No fixture packs found under {root_path}")
