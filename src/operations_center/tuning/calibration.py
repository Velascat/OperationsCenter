# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
"""Phase 6 confidence calibration infrastructure.

Tracks the relationship between the confidence label the system assigned to a
proposal and the actual outcome (merged / escalated / abandoned).  Once enough
data accumulates (target: ≥20 records per family), the calibration report
surfaces families where the confidence label is systematically miscalibrated —
e.g., type_fix "high" confidence accepted only 40% of the time.

The store is backed by ``state/calibration_store.json`` and is updated every
time a feedback record is written via ``entrypoints/feedback/main.py`` or the
reviewer watcher.

Usage::

    store = ConfidenceCalibrationStore()
    store.record("lint_fix", "high", "merged")
    store.record("type_fix", "medium", "escalated")
    for row in store.report():
        print(row)
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, asdict
from datetime import UTC, datetime
from pathlib import Path

_DEFAULT_STORE_PATH = Path("state/calibration_store.json")
_MIN_SAMPLE_SIZE = 5    # minimum records before reporting calibration


@dataclass
class CalibrationRecord:
    family: str
    confidence: str
    total: int
    merged: int
    escalated: int
    abandoned: int
    acceptance_rate: float   # merged / (merged + escalated + abandoned)
    # calibration_ratio: acceptance_rate / expected_rate_for_confidence_label
    # expected rates: high=0.8, medium=0.5, low=0.3 (conservative baselines)
    expected_rate: float
    calibration_ratio: float  # > 1.0 = well-calibrated or over-performing; < 1.0 = over-confident
    repo_key: str | None = None  # None = global aggregate across all repos

    def to_dict(self) -> dict:
        return asdict(self)


_EXPECTED_RATES = {"high": 0.8, "medium": 0.5, "low": 0.3}


class ConfidenceCalibrationStore:
    """Thread-safe append-only store for (family, confidence, outcome) records."""

    def __init__(self, path: Path = _DEFAULT_STORE_PATH) -> None:
        self._path = path
        self._lock = threading.RLock()

    def record(self, family: str, confidence: str, outcome: str, *, repo_key: str | None = None) -> None:
        """Append a calibration event.

        outcome must be one of: merged, escalated, abandoned.
        Silently ignored for unknown confidence labels (not in high/medium/low).
        repo_key (optional) enables per-repo × family calibration.
        """
        if confidence not in _EXPECTED_RATES:
            return
        if outcome not in ("merged", "escalated", "abandoned"):
            return
        entry: dict = {
            "recorded_at": datetime.now(UTC).isoformat(),
            "family": family,
            "confidence": confidence,
            "outcome": outcome,
        }
        if repo_key:
            entry["repo_key"] = repo_key
        with self._lock:
            data = self._load()
            data.setdefault("events", []).append(entry)
            self._save(data)

    def calibration_for(
        self,
        family: str,
        confidence: str,
        *,
        repo_key: str | None = None,
        window_days: int | None = 90,
    ) -> float | None:
        """Return the observed acceptance rate for (family, confidence[, repo_key]).

        When repo_key is given, only events matching that repo are counted.
        Falls back to global aggregate when repo_key is None.
        When window_days is given (default 90), only events within that many days
        are counted so stale data does not dilute recent signal.
        Returns None when fewer than _MIN_SAMPLE_SIZE records exist.
        """
        with self._lock:
            data = self._load()
        cutoff = self._cutoff(window_days)
        events = [
            e for e in data.get("events", [])
            if e.get("family") == family and e.get("confidence") == confidence
            and (repo_key is None or e.get("repo_key") == repo_key)
            and (cutoff is None or e.get("recorded_at", "") >= cutoff)
        ]
        if len(events) < _MIN_SAMPLE_SIZE:
            return None
        merged = sum(1 for e in events if e["outcome"] == "merged")
        total = len(events)
        return merged / total

    def cleanup_old_events(self, *, window_days: int = 90) -> int:
        """Remove events older than *window_days*. Returns number of events removed."""
        cutoff = self._cutoff(window_days)
        if cutoff is None:
            return 0
        with self._lock:
            data = self._load()
            events = data.get("events", [])
            kept = [e for e in events if e.get("recorded_at", "") >= cutoff]
            removed = len(events) - len(kept)
            if removed > 0:
                data["events"] = kept
                self._save(data)
        return removed

    @staticmethod
    def _cutoff(window_days: int | None) -> str | None:
        """Return ISO timestamp cutoff string or None when no window is configured."""
        if window_days is None:
            return None
        from datetime import timedelta
        cutoff_dt = datetime.now(UTC) - timedelta(days=window_days)
        return cutoff_dt.isoformat()

    def report(self, *, per_repo: bool = False, window_days: int | None = 90) -> list[CalibrationRecord]:
        """Return calibration records for all (family, confidence[, repo_key]) pairs with enough data.

        When per_repo=True, groups by (repo_key, family, confidence).
        When per_repo=False (default), groups by (family, confidence) across all repos.
        When window_days is given (default 90), only events within that window are used.
        """
        with self._lock:
            data = self._load()
        cutoff = self._cutoff(window_days)
        all_events = data.get("events", [])
        events = (
            [e for e in all_events if e.get("recorded_at", "") >= cutoff]
            if cutoff is not None else all_events
        )

        if per_repo:
            # Group by (repo_key, family, confidence)
            groups: dict[tuple, list[str]] = {}
            for e in events:
                rk = str(e.get("repo_key") or "")
                key = (rk, str(e.get("family", "")), str(e.get("confidence", "")))
                if key[1] and key[2] in _EXPECTED_RATES:
                    groups.setdefault(key, []).append(str(e.get("outcome", "")))
        else:
            # Group by (family, confidence) — global aggregate
            _groups: dict[tuple[str, str], list[str]] = {}
            for e in events:
                key2 = (str(e.get("family", "")), str(e.get("confidence", "")))
                if key2[0] and key2[1] in _EXPECTED_RATES:
                    _groups.setdefault(key2, []).append(str(e.get("outcome", "")))
            # Unify format
            groups = {("", k[0], k[1]): v for k, v in _groups.items()}

        records: list[CalibrationRecord] = []
        for group_key, outcomes in sorted(groups.items()):
            if len(outcomes) < _MIN_SAMPLE_SIZE:
                continue
            rk_val, family, confidence = group_key
            merged = outcomes.count("merged")
            escalated = outcomes.count("escalated")
            abandoned = outcomes.count("abandoned")
            total = len(outcomes)
            acceptance_rate = merged / total if total > 0 else 0.0
            expected_rate = _EXPECTED_RATES[confidence]
            calibration_ratio = acceptance_rate / expected_rate if expected_rate > 0 else 0.0
            records.append(CalibrationRecord(
                family=family,
                confidence=confidence,
                total=total,
                merged=merged,
                escalated=escalated,
                abandoned=abandoned,
                acceptance_rate=round(acceptance_rate, 3),
                expected_rate=expected_rate,
                calibration_ratio=round(calibration_ratio, 3),
                repo_key=rk_val or None,
            ))
        return records

    def _load(self) -> dict:
        if self._path.exists():
            try:
                return json.loads(self._path.read_text())
            except Exception:
                pass
        return {"events": []}

    def _save(self, data: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data, indent=2))
