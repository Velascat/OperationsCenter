# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
"""CLI entry point: operations-center-calibration.

Commands
--------
  analyze        Analyze an artifact manifest with an explicit profile.
  tune-autonomy  Run recommendation profile and write advisory report.
  report         Display a previously-written calibration report.

This CLI is read-only. It does not:
  - run audits
  - modify manifests, configs, or producer files
  - harvest fixtures
  - run replay tests
  - apply recommendations automatically
  - import VideoFoundry code
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from operations_center.artifact_index import (
    ManifestInvalidError,
    ManifestNotFoundError,
    build_artifact_index,
    load_artifact_manifest,
)
from operations_center.behavior_calibration import (
    AnalysisProfile,
    BehaviorCalibrationInput,
    FindingSeverity,
    analyze_artifacts,
    load_calibration_report,
    write_calibration_report,
)

app = typer.Typer(
    help="Managed repo audit behavior calibration commands.",
    no_args_is_help=True,
)
console = Console()

_SEVERITY_STYLE = {
    FindingSeverity.INFO: "dim",
    FindingSeverity.WARNING: "yellow",
    FindingSeverity.ERROR: "red",
    FindingSeverity.CRITICAL: "bold red",
}


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
    return manifest, build_artifact_index(manifest, path, repo_root=root)


@app.command("analyze")
def cmd_analyze(
    manifest: str = typer.Option(..., "--manifest", "-m", help="Path to artifact_manifest.json."),
    profile: str = typer.Option(
        "summary",
        "--profile",
        "-p",
        help="Analysis profile: summary | failure_diagnosis | coverage_gaps | artifact_health | producer_compliance | recommendation",
    ),
    repo_root: Optional[str] = typer.Option(None, "--repo-root", help="Override managed repo root for path resolution."),
    include_content: bool = typer.Option(False, "--include-content", help="Opt-in artifact content analysis (JSON readability check)."),
    output_dir: Optional[str] = typer.Option(None, "--output-dir", help="Write JSON report to this directory."),
    json_output: bool = typer.Option(False, "--json", help="Print report as JSON."),
) -> None:
    """Analyze an artifact manifest with an explicit analysis profile."""
    try:
        ap = AnalysisProfile(profile)
    except ValueError:
        valid = [p.value for p in AnalysisProfile]
        console.print(f"[red]Invalid profile:[/red] '{profile}'. Valid: {valid}")
        raise typer.Exit(code=3)

    manifest_obj, index = _load_index(manifest, repo_root)

    calibration_input = BehaviorCalibrationInput(
        repo_id=index.source.repo_id,
        run_id=index.source.run_id,
        audit_type=index.source.audit_type,
        artifact_index=index,
        analysis_profile=ap,
        include_artifact_content=include_content,
    )

    report = analyze_artifacts(calibration_input)

    if json_output:
        typer.echo(report.model_dump_json(indent=2))
    else:
        _print_report_summary(report)

    if output_dir:
        path = write_calibration_report(report, output_dir)
        console.print(f"[dim]Report written: {path}[/dim]")

    raise typer.Exit(code=0 if not report.has_errors else 1)


@app.command("tune-autonomy")
def cmd_tune_autonomy(
    manifest: str = typer.Option(..., "--manifest", "-m", help="Path to artifact_manifest.json."),
    repo_root: Optional[str] = typer.Option(None, "--repo-root"),
    output_dir: Optional[str] = typer.Option(None, "--output-dir", help="Write JSON report to this directory."),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Run recommendation profile and produce advisory tuning suggestions.

    Output is advisory only. Recommendations are never applied automatically.
    """
    manifest_obj, index = _load_index(manifest, repo_root)

    calibration_input = BehaviorCalibrationInput(
        repo_id=index.source.repo_id,
        run_id=index.source.run_id,
        audit_type=index.source.audit_type,
        artifact_index=index,
        analysis_profile=AnalysisProfile.RECOMMENDATION,
        include_artifact_content=False,
    )

    report = analyze_artifacts(calibration_input)

    if json_output:
        typer.echo(report.model_dump_json(indent=2))
    else:
        _print_report_summary(report)
        if report.recommendations:
            console.print()
            t = Table(title="Advisory Recommendations (human review required)")
            t.add_column("priority")
            t.add_column("summary")
            t.add_column("risk")
            for rec in report.recommendations:
                t.add_row(rec.priority.value, rec.summary, rec.risk)
            console.print(t)
        else:
            console.print("[dim]No recommendations produced.[/dim]")

    if output_dir:
        path = write_calibration_report(report, output_dir)
        console.print(f"[dim]Report written: {path}[/dim]")


@app.command("report")
def cmd_report(
    report_path: str = typer.Argument(help="Path to a calibration report JSON file."),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Display a previously-written calibration report."""
    try:
        report = load_calibration_report(report_path)
    except FileNotFoundError as exc:
        console.print(f"[red]Not found:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    except Exception as exc:
        console.print(f"[red]Invalid report:[/red] {exc}")
        raise typer.Exit(code=2) from exc

    if json_output:
        typer.echo(report.model_dump_json(indent=2))
    else:
        _print_report_summary(report)


def _print_report_summary(report) -> None:
    console.print(
        f"[bold]Calibration Report[/bold] — "
        f"{report.repo_id}/{report.audit_type} "
        f"[dim]profile={report.analysis_profile.value}[/dim]"
    )
    s = report.artifact_index_summary
    console.print(f"  artifacts: {s.total_artifacts} total, {s.singleton_count} singleton(s)")
    console.print(f"  status: {dict(s.by_status)}")
    if s.missing_file_count:
        console.print(f"  [red]missing files: {s.missing_file_count}[/red]")
    if s.unresolved_path_count:
        console.print(f"  [yellow]unresolved paths: {s.unresolved_path_count}[/yellow]")
    if s.excluded_path_count:
        console.print(f"  [dim]excluded paths: {s.excluded_path_count}[/dim]")

    if report.findings:
        console.print()
        t = Table(title=f"Findings ({len(report.findings)})")
        t.add_column("severity")
        t.add_column("category")
        t.add_column("summary")
        for f in report.findings:
            style = _SEVERITY_STYLE.get(f.severity, "")
            t.add_row(
                f"[{style}]{f.severity.value}[/{style}]",
                f.category.value,
                f.summary,
            )
        console.print(t)
    else:
        console.print("  [dim]No findings.[/dim]")
