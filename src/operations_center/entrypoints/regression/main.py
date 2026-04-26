"""CLI entry point: operations-center-regression.

Commands
--------
  run      Run a mini regression suite from a suite definition JSON file.
  inspect  Inspect a previously written suite report.
  list     List entries in a suite definition.

This CLI does NOT:
  - run full audits
  - harvest fixtures
  - modify fixture packs, source artifacts, or manifests
  - apply calibration recommendations
  - call Phase 6 dispatch or Phase 9 harvesting directly
  - import managed repo code
"""

from __future__ import annotations

import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from operations_center.mini_regression import (
    MiniRegressionRunRequest,
    SuiteDefinitionError,
    SuiteReportLoadError,
    SuiteRunError,
    load_mini_regression_suite,
    load_suite_report,
    run_mini_regression_suite,
)

app = typer.Typer(
    help="Mini regression suite runner for slice replay.",
    no_args_is_help=True,
)
console = Console()


@app.command("run")
def cmd_run(
    suite: str = typer.Option(
        ..., "--suite", "-s", help="Path to suite definition JSON file."
    ),
    output_dir: str = typer.Option(
        "tools/audit/report/mini_regression",
        "--output-dir", "-o",
        help="Root directory for suite and replay reports.",
    ),
    fail_fast: bool = typer.Option(
        False, "--fail-fast", help="Stop after first required entry failure."
    ),
    include_optional: bool = typer.Option(
        True, "--include-optional/--skip-optional",
        help="Whether to run optional suite entries.",
    ),
    run_id: str | None = typer.Option(
        None, "--run-id", help="Override the auto-generated suite run ID."
    ),
) -> None:
    """Run a mini regression suite and write a report."""
    try:
        suite_def = load_mini_regression_suite(suite)
    except FileNotFoundError as exc:
        console.print(f"[red]Suite file not found:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    except SuiteDefinitionError as exc:
        console.print(f"[red]Suite definition error:[/red] {exc}")
        raise typer.Exit(code=2) from exc

    request = MiniRegressionRunRequest(
        suite_definition=suite_def,
        output_dir=Path(output_dir),
        fail_fast=fail_fast,
        include_optional_entries=include_optional,
        **({"run_id": run_id} if run_id else {}),
    )

    try:
        report = run_mini_regression_suite(request)
    except SuiteRunError as exc:
        console.print(f"[red]Suite run error:[/red] {exc}")
        raise typer.Exit(code=3) from exc

    status_color = (
        "green" if report.status == "passed"
        else "red" if report.status in ("failed", "error")
        else "yellow"
    )
    console.print(f"[bold]{suite_def.name}[/bold]")
    console.print(f"  suite_id:   {report.suite_id}")
    console.print(f"  run_id:     {report.suite_run_id}")
    console.print(f"  status:     [{status_color}]{report.status.upper()}[/{status_color}]")
    console.print(f"  summary:    {report.summary.text}")

    table = Table(show_header=True, header_style="bold")
    table.add_column("Status", width=8)
    table.add_column("Entry ID", overflow="fold")
    table.add_column("Profile")
    table.add_column("Req", width=4)
    table.add_column("Summary", overflow="fold")

    for result in report.entry_results:
        s = result.status
        color = (
            "green" if s == "passed"
            else "red" if s in ("failed", "error")
            else "dim"
        )
        req_marker = "Y" if result.required else "n"
        table.add_row(
            f"[{color}]{s}[/{color}]",
            result.entry_id,
            result.replay_profile.value,
            req_marker,
            result.summary[:80],
        )
    console.print(table)

    if report.status in ("failed", "error"):
        raise typer.Exit(code=1)


@app.command("inspect")
def cmd_inspect(
    report: str = typer.Option(..., "--report", "-r", help="Path to suite_report.json."),
) -> None:
    """Inspect a previously written suite report."""
    try:
        rep = load_suite_report(Path(report))
    except FileNotFoundError as exc:
        console.print(f"[red]Not found:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    except SuiteReportLoadError as exc:
        console.print(f"[red]Load error:[/red] {exc}")
        raise typer.Exit(code=2) from exc

    status_color = (
        "green" if rep.status == "passed"
        else "red" if rep.status in ("failed", "error")
        else "yellow"
    )
    console.print(f"[bold]Mini Regression Suite Report[/bold]")
    console.print(f"  suite:      {rep.suite_name} ({rep.suite_id})")
    console.print(f"  run_id:     {rep.suite_run_id}")
    console.print(f"  status:     [{status_color}]{rep.status}[/{status_color}]")
    console.print(f"  summary:    {rep.summary.text}")
    console.print(f"  started:    {rep.started_at.isoformat()}")
    console.print(f"  ended:      {rep.ended_at.isoformat()}")

    table = Table(show_header=True, header_style="bold")
    table.add_column("Status", width=8)
    table.add_column("Entry ID", overflow="fold")
    table.add_column("Profile")
    table.add_column("Req", width=4)
    table.add_column("Summary", overflow="fold")

    for result in rep.entry_results:
        s = result.status
        color = (
            "green" if s == "passed"
            else "red" if s in ("failed", "error")
            else "dim"
        )
        req_marker = "Y" if result.required else "n"
        table.add_row(
            f"[{color}]{s}[/{color}]",
            result.entry_id,
            result.replay_profile.value,
            req_marker,
            result.summary[:80],
        )
    console.print(table)


@app.command("list")
def cmd_list(
    suite: str = typer.Option(..., "--suite", "-s", help="Path to suite definition JSON file."),
) -> None:
    """List entries in a suite definition."""
    try:
        suite_def = load_mini_regression_suite(suite)
    except FileNotFoundError as exc:
        console.print(f"[red]Suite file not found:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    except SuiteDefinitionError as exc:
        console.print(f"[red]Suite definition error:[/red] {exc}")
        raise typer.Exit(code=2) from exc

    console.print(f"[bold]{suite_def.name}[/bold]  (suite_id={suite_def.suite_id})")
    console.print(f"  {len(suite_def.entries)} entries "
                  f"({len(suite_def.required_entries)} required, "
                  f"{len(suite_def.optional_entries)} optional)")

    table = Table(show_header=True, header_style="bold")
    table.add_column("Entry ID", overflow="fold")
    table.add_column("Profile")
    table.add_column("Req", width=4)
    table.add_column("Fixture Pack Path", overflow="fold")

    for entry in suite_def.entries:
        req_marker = "Y" if entry.required else "n"
        table.add_row(
            entry.entry_id,
            entry.replay_profile.value,
            req_marker,
            entry.fixture_pack_path,
        )
    console.print(table)
