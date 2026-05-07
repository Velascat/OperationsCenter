# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Kodo discovery adapter — Phase 1.

Wraps the existing operations_center.backends.kodo invocation logic
with sample capture. NO normalization (Phase 2). NO policy enforcement.

Capture lives at:
    operations_center/executors/kodo/samples/raw_output/*.json
    operations_center/executors/kodo/samples/invocations/*.json

Every sample-write call routes through ``_scrub.scrub_sample``.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from operations_center.executors._scrub import scrub_sample

_SAMPLES_BASE = Path(__file__).parent / "samples"
_RAW_OUTPUT_DIR = _SAMPLES_BASE / "raw_output"
_INVOCATIONS_DIR = _SAMPLES_BASE / "invocations"


@dataclass
class DiscoveryRunCapture:
    """Raw output + invocation metadata from a single discovery run."""

    run_id: str
    invoked_at: str
    lane: str
    raw_output: dict[str, Any]
    invocation: dict[str, Any]
    duration_seconds: Optional[float] = None
    extras: dict[str, Any] = field(default_factory=dict)


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def write_capture(capture: DiscoveryRunCapture, *, samples_base: Path | None = None) -> tuple[Path, Path]:
    """Persist a capture to disk after scrubbing. Returns (raw_path, invocation_path)."""
    base = samples_base or _SAMPLES_BASE
    raw_dir = base / "raw_output"
    inv_dir = base / "invocations"
    raw_dir.mkdir(parents=True, exist_ok=True)
    inv_dir.mkdir(parents=True, exist_ok=True)

    raw_path = raw_dir / f"{capture.run_id}.json"
    inv_path = inv_dir / f"{capture.run_id}.json"

    raw_payload = scrub_sample({
        "run_id": capture.run_id,
        "invoked_at": capture.invoked_at,
        "lane": capture.lane,
        "duration_seconds": capture.duration_seconds,
        "raw_output": capture.raw_output,
        "extras": capture.extras,
    })
    inv_payload = scrub_sample({
        "run_id": capture.run_id,
        "invoked_at": capture.invoked_at,
        "lane": capture.lane,
        "invocation": capture.invocation,
    })

    raw_path.write_text(json.dumps(raw_payload, indent=2, ensure_ascii=False), encoding="utf-8")
    inv_path.write_text(json.dumps(inv_payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return raw_path, inv_path


def discover(
    *,
    lane: str,
    invocation: dict[str, Any],
    raw_output: dict[str, Any],
    duration_seconds: Optional[float] = None,
    extras: Optional[dict[str, Any]] = None,
    samples_base: Path | None = None,
) -> DiscoveryRunCapture:
    """Record a discovery run. Real-backend integration calls this with
    the actual invocation/output; tests pass synthetic values.

    The harness deliberately accepts pre-captured ``raw_output`` rather
    than invoking Kodo internally — keeps Phase 1 transport-agnostic and
    lets the caller (real production runner OR test fixture) decide how
    to invoke the backend.
    """
    capture = DiscoveryRunCapture(
        run_id=str(uuid.uuid4())[:8],
        invoked_at=_now_iso(),
        lane=lane,
        invocation=invocation,
        raw_output=raw_output,
        duration_seconds=duration_seconds,
        extras=extras or {},
    )
    write_capture(capture, samples_base=samples_base)
    return capture
