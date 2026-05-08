# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Run Memory CLI — `operations-center-run-memory`."""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .index import (
    RunMemoryQueryService,
    rebuild_index_from_artifacts,
)
from .models import RunMemoryQuery

app = typer.Typer(help="Run Memory (ER-002) query/rebuild commands.")
_console = Console()


@app.command("query")
def query_cmd(
    index_dir: Path = typer.Option(..., "--index-dir", help="Directory containing records.jsonl"),
    repo_id: str | None = typer.Option(None, "--repo-id"),
    run_id: str | None = typer.Option(None, "--run-id"),
    request_id: str | None = typer.Option(None, "--request-id"),
    status: str | None = typer.Option(None, "--status"),
    contract_kind: str | None = typer.Option(None, "--contract-kind"),
    tag: str | None = typer.Option(None, "--tag"),
    text: str | None = typer.Option(None, "--text", help="Substring across summary/tags/artifacts/repo/run"),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON array instead of a table"),
) -> None:
    """Substring + exact-match search over indexed run memory."""
    svc = RunMemoryQueryService(index_dir)
    q = RunMemoryQuery(
        repo_id=repo_id,
        run_id=run_id,
        request_id=request_id,
        status=status,
        contract_kind=contract_kind,
        tag=tag,
        text=text,
    )
    records = svc.query(q)
    if json_out:
        typer.echo(json.dumps([r.to_jsonl() for r in records], sort_keys=True, indent=2, ensure_ascii=False))
        return
    table = Table(title=f"Run Memory ({len(records)} matches)")
    table.add_column("created_at")
    table.add_column("status")
    table.add_column("repo")
    table.add_column("run_id")
    table.add_column("summary", overflow="fold")
    for r in records:
        table.add_row(r.created_at, r.status, r.repo_id or "-", r.run_id, r.summary or "-")
    _console.print(table)


@app.command("rebuild")
def rebuild_cmd(
    artifacts_dir: Path = typer.Option(..., "--artifacts-dir", help="Directory of execution_result*.json artifacts"),
    index_dir: Path = typer.Option(..., "--index-dir"),
) -> None:
    """Regenerate the index from on-disk ExecutionResult artifacts."""
    n = rebuild_index_from_artifacts(artifacts_dir, index_dir)
    _console.print(f"rebuilt: [cyan]{n}[/cyan] records → {index_dir}/records.jsonl")


if __name__ == "__main__":
    app()
