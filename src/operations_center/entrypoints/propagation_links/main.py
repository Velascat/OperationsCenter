# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""operations-center-propagation-links — inspect parent-child chains (R5.5).

Reads PropagationRecord artifacts from the configured record_dir and
reports parent-child relationships across runs. Useful for answering:

- "Why was this Plane task created?"  (find the source target/version)
- "What did the last CxRP propagation actually fire?"
- "How many consumers got tasks for v1 vs v2?"

Pure read tool — no Plane calls, no state mutation.

Usage::

    operations-center-propagation-links list
    operations-center-propagation-links show <run_id>
    operations-center-propagation-links latest --target cxrp

Exit codes:
  0  found and printed
  1  no matching records
  2  invocation problem
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from operations_center.config.settings import load_settings


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="operations-center-propagation-links",
        description="Inspect propagation parent-child chains.",
    )
    p.add_argument(
        "--config",
        type=Path,
        default=Path("config/operations_center.local.yaml"),
        help="OC config path; record_dir is read from contract_change_propagation block.",
    )
    p.add_argument(
        "--records-dir",
        type=Path,
        default=None,
        help="Override record_dir from settings (e.g. for inspecting a captured snapshot).",
    )
    p.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Emit JSON for automation.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="List all records in chronological order (newest first).")

    show = sub.add_parser("show", help="Pretty-print one record by run_id (or run_id prefix).")
    show.add_argument("run_id", help="Full run_id or unique prefix.")

    latest = sub.add_parser("latest", help="Show the most recent record for a target.")
    latest.add_argument("--target", required=True, help="Target repo_id, e.g. cxrp.")

    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    records_dir = _resolve_records_dir(args)
    if records_dir is None:
        return 2
    records = _load_all(records_dir)
    if not records:
        _emit(args, {"error": f"no records found in {records_dir}"}, plain=f"(no records in {records_dir})")
        return 1

    if args.cmd == "list":
        return _cmd_list(records, args)
    if args.cmd == "show":
        return _cmd_show(records, args.run_id, args)
    if args.cmd == "latest":
        return _cmd_latest(records, args.target, args)
    return 2


def _resolve_records_dir(args) -> Path | None:  # type: ignore[no-untyped-def]
    if args.records_dir is not None:
        return args.records_dir
    if not args.config.exists():
        print(f"✗ propagation-links: config not found: {args.config}", file=sys.stderr)
        return None
    try:
        settings = load_settings(args.config)
    except Exception as exc:  # noqa: BLE001
        print(f"✗ propagation-links: settings load failed: {exc}", file=sys.stderr)
        return None
    rd = settings.contract_change_propagation.record_dir
    return rd if rd.is_absolute() else (Path.cwd() / rd)


def _load_all(records_dir: Path) -> list[dict]:
    if not records_dir.exists():
        return []
    out: list[dict] = []
    for f in sorted(records_dir.glob("*.json")):
        try:
            payload = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            out.append(payload)
    out.sort(key=lambda r: r.get("triggered_at", ""), reverse=True)
    return out


def _cmd_list(records: list[dict], args) -> int:  # type: ignore[no-untyped-def]
    if args.json_output:
        print(json.dumps(records, indent=2, ensure_ascii=False))
    else:
        for r in records:
            outcomes = r.get("outcomes", [])
            fired = [o for o in outcomes if o.get("issue_id")]
            print(f"{r.get('triggered_at', '?')} {r.get('propagator_run_id', '?')[:12]} "
                  f"target={r.get('target_canonical', '?')} "
                  f"version={r.get('target_version', '?')} "
                  f"fired={len(fired)}/{len(outcomes)}")
    return 0


def _cmd_show(records: list[dict], run_id: str, args) -> int:  # type: ignore[no-untyped-def]
    matches = [r for r in records if r.get("propagator_run_id", "").startswith(run_id)]
    if not matches:
        _emit(args, {"error": f"no run matches {run_id!r}"}, plain=f"(no run matches {run_id!r})")
        return 1
    if len(matches) > 1:
        _emit(args, {"error": f"ambiguous prefix {run_id!r}: {len(matches)} matches"},
              plain=f"(ambiguous: {len(matches)} matches)")
        return 1
    record = matches[0]
    if args.json_output:
        print(json.dumps(record, indent=2, ensure_ascii=False))
    else:
        _print_human(record)
    return 0


def _cmd_latest(records: list[dict], target: str, args) -> int:  # type: ignore[no-untyped-def]
    matches = [
        r for r in records
        if r.get("target_repo_id", "").lower() == target.lower()
        or r.get("target_canonical", "").lower() == target.lower()
    ]
    if not matches:
        _emit(args, {"error": f"no records for target {target!r}"}, plain=f"(no records for {target!r})")
        return 1
    record = matches[0]  # already sorted newest-first
    if args.json_output:
        print(json.dumps(record, indent=2, ensure_ascii=False))
    else:
        _print_human(record)
    return 0


def _print_human(record: dict) -> None:
    print(f"propagation run: {record.get('propagator_run_id')}")
    print(f"  target:           {record.get('target_canonical')} ({record.get('target_repo_id')})")
    print(f"  target_version:   {record.get('target_version')}")
    print(f"  triggered_at:     {record.get('triggered_at')}")
    print(f"  policy:           {record.get('policy_summary', {})}")
    print(f"  impact:           {record.get('impact_summary', {})}")
    for o in record.get("outcomes", []):
        suffix = ""
        if o.get("issue_id"):
            suffix = f" → issue={o['issue_id']}"
        if o.get("error"):
            suffix += f" (error: {o['error']})"
        print(f"    [{o.get('decision_action')}] "
              f"{o.get('consumer_canonical')}: {o.get('decision_reason')}{suffix}")


def _emit(args, payload: dict, *, plain: str) -> None:  # type: ignore[no-untyped-def]
    if args.json_output:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(plain)


if __name__ == "__main__":
    sys.exit(main())
