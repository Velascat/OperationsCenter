# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""``operations-center-archon-probe`` — health-probe a running Archon.

Standalone CLI for ops/monitoring. Wraps
``operations_center.backends.archon.archon_health_probe``.

Exit codes:
    0   Archon healthy
    1   Archon unreachable or unhealthy
    2   Bad usage
"""
from __future__ import annotations


import typer


app = typer.Typer(no_args_is_help=False, add_completion=False)


@app.command()
def probe(
    base_url: str = typer.Option(
        "http://localhost:3000",
        "--base-url", "-u",
        help="Archon HTTP base URL (default: http://localhost:3000)",
    ),
    timeout: float = typer.Option(
        5.0, "--timeout", "-t",
        help="HTTP request timeout in seconds",
    ),
) -> None:
    """Probe Archon's /api/health endpoint and print the verdict."""
    from operations_center.backends.archon import archon_health_probe

    result = archon_health_probe(base_url, timeout_seconds=timeout)

    if result.ok:
        typer.echo(f"[OK] {result.summary} (HTTP {result.status_code})")
        raise typer.Exit(0)

    if result.reachable:
        typer.echo(f"[FAIL] {result.summary}", err=True)
    else:
        typer.echo(f"[UNREACHABLE] {result.summary}", err=True)
    raise typer.Exit(1)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
