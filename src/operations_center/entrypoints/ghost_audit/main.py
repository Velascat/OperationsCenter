# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Ghost-work audit scanner.

Each detector here corresponds to a row in
docs/architecture/ghost_work_audit.md. The output is JSON keyed by pattern
ID (G1..GN), each entry { count: int, samples: [str], status: "fixed"|"open" }.

Adding a new detector:
  1. Document the pattern in ghost_work_audit.md (gets a Gn ID).
  2. Append a Detector entry to _DETECTORS below.
  3. The Detector reads from the data sources passed to scan() — log lines,
     Plane issues, the local git remote. Missing data should degrade to
     "unknown" rather than fail.

Run:
  python -m operations_center.entrypoints.ghost_audit \\
      --config config/operations_center.local.yaml [--since 24h]
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

_DEFAULT_LOG_DIR = Path("logs/local/watch-all")


@dataclass
class Detector:
    pattern_id: str
    description: str
    status: str  # "fixed" | "open"
    detect: Callable[["AuditContext"], tuple[int, list[str]]]


@dataclass
class AuditContext:
    log_dir: Path
    since: datetime
    plane_issues: list[dict] = field(default_factory=list)


# ── helpers ───────────────────────────────────────────────────────────────────

# Single shared scanner instance — OCLogScanner is stateless and pure, so
# a module-level instance avoids re-instantiation per detector call. Living
# in _custodian/ means the parsing rules are reused by Custodian's detectors
# too (no drift between OC's own ghost_audit and what Custodian sees).
try:
    from _custodian.log_scanner import OCLogScanner
    _LOG_SCANNER = OCLogScanner()
except ImportError:
    _LOG_SCANNER = None


def _log_lines_since(ctx: AuditContext) -> list[tuple[Path, str]]:
    """Yield (log_path, line) tuples for every log file modified after ctx.since.

    Raw-line iterator kept for detectors that still grep with substring
    matches; new detectors should prefer ``_log_events_since`` which uses
    the OCLogScanner protocol implementation.
    """
    out: list[tuple[Path, str]] = []
    if not ctx.log_dir.exists():
        return out
    for log in ctx.log_dir.glob("*.log"):
        try:
            if datetime.fromtimestamp(log.stat().st_mtime, UTC) < ctx.since:
                continue
        except OSError:
            continue
        try:
            for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
                out.append((log, line))
        except OSError:
            pass
    return out


def _log_events_since(ctx: AuditContext):
    """Yield (log_path, event_dict) for parseable lines via OCLogScanner.

    Lines that don't match a known shape are silently skipped (delegated to
    the scanner). This is the preferred iterator for detectors written
    against structured event types rather than substring patterns.
    """
    if _LOG_SCANNER is None:
        return
    for log, line in _log_lines_since(ctx):
        event = _LOG_SCANNER.parse_event(line)
        if event is not None:
            yield log, event


# ── detectors ─────────────────────────────────────────────────────────────────

def _detect_g4_oversized(ctx: AuditContext) -> tuple[int, list[str]]:
    """Diff cap fired."""
    samples = []
    for _log, line in _log_lines_since(ctx):
        if "refusing to commit oversized diff" in line:
            samples.append(line.strip()[:120])
    return len(samples), samples[:5]


def _detect_g5_policy_blocked(ctx: AuditContext) -> tuple[int, list[str]]:
    """Tasks blocked by policy after a kodo run.

    Uses the structured event iterator — when the OCLogScanner sees a
    board_worker_blocked event with category=policy_blocked, it surfaces
    that as a typed dict we can match cleanly. Falls back to substring
    matching when the scanner isn't available (CI without the editable
    Custodian install).
    """
    samples = []
    if _LOG_SCANNER is not None:
        for _log, event in _log_events_since(ctx):
            if event.get("category") == "policy_blocked" and event.get("action") == "blocked":
                ts = event.get("ts", "")
                role = event.get("role", "?")
                tid = (event.get("task_id") or "")[:36]
                samples.append(f"{ts} [{role}] task_id={tid} blocked status=skipped category=policy_blocked")
                if len(samples) >= 5:
                    break
        return len(samples), samples
    # Fallback path
    for _log, line in _log_lines_since(ctx):
        if "category=policy_blocked" in line:
            samples.append(line.strip()[:120])
    return len(samples), samples[:5]


def _detect_g7_thin_goal(ctx: AuditContext) -> tuple[int, list[str]]:
    """Tasks the worker refused due to short description."""
    samples = []
    for _log, line in _log_lines_since(ctx):
        if "refused thin task_id" in line:
            samples.append(line.strip()[:120])
    return len(samples), samples[:5]


def _detect_g8_stale_running(ctx: AuditContext) -> tuple[int, list[str]]:
    """Plane tasks in Running state for an unusual duration."""
    samples = []
    for issue in ctx.plane_issues:
        state = (issue.get("state") or {}).get("name", "") if isinstance(issue.get("state"), dict) else ""
        if state != "Running":
            continue
        # No good "started_at" field on Plane work-items; updated_at is the
        # closest proxy. Anything older than 4 hours is suspicious.
        ts_raw = issue.get("updated_at") or issue.get("created_at") or ""
        try:
            ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
        except Exception:
            continue
        age = (datetime.now(UTC) - ts).total_seconds()
        if age > 4 * 3600:
            samples.append(f"{(issue.get('name') or '')[:60]} (running {int(age/60)}m)")
    return len(samples), samples[:5]


