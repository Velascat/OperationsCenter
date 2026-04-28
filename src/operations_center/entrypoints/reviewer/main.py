"""Reviewer watcher — placeholder pending full PR review implementation.

The legacy reviewer loop was retired. The full two-phase PR review state machine
(self-review → human review) is tracked as a separate campaign; see
docs/specs/reviewer-pr-state-machine.md once that campaign runs.

Until then this watcher runs as an idle loop so the bash restart wrapper does
not accumulate spurious restart counts. It accepts the same CLI flags as the
other watcher roles so operations-center.sh needs no special-casing.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)


def _write_heartbeat(status_dir: Path) -> None:
    try:
        status_dir.mkdir(parents=True, exist_ok=True)
        hb = status_dir / "heartbeat_review.json"
        hb.write_text(json.dumps({
            "role":   "review",
            "at":     datetime.now(UTC).isoformat(),
            "status": "pending_implementation",
        }), encoding="utf-8")
    except Exception:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description="OperationsCenter reviewer watcher (placeholder)")
    parser.add_argument("--config",                required=True)
    parser.add_argument("--watch",                 action="store_true")
    parser.add_argument("--poll-interval-seconds", type=int, default=60, dest="poll_interval")
    parser.add_argument("--status-dir",            type=Path, default=None, dest="status_dir")
    parser.add_argument("--log-level",             default="INFO")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s [review] %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    status_dir = args.status_dir or (Path(__file__).resolve().parents[4] / "logs" / "local" / "watch-all")

    logger.info("review: watcher pending full implementation — idling")

    if not args.watch:
        return 0

    while True:
        _write_heartbeat(status_dir)
        time.sleep(args.poll_interval)


if __name__ == "__main__":
    sys.exit(main())
