# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
"""Custodian sweep — run ``custodian-audit`` across every managed repo.

For each repo in ``settings.repos`` that has a local checkout and a
``.custodian.yaml`` at its root: shell out to ``custodian-audit --repo
<path> --json``, parse the envelope, and (when ``--emit`` is passed)
create-or-comment a Plane task per repo so operators can act on
findings during the weekly audit step of OC's autonomy cycle.

One open task per repo, ever — subsequent sweeps add a comment with the
new finding table rather than create a duplicate task. Operators close
the task when the underlying findings are resolved.

Read-only against managed repos (custodian-audit doesn't mutate); the
only mutation is into Plane, which is why this lives in OC and not in
Custodian itself.

Run:
    python -m operations_center.entrypoints.custodian_sweep \\
        --config config/operations_center.local.yaml [--emit] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_DEFAULT_HISTORY_PATH = Path("state/custodian_sweep/last_sweep.json")
_DEDUP_LABEL_PREFIX = "custodian-sweep:"  # one label per repo for dedup


@dataclass(frozen=True)
class _RepoTarget:
    repo_key: str
    local_path: Path


@dataclass
class _RepoSweep:
    repo_key: str
    envelope: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    @property
    def total(self) -> int:
        return int(self.envelope.get("total_findings", 0))

    def patterns(self) -> dict[str, dict[str, Any]]:
        return dict(self.envelope.get("patterns", {}))


def _discover_targets(settings) -> list[_RepoTarget]:
    """Pick repos with a local path and a ``.custodian.yaml`` at their root.

    Other repos are silently skipped — they're either not checked out
    locally or haven't adopted Custodian yet. ``custodian-doctor`` is a
    better surface for "should this repo have one?" than the sweep.
    """
    out: list[_RepoTarget] = []
    for key, cfg in settings.repos.items():
        if not cfg.local_path:
            continue
        path = Path(cfg.local_path)
        if not (path / ".custodian.yaml").exists():
            continue
        out.append(_RepoTarget(repo_key=key, local_path=path))
    return out


def _resolve_custodian_audit() -> str | None:
    """Find custodian-audit on PATH, falling back to the running python's bin dir.

    `python -m operations_center...` doesn't put the venv's bin on PATH unless
    the venv was activated, so a plain shutil.which fails for the common case
    of invoking via the venv python directly. Check sys.executable's dir too.
    """
    found = shutil.which("custodian-audit")
    if found:
        return found
    venv_bin = os.path.dirname(sys.executable)
    candidate = os.path.join(venv_bin, "custodian-audit")
    return candidate if os.access(candidate, os.X_OK) else None


def _run_custodian_audit(target: _RepoTarget) -> _RepoSweep:
    """Shell out to custodian-audit. Failures land in ``error`` rather than raise."""
    audit_bin = _resolve_custodian_audit()
    if audit_bin is None:
        return _RepoSweep(target.repo_key, error="custodian-audit not on PATH or in venv bin")
    try:
        proc = subprocess.run(
            [audit_bin, "--repo", str(target.local_path), "--json"],
            capture_output=True, text=True, check=False, timeout=300,
        )
    except subprocess.TimeoutExpired:
        return _RepoSweep(target.repo_key, error="custodian-audit timed out (>300s)")
    if proc.returncode != 0 and not proc.stdout.strip():
        return _RepoSweep(
            target.repo_key,
            error=f"custodian-audit exit={proc.returncode}: {proc.stderr.strip()[:200]}",
        )
    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return _RepoSweep(target.repo_key, error=f"non-JSON output: {exc}")
    return _RepoSweep(target.repo_key, envelope=envelope)


def _delta(current: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, int]:
    """Per-detector delta = current.count - previous.count (0 if absent)."""
    prev_patterns = (previous or {}).get("patterns", {})
    out: dict[str, int] = {}
    for det_id, body in current.get("patterns", {}).items():
        prev_count = int(prev_patterns.get(det_id, {}).get("count", 0))
        out[det_id] = int(body.get("count", 0)) - prev_count
    return out


def _render_body(sweep: _RepoSweep, deltas: dict[str, int]) -> str:
    """Markdown body: header + per-detector table + drill-down hint."""
    if sweep.error:
        return (
            f"**Custodian sweep error for {sweep.repo_key}**\n\n"
            f"```\n{sweep.error}\n```\n"
        )
    rows = ["| Detector | Status | Count | Δ since last sweep |",
            "|---|---|---:|---:|"]
    for det_id, body in sorted(sweep.patterns().items()):
        count = int(body.get("count", 0))
        status = str(body.get("status", "?"))
        delta = deltas.get(det_id, 0)
        delta_str = "—" if delta == 0 else (f"+{delta}" if delta > 0 else str(delta))
        desc = str(body.get("description", "")).strip()
        rows.append(f"| `{det_id}` {desc[:40]} | {status} | {count} | {delta_str} |")
    drill = (
        f"\n\nRe-run locally for full samples:\n"
        f"```\ncustodian-audit --repo <path-to-{sweep.repo_key}>\n```"
    )
    return "\n".join(rows) + drill


def _find_open_sweep_task(plane, repo_key: str) -> dict[str, Any] | None:
    """Return the existing open sweep task for this repo, if any.

    Dedup uses a per-repo label rather than a description marker — labels
    are queryable via list_issues without scanning every body.
    """
    target_label = f"{_DEDUP_LABEL_PREFIX}{repo_key}".lower()
    for issue in plane.list_issues():
        state = issue.get("state")
        state_name = (state.get("name") if isinstance(state, dict) else str(state or "")).lower()
        if state_name in {"done", "cancelled"}:
            continue
        for lab in issue.get("labels", []) or []:
            name = (lab.get("name") if isinstance(lab, dict) else str(lab)).lower()
            if name == target_label:
                return issue
    return None


def _emit(sweep: _RepoSweep, deltas: dict[str, int], plane, *, dry_run: bool) -> str:
    """Create-or-comment one Plane task per repo. Returns action label."""
    title = f"[{sweep.repo_key}] custodian sweep: {sweep.total} findings"
    body = _render_body(sweep, deltas)
    existing = _find_open_sweep_task(plane, sweep.repo_key)
    if dry_run:
        return "would-comment" if existing else "would-create"
    if existing:
        plane.comment_issue(str(existing["id"]), body)
        return "commented"
    plane.create_issue(
        name=title,
        description=body,
        label_names=[f"{_DEDUP_LABEL_PREFIX}{sweep.repo_key}", "custodian-sweep"],
    )
    return "created"


def main() -> int:
    parser = argparse.ArgumentParser(description="Custodian cross-repo audit sweep")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--history", type=Path, default=_DEFAULT_HISTORY_PATH,
                        help="Where to read/write per-repo last-sweep snapshots")
    parser.add_argument("--emit", action="store_true",
                        help="Create or comment Plane tasks; default is print-only")
    parser.add_argument("--dry-run", action="store_true",
                        help="With --emit, log what would happen without calling Plane")
    args = parser.parse_args()

    from operations_center.config import load_settings
    settings = load_settings(args.config)

    targets = _discover_targets(settings)
    previous_all: dict[str, Any] = {}
    if args.history.exists():
        try:
            previous_all = json.loads(args.history.read_text())
        except (OSError, json.JSONDecodeError):
            previous_all = {}

    sweeps: list[_RepoSweep] = [_run_custodian_audit(t) for t in targets]

    plane = None
    if args.emit:
        from operations_center.adapters.plane import PlaneClient
        plane = PlaneClient(
            base_url=settings.plane.base_url,
            api_token=settings.plane_token(),
            workspace_slug=settings.plane.workspace_slug,
            project_id=settings.plane.project_id,
        )

    summary: dict[str, Any] = {
        "scanned_at": datetime.now(UTC).isoformat(),
        "repos_swept": len(sweeps),
        "results": {},
    }
    try:
        for sweep in sweeps:
            deltas = _delta(sweep.envelope, previous_all.get(sweep.repo_key))
            entry: dict[str, Any] = {
                "total_findings": sweep.total,
                "deltas":         deltas,
                "error":          sweep.error,
            }
            if args.emit:
                entry["plane"] = _emit(sweep, deltas, plane, dry_run=args.dry_run)
            summary["results"][sweep.repo_key] = entry
    finally:
        if plane is not None:
            plane.close()

    args.history.parent.mkdir(parents=True, exist_ok=True)
    args.history.write_text(json.dumps(
        {s.repo_key: s.envelope for s in sweeps if not s.error},
        indent=2,
    ))
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
