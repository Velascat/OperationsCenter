# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""CLI entry point: ``operations-center-run-show``.

Reads a single run's ``execution_trace.json`` and prints the full
provenance chain — OC ``run_id`` → SwitchBoard rule → RxP invocation →
captured artifacts — without consulting any other artifact.

Validates from the operator's seat that the trace really is
self-contained.

Usage::

    operations-center-run-show <run_id>
    operations-center-run-show <run_id> --root <path>
    operations-center-run-show --trace <path/to/execution_trace.json>
    operations-center-run-show <run_id> --json
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(
    help="Print one run's provenance chain from execution_trace.json alone.",
    no_args_is_help=True,
    add_completion=False,
)

_console = Console()


def _default_search_roots() -> list[Path]:
    """Where to look for ``<root>/<run_id>/execution_trace.json``."""
    roots: list[Path] = []
    cwd = Path.cwd()
    roots.append(cwd / ".operations_center" / "runs")
    env_root = os.environ.get("OC_RUNS_ROOT")
    if env_root:
        roots.append(Path(env_root))
    roots.append(Path.home() / ".console" / "operations_center" / "runs")
    return [r for r in roots if r.exists()]


def _resolve_trace(run_id: Optional[str], explicit: Optional[Path], roots: list[Path]) -> Path:
    if explicit is not None:
        if not explicit.exists():
            raise typer.BadParameter(f"trace file does not exist: {explicit}")
        return explicit
    if run_id is None:
        raise typer.BadParameter("provide a run_id or --trace <path>")

    candidates: list[Path] = []
    for root in roots:
        # exact match first
        direct = root / run_id / "execution_trace.json"
        if direct.exists():
            return direct
        # prefix match (git-style)
        for child in root.iterdir():
            if child.is_dir() and child.name.startswith(run_id):
                trace = child / "execution_trace.json"
                if trace.exists():
                    candidates.append(trace)
    if not candidates:
        searched = "\n  ".join(str(r) for r in roots) or "(no roots)"
        raise typer.BadParameter(
            f"no execution_trace.json found for run_id {run_id!r} under:\n  {searched}\n"
            f"Pass --root <path> or --trace <file> to point at a specific location."
        )
    if len(candidates) > 1:
        rendered = "\n  ".join(str(c) for c in candidates)
        raise typer.BadParameter(
            f"run_id prefix {run_id!r} is ambiguous; candidates:\n  {rendered}"
        )
    return candidates[0]


def _print_trace(trace: dict) -> None:
    headline = trace.get("headline") or "(no headline)"
    status = trace.get("status") or "(unknown)"
    summary = trace.get("summary") or ""

    _console.print(f"[bold]{headline}[/bold]")
    _console.print(f"  status   : {status}")
    if summary:
        _console.print(f"  summary  : {summary}")

    routing = trace.get("routing") or {}
    if routing:
        table = Table(title="SwitchBoard routing", show_header=True, header_style="bold")
        table.add_column("field")
        table.add_column("value")
        for field in (
            "decision_id", "selected_lane", "selected_backend",
            "policy_rule_matched", "rationale", "switchboard_version",
            "confidence", "alternatives_considered",
        ):
            if field in routing:
                value = routing[field]
                table.add_row(field, _render(value))
        _console.print(table)
    else:
        _console.print("[dim](no routing block on this trace)[/dim]")

    provenance = trace.get("provenance") or {}
    if provenance:
        table = Table(title="SourceRegistry provenance", show_header=True, header_style="bold")
        table.add_column("field")
        table.add_column("value")
        for field in ("source", "repo", "ref", "patches"):
            if field in provenance:
                table.add_row(field, _render(provenance[field]))
        _console.print(table)
    else:
        _console.print("[dim](no SourceRegistry provenance on this trace)[/dim]")

    ref = trace.get("runtime_invocation_ref")
    if ref:
        table = Table(title="RxP runtime invocation", show_header=True, header_style="bold")
        table.add_column("field")
        table.add_column("value")
        for field in ("invocation_id", "runtime_name", "runtime_kind",
                      "stdout_path", "stderr_path", "artifact_directory"):
            if field in ref:
                value = ref[field]
                rendered = _render(value)
                # Annotate stdout/stderr/artifact paths with on-disk presence.
                if field in {"stdout_path", "stderr_path", "artifact_directory"} and isinstance(value, str):
                    rendered = f"{value}  {_presence_tag(value)}"
                table.add_row(field, rendered)
        _console.print(table)
    else:
        _console.print("[dim](no runtime_invocation_ref — adapter did not invoke ExecutorRuntime)[/dim]")

    warnings = trace.get("warnings") or []
    if warnings:
        _console.print("[bold yellow]warnings[/bold yellow]")
        for w in warnings:
            _console.print(f"  - {w}")


def _render(value) -> str:
    if isinstance(value, list):
        return ", ".join(str(v) for v in value) if value else "(none)"
    if value is None:
        return "(none)"
    return str(value)


def _presence_tag(path: str) -> str:
    p = Path(path)
    if not p.exists():
        return "[red](missing)[/red]"
    if p.is_dir():
        return "[green](dir present)[/green]"
    return f"[green]({p.stat().st_size} bytes)[/green]"


@app.command()
def show(
    run_id: Optional[str] = typer.Argument(None, help="Run ID (or unambiguous prefix)."),
    root: Optional[Path] = typer.Option(
        None, "--root", help="Search root directory containing per-run subdirs.",
    ),
    trace: Optional[Path] = typer.Option(
        None, "--trace", help="Direct path to execution_trace.json (skips run_id resolution).",
    ),
    as_json: bool = typer.Option(False, "--json", help="Print the full trace JSON instead of the formatted view."),
) -> None:
    """Print one run's provenance chain from execution_trace.json alone."""
    roots = [root] if root is not None else _default_search_roots()
    trace_path = _resolve_trace(run_id, trace, roots)
    payload = json.loads(trace_path.read_text(encoding="utf-8"))

    if as_json:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False))
        return
    _console.print(f"[dim]source: {trace_path}[/dim]\n")
    _print_trace(payload)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
