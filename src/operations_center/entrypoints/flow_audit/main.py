"""Flow-audit scanner.

The complement to ghost_audit. Each detector measures whether a *flow gap*
documented in `docs/architecture/flow_audit.md` is currently open or has
been closed. For Fixed patterns the count should be zero; for Open /
Partial patterns the count is the size of the gap.

    python -m operations_center.entrypoints.flow_audit \\
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
    status: str  # "fixed" | "open" | "partial" | "deferred"
    detect: Callable[["FlowContext"], tuple[int, list[str]]]


@dataclass
class FlowContext:
    log_dir: Path
    since: datetime
    plane_issues: list[dict] = field(default_factory=list)
    state_dir: Path = Path("state")


# ── F1: stale Running tasks ──────────────────────────────────────────────────

def _detect_f1_stale_running(ctx: FlowContext) -> tuple[int, list[str]]:
    """Tasks Running for >4h without an updated_at refresh.

    With the recover_stale entrypoint deployed and run periodically this
    should be zero. Nonzero means either recover_stale isn't running or
    something is freshly stuck right now (will be picked up next cycle).
    """
    threshold = 4 * 60 * 60
    now = datetime.now(UTC)
    samples = []
    for issue in ctx.plane_issues:
        state = issue.get("state")
        name = (state.get("name", "") if isinstance(state, dict) else str(state or "")).strip()
        if name.lower() != "running":
            continue
        ts_raw = issue.get("updated_at") or issue.get("created_at") or ""
        try:
            ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
        except Exception:
            continue
        age = (now - ts).total_seconds()
        if age > threshold:
            samples.append(f"{(issue.get('name') or '')[:60]} ({int(age/60)}m)")
    return len(samples), samples[:5]


# ── F3: proposal duplicates ──────────────────────────────────────────────────

def _detect_f3_proposal_dupes(ctx: FlowContext) -> tuple[int, list[str]]:
    """Non-terminal tasks sharing normalised title within the same family."""
    by_key: dict[str, list[dict]] = {}
    for issue in ctx.plane_issues:
        state = issue.get("state")
        name = (state.get("name", "") if isinstance(state, dict) else str(state or "")).strip().lower()
        if name in {"done", "cancelled", "blocked"}:
            continue
        title_norm = re.sub(r"\s+", " ", (issue.get("name") or "").strip().lower())
        labels = [
            (lab.get("name", "") if isinstance(lab, dict) else str(lab)).strip().lower()
            for lab in issue.get("labels", [])
        ]
        family = next((lab.split(":", 1)[1].strip() for lab in labels if lab.startswith("source-family:")), "")
        key = f"{family}|{title_norm}"
        by_key.setdefault(key, []).append(issue)
    samples = []
    for key, issues in by_key.items():
        if len(issues) > 1 and key.split("|", 1)[1]:
            samples.append(f"{key[:80]} ×{len(issues)}")
    return len(samples), samples[:5]


# ── F11: runaway follow-up retries ───────────────────────────────────────────

def _detect_f11_retry_overflow(ctx: FlowContext) -> tuple[int, list[str]]:
    """Tasks with retry-count: N where N >= 3 (cap exceeded somehow)."""
    samples = []
    for issue in ctx.plane_issues:
        for lab in issue.get("labels", []) or []:
            name = (lab.get("name", "") if isinstance(lab, dict) else str(lab)).strip().lower()
            m = re.match(r"retry-count:\s*(\d+)", name)
            if m and int(m.group(1)) > 3:
                samples.append(f"{(issue.get('name') or '')[:60]} retry={m.group(1)}")
                break
    return len(samples), samples[:5]


# ── F13: stale state files ───────────────────────────────────────────────────

# Reuse OCStateScanner's per_task_subdirs list — single source of truth for
# which state directories hold per-task records, keeping flow_audit and
# cleanup_state from drifting if a new state store is added later.
try:
    from _custodian.state_scanner import OCStateScanner
    _STATE_SCANNER = OCStateScanner()
    _PER_TASK_SUBDIRS = _STATE_SCANNER.per_task_subdirs
except ImportError:
    _STATE_SCANNER = None
    _PER_TASK_SUBDIRS = ("proposal_feedback", "pr_reviews", "improve_insights")


def _detect_f13_stale_state(ctx: FlowContext) -> tuple[int, list[str]]:
    """Per-task state files older than 90 days where Plane task is terminal.

    Reports the count of files cleanup_state would delete; nonzero means
    cleanup_state hasn't been run lately. The per-task-state directory list
    is now sourced from OCStateScanner so flow_audit and the maintenance
    CLIs can never disagree on what state lives where.
    """
    cutoff_age = 90 * 86400
    now = datetime.now(UTC).timestamp()
    samples = []
    n = 0
    for sub in _PER_TASK_SUBDIRS:
        d = ctx.state_dir / sub
        if not d.is_dir():
            continue
        for f in d.iterdir():
            if not f.is_file():
                continue
            try:
                age = now - f.stat().st_mtime
            except OSError:
                continue
            if age <= cutoff_age:
                continue
            n += 1
            if len(samples) < 5:
                samples.append(f"{f} ({int(age/86400)}d)")
    return n, samples


# ── F8: ready-queue size (back-pressure) ─────────────────────────────────────

def _detect_f8_ready_queue_size(ctx: FlowContext) -> tuple[int, list[str]]:
    """Number of tasks in Ready for AI; large values mean propose isn't being throttled."""
    n = 0
    for issue in ctx.plane_issues:
        state = issue.get("state")
        name = (state.get("name", "") if isinstance(state, dict) else str(state or "")).strip().lower()
        if name == "ready for ai":
            n += 1
    samples = [f"ready_for_ai_count={n}"] if n > 50 else []
    # Count is informational; anything below 50 is not a problem.
    return (n if n > 50 else 0), samples


