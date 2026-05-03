# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Clean up stale per-task state files.

`state/proposal_feedback/`, `state/pr_reviews/`, `state/improve_insights/`,
and similar directories accumulate one file per task. Without cleanup they
grow indefinitely; eventually scans slow and disk fills.

This entrypoint removes files older than `--retention-days` whose
corresponding Plane task is in a terminal state (Done / Blocked /
Cancelled). Files for live tasks are always preserved regardless of age.

    python -m operations_center.entrypoints.maintenance.cleanup_state \\
        --config config/operations_center.local.yaml \\
        [--retention-days 90] \\
        [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from operations_center.adapters.plane import PlaneClient
from operations_center.config import load_settings

_TERMINAL_STATES = {"done", "blocked", "cancelled"}
_TASK_ID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)

# Directories under state/ where one file == one task. Add new dirs here as
# new per-task state stores appear. Generic filename matching via UUID regex.
_PER_TASK_DIRS = (
    "proposal_feedback",
    "pr_reviews",
    "improve_insights",
)


def _file_task_id(path: Path) -> str | None:
    m = _TASK_ID_RE.search(path.stem)
    return m.group(0).lower() if m else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Delete stale per-task state files")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--retention-days", type=int, default=90,
                        help="age threshold in days (default: 90)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--state-dir", type=Path, default=Path("state"))
    args = parser.parse_args()

    settings = load_settings(args.config)
    client = PlaneClient(
        base_url=settings.plane.base_url,
        api_token=settings.plane_token(),
        workspace_slug=settings.plane.workspace_slug,
        project_id=settings.plane.project_id,
    )
    try:
        items = client.list_issues()
    except Exception as exc:
        print(json.dumps({"error": f"plane_fetch_failed: {exc}"}, ensure_ascii=False))
        return 1
    finally:
        # Close after we're done extracting — but we still need terminal map
        pass

    state_by_task: dict[str, str] = {}
    for issue in items:
        task_id = str(issue.get("id") or "").lower()
        if not task_id:
            continue
        st = issue.get("state")
        name = (st.get("name", "") if isinstance(st, dict) else str(st or "")).strip().lower()
        state_by_task[task_id] = name
    client.close()

    now = datetime.now(UTC).timestamp()
    cutoff = now - args.retention_days * 86400

    deleted: list[dict] = []
    kept: list[dict] = []

    for sub in _PER_TASK_DIRS:
        d = args.state_dir / sub
        if not d.is_dir():
            continue
        for f in d.iterdir():
            if not f.is_file():
                continue
            try:
                mtime = f.stat().st_mtime
            except OSError:
                continue
            if mtime > cutoff:
                continue  # too new
            task_id = _file_task_id(f)
            entry = {"path": str(f), "task_id": task_id, "mtime_age_days": int((now - mtime) / 86400)}
            if not task_id:
                # No recognisable task id — leave alone. Could be metadata file.
                kept.append({**entry, "reason": "no_task_id"})
                continue
            plane_state = state_by_task.get(task_id)
            if plane_state and plane_state not in _TERMINAL_STATES:
                kept.append({**entry, "reason": f"task_state={plane_state}"})
                continue
            # Either task is terminal or it's gone from Plane (assume terminal).
            entry["plane_state"] = plane_state or "unknown"
            if args.dry_run:
                entry["action"] = "would_delete"
            else:
                try:
                    f.unlink()
                    entry["action"] = "deleted"
                except OSError as exc:
                    entry["action"] = "error"
                    entry["error"]  = str(exc)
            deleted.append(entry)

    out = {
        "scanned_at":         datetime.now(UTC).isoformat(),
        "retention_days":     args.retention_days,
        "dry_run":            args.dry_run,
        "deleted_count":      sum(1 for d in deleted if d.get("action") == "deleted"),
        "would_delete_count": sum(1 for d in deleted if d.get("action") == "would_delete"),
        "kept_count":         len(kept),
        "deleted":            deleted[:50],
        "kept_sample":        kept[:10],
    }
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
