"""CLI entry point: operations-center-replay.

Commands
--------
  run      Run a slice replay against a fixture pack.
  inspect  Inspect a previously written replay report.

This CLI does NOT:
  - run full audits
  - harvest fixtures
  - modify fixture packs or source artifacts
  - apply recommendations
  - create regression suites
  - import managed repo code
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from operations_center.slice_replay import (
    ReplayInputError,
    ReplayReportLoadError,
    SliceReplayProfile,
    SliceReplayRequest,
    load_replay_report,
    run_slice_replay,
    write_replay_report,
)

app = typer.Typer(
    help="Slice replay testing from fixture packs.",
    no_args_is_help=True,
)
console = Console()


@app.command("run")
def cmd_run(
    fixture_pack: str = typer.Option(
        ..., "--fixture-pack", "-f", help="Path to fixture_pack.json or pack directory."
    ),
    profile: SliceReplayProfile = typer.Option(
        SliceReplayProfile.FIXTURE_INTEGRITY,
        "--profile", "-p",
        help="Replay profile.",
    ),
    source_stage: str | None = typer.Option(None, "--stage", help="Filter by source_stage (STAGE_SLICE profile)."),
    artifact_kind: str | None = typer.Option(None, "--kind", help="Filter by artifact_kind."),
    max_artifact_bytes: int = typer.Option(
        10 * 1024 * 1024, "--max-artifact-bytes", help="Max bytes per artifact when reading content."
    ),
    fail_fast: bool = typer.Option(False, "--fail-fast", help="Stop after first required failure."),
    output_dir: str = typer.Option(
        "tools/audit/report/slice_replay",
        "--output-dir", "-o",
        help="Root directory for replay reports.",
    ),
) -> None:
    """Run a slice replay against a fixture pack and write a report."""
    request = SliceReplayRequest(
        fixture_pack_path=Path(fixture_pack),
        replay_profile=profile,
        source_stage=source_stage,
        artifact_kind=artifact_kind,
        max_artifact_bytes=max_artifact_bytes,
        fail_fast=fail_fast,
    )

    try:
        report = run_slice_replay(request)
    except ReplayInputError as exc:
        console.print(f"[red]Replay error:[/red] {exc}")
        raise typer.Exit(code=3) from exc

    out = Path(output_dir)
    try:
        report_path = write_replay_report(report, out)
    except Exception as exc:
        console.print(f"[yellow]Warning: could not write report:[/yellow] {exc}")
        report_path = None

    status_color = "green" if report.status == "passed" else "red" if report.status == "failed" else "yellow"
    console.print(f"[{status_color}]Replay {report.status.upper()}[/{status_color}]  {report.summary}")
    console.print(f"  profile:    {report.replay_profile.value}")
    console.print(f"  fixture:    {report.fixture_pack_id}")
    if report_path:
        console.print(f"  report:     {report_path}")

    if report.status in ("failed", "error"):
        raise typer.Exit(code=1)


@app.command("inspect")
def cmd_inspect(
    report: str = typer.Option(..., "--report", "-r", help="Path to slice replay report JSON."),
) -> None:
    """Inspect a previously written slice replay report."""
    try:
        rep = load_replay_report(Path(report))
    except FileNotFoundError as exc:
        console.print(f"[red]Not found:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    except ReplayReportLoadError as exc:
        console.print(f"[red]Load error:[/red] {exc}")
        raise typer.Exit(code=2) from exc

    status_color = "green" if rep.status == "passed" else "red" if rep.status == "failed" else "yellow"
    console.print("[bold]Slice Replay Report[/bold]")
    console.print(f"  replay_id:  {rep.replay_id}")
    console.print(f"  pack:       {rep.fixture_pack_id}")
    console.print(f"  profile:    {rep.replay_profile.value}")
    console.print(f"  status:     [{status_color}]{rep.status}[/{status_color}]")
    console.print(f"  summary:    {rep.summary}")

    table = Table(show_header=True, header_style="bold")
    table.add_column("Status")
    table.add_column("Check Type")
    table.add_column("Artifact ID", overflow="fold")
    table.add_column("Summary", overflow="fold")

    for result in rep.check_results:
        s = result.status
        color = "green" if s == "passed" else "red" if s == "failed" else "yellow" if s == "error" else "dim"
        artifact_id = result.fixture_artifact_ids[0] if result.fixture_artifact_ids else ""
        # Derive check_type from summary (best effort — check_id is uuid)
        table.add_row(
            f"[{color}]{s}[/{color}]",
            result.check_id[:8] + "…",
            artifact_id,
            result.summary,
        )
    console.print(table)
