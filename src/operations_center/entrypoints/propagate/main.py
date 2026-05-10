# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""operations-center-propagate — manual cross-repo task chaining trigger (R5.2).

Usage::

    operations-center-propagate \\
        --target cxrp \\
        --version <commit-sha> \\
        --config config/operations_center.local.yaml

Composes the EffectiveRepoGraph from the configured platform_manifest
block, walks the contract-impact set for the target, and (per the
contract_change_propagation policy) creates Plane tasks for downstream
consumers. Always writes a structured PropagationRecord to
``<settings.contract_change_propagation.record_dir>/<run_id>.json``.

Exit codes:
  0  ran cleanly (whether or not any tasks were created)
  1  configuration problem (settings load, graph build, policy disabled
     when --require-enabled was given)
  2  invocation problem (missing config, invalid args)

Designed to be safe to run repeatedly. Idempotency via the dedup store.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from operations_center.adapters.plane.client import PlaneClient
from operations_center.config.settings import (
    ContractChangePropagationSettings,
    load_settings,
)
from operations_center.propagation import (
    ContractChangePropagator,
    PropagationDedupStore,
    PropagationPolicy,
    PropagationRegistry,
    PropagationSettings,
)
from operations_center.propagation.plane_adapter import PlaneTaskCreator
from operations_center.propagation.policy import _Action, _PairOverride
from operations_center.repo_graph_factory import (
    build_effective_repo_graph_from_settings,
)

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="operations-center-propagate",
        description="Cross-repo task chaining — manual trigger (R5.2).",
    )
    p.add_argument(
        "--target",
        required=True,
        help="repo_id (or canonical name) of the changed repo, e.g. cxrp / CxRP.",
    )
    p.add_argument(
        "--version",
        required=True,
        help="Target version (commit SHA, semver tag, etc.) — used as dedup key.",
    )
    p.add_argument(
        "--config",
        type=Path,
        default=Path("config/operations_center.local.yaml"),
        help="OC config path (default: config/operations_center.local.yaml).",
    )
    p.add_argument(
        "--require-enabled",
        action="store_true",
        help="Exit non-zero if propagation is disabled in settings.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip Plane API calls; record what would be created. Forces a "
             "fake task creator that returns synthetic IDs.",
    )
    p.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Emit the PropagationRecord as JSON for automation.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    config_path = args.config.resolve()
    if not config_path.exists():
        print(f"✗ propagate: config not found: {config_path}", file=sys.stderr)
        return 2
    try:
        settings = load_settings(config_path)
    except Exception as exc:  # noqa: BLE001 — surface load errors plainly
        print(f"✗ propagate: settings load failed: {exc}", file=sys.stderr)
        return 2

    pcfg: ContractChangePropagationSettings = settings.contract_change_propagation
    if args.require_enabled and not pcfg.enabled:
        print("✗ propagate: contract_change_propagation.enabled is False; aborting "
              "(--require-enabled was set)", file=sys.stderr)
        return 1

    graph = build_effective_repo_graph_from_settings(
        settings, repo_root=Path.cwd()
    )
    if graph is None:
        print("✗ propagate: EffectiveRepoGraph build returned None — see warnings",
              file=sys.stderr)
        return 1

    policy = _build_policy(pcfg)
    registry = PropagationRegistry.from_mapping()

    record_dir = pcfg.record_dir if pcfg.record_dir.is_absolute() else (
        Path.cwd() / pcfg.record_dir
    )
    dedup_path = pcfg.dedup_path if pcfg.dedup_path.is_absolute() else (
        Path.cwd() / pcfg.dedup_path
    )
    dedup = PropagationDedupStore(path=dedup_path)

    task_creator: object
    if args.dry_run:
        task_creator = _DryRunTaskCreator()
    else:
        plane_client = PlaneClient(
            base_url=settings.plane.base_url,
            api_token=settings.plane_token(),
            workspace_slug=settings.plane.workspace_slug,
            project_id=settings.plane.project_id,
        )
        task_creator = PlaneTaskCreator(client=plane_client)

    propagator = ContractChangePropagator(
        policy=policy,
        registry=registry,
        dedup=dedup,
        task_creator=task_creator,
        record_dir=record_dir,
    )

    record = propagator.propagate(
        target_repo_id=args.target,
        target_version=args.version,
        graph=graph,
    )

    if args.json_output:
        print(json.dumps(record.to_dict(), indent=2, ensure_ascii=False))
    else:
        _print_human(record)
    return 0


def _build_policy(pcfg: ContractChangePropagationSettings) -> PropagationPolicy:
    overrides: list[tuple[str, str, _PairOverride]] = []
    for ov in pcfg.pair_overrides:
        try:
            action = _Action(ov.action)
        except ValueError:
            logger.warning(
                "ignoring pair_override with invalid action=%r (target=%s consumer=%s)",
                ov.action, ov.target_repo_id, ov.consumer_repo_id,
            )
            continue
        overrides.append((
            ov.target_repo_id,
            ov.consumer_repo_id,
            _PairOverride(action=action, reason=ov.reason),
        ))
    settings = PropagationSettings(
        enabled=pcfg.enabled,
        auto_trigger_edge_types=frozenset(pcfg.auto_trigger_edge_types),
        dedup_window_hours=pcfg.dedup_window_hours,
        pair_overrides=tuple(overrides),
    )
    return PropagationPolicy(settings=settings)


class _DryRunTaskCreator:
    """Stand-in task creator that doesn't hit Plane. Used for --dry-run."""

    def __init__(self) -> None:
        self._next = 0

    def create_issue(
        self,
        *,
        title: str,
        body: str,  # noqa: ARG002
        labels: tuple[str, ...],  # noqa: ARG002
        promote_to_ready: bool,  # noqa: ARG002
    ) -> str:
        self._next += 1
        return f"DRY-RUN-{self._next}"


def _print_human(record) -> None:  # type: ignore[no-untyped-def]
    print(f"propagation run: {record.propagator_run_id}")
    print(f"  target:           {record.target_canonical} ({record.target_repo_id})")
    print(f"  target_version:   {record.target_version}")
    print(f"  triggered_at:     {record.triggered_at}")
    print(f"  policy:           enabled={record.policy_summary.get('enabled')} "
          f"edge_types={record.policy_summary.get('auto_trigger_edge_types')}")
    impact = record.impact_summary
    print(f"  impact:           {impact.get('affected_count', 0)} consumer(s) "
          f"[public={len(impact.get('public_affected', []))} "
          f"private={len(impact.get('private_affected', []))}]")
    for o in record.outcomes:
        suffix = f" → issue={o.issue_id}" if o.issue_id else ""
        if o.error:
            suffix += f" (error: {o.error})"
        print(f"    [{o.decision_action}] {o.consumer_canonical}: {o.decision_reason}{suffix}")


if __name__ == "__main__":
    sys.exit(main())
