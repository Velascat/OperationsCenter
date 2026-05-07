# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""R5 — Phase 13 re-audit trigger CLI.

Iterates every backend in the catalog, calls ``needs_reaudit()`` with
the current CxRP version + per-backend invocation recency, prints a
report, and exits non-zero if any backend needs re-auditing. Designed
to run on a CI cron schedule (weekly or daily).

Usage::

    operations-center-reaudit-check                  # uses real catalog
    operations-center-reaudit-check --dir <path>     # alternate executors dir
    operations-center-reaudit-check --json           # machine-readable
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from operations_center.executors.catalog import load_catalog
from operations_center.executors.reaudit import (
    ReauditDecision,
    needs_reaudit,
)


def _read_last_invoked_at(backend_dir: Path) -> date | None:
    """Best-effort: read the newest invocation sample's timestamp."""
    inv_dir = backend_dir / "samples" / "invocations"
    if not inv_dir.is_dir():
        return None
    newest: date | None = None
    for sample in inv_dir.glob("*.json"):
        try:
            data = json.loads(sample.read_text(encoding="utf-8"))
            ts = data.get("invoked_at", "")
            if "T" in ts:
                d = date.fromisoformat(ts.split("T")[0])
                if newest is None or d > newest:
                    newest = d
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    return newest


def _detect_current_cxrp_version() -> str:
    """Read the installed CxRP version. Falls back to 0.2 (current minor)."""
    try:
        from importlib.metadata import version
        return version("cxrp")
    except Exception:
        return "0.2"


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="operations-center-reaudit-check",
        description="Phase 13 — evaluate re-audit triggers for all backends.",
    )
    parser.add_argument("--dir", type=Path, default=None, help="Executors directory")
    parser.add_argument("--json", action="store_true", help="Emit JSON report")
    parser.add_argument(
        "--current-backend-version", default="unknown",
        help="Current backend version to compare against (default: 'unknown' = no version trigger)",
    )
    parser.add_argument(
        "--runtimebinding-changed", action="store_true",
        help="Mark CxRP RuntimeBinding schema as changed",
    )
    parser.add_argument(
        "--capabilityset-changed", action="store_true",
        help="Mark CxRP CapabilitySet schema as changed",
    )
    args = parser.parse_args()

    catalog = load_catalog(args.dir)
    cxrp_version = _detect_current_cxrp_version()

    results: dict[str, ReauditDecision] = {}
    for backend_id, entry in catalog.entries.items():
        backend_dir = (args.dir or Path(__file__).resolve().parents[2] / "executors") / backend_id
        last_invoked = _read_last_invoked_at(backend_dir)
        decision = needs_reaudit(
            entry.audit_verdict,
            current_backend_version=args.current_backend_version,
            current_cxrp_version=cxrp_version,
            runtimebinding_schema_changed=args.runtimebinding_changed,
            capabilityset_schema_changed=args.capabilityset_changed,
            last_invoked_at=last_invoked,
        )
        results[backend_id] = decision

    if args.json:
        report = {
            "cxrp_version": cxrp_version,
            "backends": {
                bid: {
                    "needed": d.needed,
                    "reasons": [r.value for r in d.reasons],
                }
                for bid, d in results.items()
            },
        }
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        for bid, d in results.items():
            if not d.needed:
                print(f"[OK]    {bid}: no re-audit needed")
            else:
                reasons = ", ".join(r.value for r in d.reasons)
                print(f"[STALE] {bid}: re-audit needed ({reasons})")

    any_needed = any(d.needed for d in results.values())
    return 1 if any_needed else 0


if __name__ == "__main__":
    sys.exit(main())