_DETECTORS: list[Detector] = [
    Detector("F1",  "stale Running task auto-recovery",        "fixed",   _detect_f1_stale_running),
    Detector("F3",  "proposal deduplication",                  "fixed",   _detect_f3_proposal_dupes),
    Detector("F8",  "back-pressure on Ready queue",            "partial", _detect_f8_ready_queue_size),
    Detector("F11", "runaway follow-up retries",               "fixed",   _detect_f11_retry_overflow),
    Detector("F13", "stale state file cleanup",                "fixed",   _detect_f13_stale_state),
]


def _parse_since(raw: str) -> datetime:
    if not raw:
        return datetime.now(UTC) - timedelta(hours=24)
    m = re.match(r"^(\d+)([hd])$", raw.strip())
    if not m:
        raise ValueError(f"--since must look like '24h' or '7d', got {raw!r}")
    n, unit = int(m.group(1)), m.group(2)
    delta = timedelta(hours=n) if unit == "h" else timedelta(days=n)
    return datetime.now(UTC) - delta


def main() -> int:
    parser = argparse.ArgumentParser(description="Flow-gap audit scanner")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--since",  default="24h")
    parser.add_argument("--state-dir", type=Path, default=Path("state"))
    parser.add_argument("--log-dir",   type=Path, default=None)
    args = parser.parse_args()

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
        print(f"# warning: Plane fetch failed ({exc}); skipping Plane-based detectors", flush=True)

    ctx = FlowContext(
        log_dir=args.log_dir or _DEFAULT_LOG_DIR,
        since=_parse_since(args.since),
        plane_issues=plane_issues,
        state_dir=args.state_dir,
    )
    out: dict[str, Any] = {
        "scanned_at": datetime.now(UTC).isoformat(),
        "since":      ctx.since.isoformat(),
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
    out["total_open_gaps"] = sum(
        v.get("count", 0) for v in out["patterns"].values() if isinstance(v, dict)
    )
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
