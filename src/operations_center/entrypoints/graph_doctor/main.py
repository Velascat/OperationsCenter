# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""operations-center-graph-doctor — diagnose EffectiveRepoGraph wiring.

Runs the same `build_effective_repo_graph_from_settings` factory that
`entrypoints/execute/main.py` uses, then reports:
  - which manifests were resolved + their paths
  - per-layer node/edge contributions
  - any warnings/errors raised during composition
  - a clear pass/fail status

Exit codes:
  0  graph composed cleanly (or `enabled: false` — by design)
  1  configuration problem detected (composition errored or returned None
     unexpectedly while enabled=true)
  2  invocation problem (bad config path, etc.)

Designed to be safe to run repeatedly. Reads only — never mutates state.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from io import StringIO
from pathlib import Path

from operations_center.config.settings import load_settings
from operations_center.repo_graph_factory import (
    build_effective_repo_graph_from_settings,
)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="operations-center-graph-doctor",
        description="Diagnose EffectiveRepoGraph wiring for an OC config.",
    )
    p.add_argument(
        "--config",
        type=Path,
        default=Path("config/operations_center.local.yaml"),
        help="Path to OC config (default: config/operations_center.local.yaml)",
    )
    p.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="repo_root passed to the factory (defaults to None — uses cwd)",
    )
    p.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Emit the report as JSON for automation",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    config_path: Path = args.config.resolve()
    if not config_path.exists():
        _emit_error(args.json_output, f"config not found: {config_path}", exit_code=2)
        return 2

    try:
        settings = load_settings(config_path)
    except Exception as exc:  # noqa: BLE001 — surface any settings load failure
        _emit_error(
            args.json_output,
            f"settings load failed: {exc}",
            exit_code=2,
            config=str(config_path),
        )
        return 2

    pm = settings.platform_manifest

    # Capture WARNING+ logs from the factory so the doctor can report them.
    log_buffer = StringIO()
    handler = logging.StreamHandler(log_buffer)
    handler.setLevel(logging.WARNING)
    factory_logger = logging.getLogger(
        "operations_center.repo_graph_factory"
    )
    prior_level = factory_logger.level
    factory_logger.addHandler(handler)
    factory_logger.setLevel(logging.WARNING)
    try:
        graph = build_effective_repo_graph_from_settings(
            settings, repo_root=args.repo_root
        )
    finally:
        factory_logger.removeHandler(handler)
        factory_logger.setLevel(prior_level)

    captured_warnings = [
        line.strip() for line in log_buffer.getvalue().splitlines() if line.strip()
    ]

    report: dict[str, object] = {
        "config": str(config_path),
        "platform_manifest": {
            "enabled": pm.enabled,
            "project_slug": pm.project_slug,
            "project_manifest_path": str(pm.project_manifest_path) if pm.project_manifest_path else None,
            "local_manifest_path": str(pm.local_manifest_path) if pm.local_manifest_path else None,
        },
        "repo_root": str(args.repo_root) if args.repo_root else None,
        "graph_built": graph is not None,
        "warnings": captured_warnings,
    }

    if graph is not None:
        report["nodes_total"] = len(graph.list_nodes())
        report["edges_total"] = len(graph.edges)
        per_source: dict[str, int] = {}
        for n in graph.list_nodes():
            per_source[n.source.value] = per_source.get(n.source.value, 0) + 1
        report["nodes_by_source"] = per_source
        per_edge_source: dict[str, int] = {}
        for e in graph.edges:
            per_edge_source[e.source.value] = per_edge_source.get(e.source.value, 0) + 1
        report["edges_by_source"] = per_edge_source

    # Decide pass/fail.
    if not pm.enabled:
        report["status"] = "ok_disabled"
        exit_code = 0
    elif graph is None:
        report["status"] = "fail_graph_none"
        exit_code = 1
    else:
        report["status"] = "ok"
        exit_code = 0

    if args.json_output:
        print(json.dumps(report, indent=2))
    else:
        _print_human(report, exit_code)
    return exit_code


def _print_human(report: dict[str, object], exit_code: int) -> None:
    icon = "✓" if exit_code == 0 else "✗"
    print(f"{icon} graph-doctor: {report['status']}")
    print(f"  config:                 {report['config']}")
    pm_raw = report["platform_manifest"]
    pm: dict[str, object] = pm_raw if isinstance(pm_raw, dict) else {}  # ty:ignore[invalid-assignment]
    print(f"  enabled:                {pm.get('enabled')}")
    print(f"  project_slug:           {pm.get('project_slug') or '(none)'}")
    print(f"  project_manifest_path:  {pm.get('project_manifest_path') or '(none)'}")
    print(f"  local_manifest_path:    {pm.get('local_manifest_path') or '(none)'}")
    print(f"  repo_root:              {report.get('repo_root') or '(none)'}")
    print(f"  graph_built:            {report['graph_built']}")
    nodes_total = report.get("nodes_total")
    if nodes_total is not None:
        print(f"  nodes_total:            {nodes_total}")
        print(f"  edges_total:            {report.get('edges_total')}")
        nbs = report.get("nodes_by_source")
        ebs = report.get("edges_by_source")
        if nbs:
            print(f"  nodes_by_source:        {nbs}")
        if ebs:
            print(f"  edges_by_source:        {ebs}")
    warnings = report.get("warnings")
    if isinstance(warnings, list) and warnings:
        print(f"  warnings ({len(warnings)}):")
        for w in warnings:
            print(f"    - {w}")


def _emit_error(
    json_output: bool,
    message: str,
    *,
    exit_code: int,
    **extra: str,
) -> None:
    payload = {"status": "error", "message": message, "exit_code": exit_code, **extra}
    if json_output:
        print(json.dumps(payload, indent=2))
    else:
        print(f"✗ graph-doctor: error — {message}")


if __name__ == "__main__":
    sys.exit(main())