def _detect_g10_runaway_followups(ctx: AuditContext) -> tuple[int, list[str]]:
    """Follow-up tasks at retry-count >= 2 still pending."""
    samples = []
    for issue in ctx.plane_issues:
        labels = [
            (lab.get("name", "") if isinstance(lab, dict) else str(lab)).strip().lower()
            for lab in issue.get("labels", [])
        ]
        for lab in labels:
            m = re.match(r"retry-count:\s*(\d+)", lab)
            if m and int(m.group(1)) >= 2:
                state = (issue.get("state") or {}).get("name", "") if isinstance(issue.get("state"), dict) else ""
                samples.append(f"{(issue.get('name') or '')[:50]} state={state}")
                break
    return len(samples), samples[:5]


def _detect_g12_expanded_rewrite(ctx: AuditContext) -> tuple[int, list[str]]:
    """Blocked-rewrite events that fired against a `lifecycle: expanded` task.

    Should be zero — _handle_blocked checks the label and skips. A nonzero
    count means the guard regressed or someone added a new blocked-task
    processor that doesn't honour the label.
    """
    expanded_ids: set[str] = set()
    for issue in ctx.plane_issues:
        labels = [
            (lab.get("name", "") if isinstance(lab, dict) else str(lab)).strip().lower()
            for lab in issue.get("labels", [])
        ]
        if "lifecycle: expanded" in labels:
            expanded_ids.add(str(issue.get("id", "")))
    if not expanded_ids:
        return 0, []
    samples = []
    for _log, line in _log_lines_since(ctx):
        if "blocked_task_unblocked" not in line and "blocked_rewrite_failed" not in line:
            continue
        for tid in expanded_ids:
            if tid and tid in line:
                samples.append(line.strip()[:140])
                break
    return len(samples), samples[:5]


def _detect_workspace_pollution(ctx: AuditContext) -> tuple[int, list[str]]:
    """Open PRs touching .operations_center/ — should be zero with the .gitignore fix."""
    # Best-effort: read recent log lines mentioning a PR with .operations_center
    # touched. Without a git API call we can't be authoritative.
    samples = []
    for _log, line in _log_lines_since(ctx):
        if ".operations_center/" in line and "pull_request_url" in line:
            samples.append(line.strip()[:140])
    return len(samples), samples[:5]


_DETECTORS: list[Detector] = [
    Detector("G1",  "workspace pollution",                "fixed", _detect_workspace_pollution),
    Detector("G4",  "oversized diff (scope_too_wide)",    "fixed", _detect_g4_oversized),
    Detector("G5",  "policy-blocked task burned kodo time","fixed", _detect_g5_policy_blocked),
    Detector("G7",  "claim-refused thin goal",            "fixed", _detect_g7_thin_goal),
    Detector("G8",  "stale Running task",                 "fixed", _detect_g8_stale_running),
    Detector("G10", "runaway follow-up loop",             "fixed", _detect_g10_runaway_followups),
    Detector("G12", "rewrite of expanded meta-task",      "fixed", _detect_g12_expanded_rewrite),
]


# ── scan ──────────────────────────────────────────────────────────────────────

def scan(ctx: AuditContext) -> dict[str, Any]:
    out: dict[str, Any] = {
        "scanned_at": datetime.now(UTC).isoformat(),
        "since":      ctx.since.isoformat(),
        "log_dir":    str(ctx.log_dir),
        "patterns":   {},
    }
    for det in _DETECTORS:
        try:
            count, samples = det.detect(ctx)
        except Exception as exc:
            out["patterns"][det.pattern_id] = {
                "description": det.description,
                "status":      det.status,
                "error":       str(exc),
            }
            continue
        out["patterns"][det.pattern_id] = {
            "description": det.description,
            "status":      det.status,
            "count":       count,
            "samples":     samples,
        }
    out["total_ghost_events"] = sum(
        v.get("count", 0) for v in out["patterns"].values() if isinstance(v, dict)
    )
    return out


def _parse_since(raw: str) -> datetime:
    if not raw:
        return datetime.now(UTC) - timedelta(hours=24)
    m = re.match(r"^(\d+)([hd])$", raw.strip())
    if not m:
        raise ValueError(f"--since must look like '24h' or '7d', got {raw!r}")
    n, unit = int(m.group(1)), m.group(2)
    delta = timedelta(hours=n) if unit == "h" else timedelta(days=n)
    return datetime.now(UTC) - delta


def main() -> None:
    parser = argparse.ArgumentParser(description="Ghost-work audit scanner")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--since",  default="24h",
                        help="window for log scanning, e.g. 24h, 7d (default: 24h)")
    parser.add_argument("--log-dir", type=Path, default=None,
                        help="override watcher log directory (default: logs/local/watch-all)")
    args = parser.parse_args()

    since = _parse_since(args.since)
    log_dir = args.log_dir or _DEFAULT_LOG_DIR

    plane_issues: list[dict] = []
    try:
        from operations_center.adapters.plane import PlaneClient
        from operations_center.config import load_settings
        settings = load_settings(args.config)
        client = PlaneClient(
            base_url=settings.plane.base_url,
            api_token=settings.plane_token(),
            workspace_slug=settings.plane.workspace_slug,
            project_id=settings.plane.project_id,
        )
        try:
            plane_issues = client.list_issues()
        finally:
            client.close()
    except Exception as exc:
        # Plane may be down or token missing; fall back to log-only audit
        plane_issues = []
        print(f"# warning: Plane fetch failed ({exc}); skipping Plane-based detectors", flush=True)

    ctx = AuditContext(log_dir=log_dir, since=since, plane_issues=plane_issues)
    print(json.dumps(scan(ctx), indent=2))


if __name__ == "__main__":
    main()
