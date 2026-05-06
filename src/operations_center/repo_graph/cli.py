# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Repo Graph CLI — `operations-center-repo-graph`.

Read-only inspection of the repo graph. Subcommands:
  list            — show canonical repos
  resolve NAME    — resolve canonical or legacy name
  upstream ID     — direct upstream nodes
  downstream ID   — direct downstream nodes
  impact ID       — repos affected by a contract change in ID
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .loader import load_repo_graph
from .models import RepoGraphConfigError

app = typer.Typer(help="Repo Graph (ER-001) inspection commands.")
_console = Console()


def _default_config_path() -> Path:
    # config/repo_graph.yaml at the repo root, found via this file's location.
    return Path(__file__).resolve().parents[3] / "config" / "repo_graph.yaml"


def _load(config: Path | None):
    path = config or _default_config_path()
    try:
        return load_repo_graph(path)
    except RepoGraphConfigError as exc:
        _console.print(f"[red]repo graph config error:[/red] {exc}")
        raise typer.Exit(code=2) from exc


@app.command("list")
def list_cmd(
    config: Path | None = typer.Option(None, "--config", help="repo graph YAML path"),
) -> None:
    """List known repos."""
    graph = _load(config)
    table = Table(title="Repo Graph")
    table.add_column("repo_id")
    table.add_column("canonical")
    table.add_column("legacy")
    table.add_column("role")
    for node in graph.list_nodes():
        table.add_row(
            node.repo_id,
            node.canonical_name,
            ", ".join(node.legacy_names) or "-",
            node.runtime_role or "-",
        )
    _console.print(table)


@app.command("resolve")
def resolve_cmd(
    name: str,
    config: Path | None = typer.Option(None, "--config"),
) -> None:
    """Resolve a canonical or legacy name."""
    graph = _load(config)
    node = graph.resolve(name)
    if node is None:
        _console.print(f"[red]not found:[/red] {name}")
        raise typer.Exit(code=1)
    _console.print(
        f"{name} → [cyan]{node.canonical_name}[/cyan] "
        f"(repo_id={node.repo_id}, role={node.runtime_role or '-'})"
    )


@app.command("upstream")
def upstream_cmd(
    repo_id: str,
    config: Path | None = typer.Option(None, "--config"),
) -> None:
    """Direct upstream nodes from repo_id."""
    graph = _load(config)
    try:
        nodes = graph.upstream(repo_id)
    except KeyError:
        _console.print(f"[red]unknown repo_id:[/red] {repo_id}")
        raise typer.Exit(code=1)
    for node in nodes:
        _console.print(f"  → {node.canonical_name} ({node.repo_id})")


@app.command("downstream")
def downstream_cmd(
    repo_id: str,
    config: Path | None = typer.Option(None, "--config"),
) -> None:
    """Direct downstream nodes pointing at repo_id."""
    graph = _load(config)
    try:
        nodes = graph.downstream(repo_id)
    except KeyError:
        _console.print(f"[red]unknown repo_id:[/red] {repo_id}")
        raise typer.Exit(code=1)
    for node in nodes:
        _console.print(f"  ← {node.canonical_name} ({node.repo_id})")


@app.command("impact")
def impact_cmd(
    repo_id: str,
    config: Path | None = typer.Option(None, "--config"),
) -> None:
    """Repos affected if `repo_id`'s contracts change."""
    graph = _load(config)
    try:
        nodes = graph.affected_by_contract_change(repo_id)
    except KeyError:
        _console.print(f"[red]unknown repo_id:[/red] {repo_id}")
        raise typer.Exit(code=1)
    if not nodes:
        _console.print("(no consumers)")
        return
    for node in nodes:
        _console.print(f"  • {node.canonical_name} ({node.repo_id})")


if __name__ == "__main__":
    app()
