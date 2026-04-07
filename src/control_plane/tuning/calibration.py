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

    def to_dict(self) -> dict:
        return asdict(self)


_EXPECTED_RATES = {"high": 0.8, "medium": 0.5, "low": 0.3}


class ConfidenceCalibrationStore:
    """Thread-safe append-only store for (family, confidence, outcome) records."""

    def __init__(self, path: Path = _DEFAULT_STORE_PATH) -> None:
        self._path = path
        self._lock = threading.RLock()

    def record(self, family: str, confidence: str, outcome: str) -> None:
        """Append a calibration event.

        outcome must be one of: merged, escalated, abandoned.
        Silently ignored for unknown confidence labels (not in high/medium/low).
        """
        if confidence not in _EXPECTED_RATES:
            return
        if outcome not in ("merged", "escalated", "abandoned"):
            return
        entry = {
            "recorded_at": datetime.now(UTC).isoformat(),
            "family": family,
            "confidence": confidence,
            "outcome": outcome,
        }
        with self._lock:
            data = self._load()
            data.setdefault("events", []).append(entry)
            self._save(data)

    def calibration_for(self, family: str, confidence: str) -> float | None:
        """Return the observed acceptance rate for (family, confidence), or None if too few samples."""
        with self._lock:
            data = self._load()
        events = [
            e for e in data.get("events", [])
            if e.get("family") == family and e.get("confidence") == confidence
        ]
        if len(events) < _MIN_SAMPLE_SIZE:
            return None
        merged = sum(1 for e in events if e["outcome"] == "merged")
        total = len(events)
        return merged / total

    def report(self) -> list[CalibrationRecord]:
        """Return calibration records for all (family, confidence) pairs with enough data."""
        with self._lock:
            data = self._load()
        events = data.get("events", [])

        # Group by (family, confidence)
        groups: dict[tuple[str, str], list[str]] = {}
        for e in events:
            key = (str(e.get("family", "")), str(e.get("confidence", "")))
            if key[0] and key[1] in _EXPECTED_RATES:
                groups.setdefault(key, []).append(str(e.get("outcome", "")))

        records: list[CalibrationRecord] = []
        for (family, confidence), outcomes in sorted(groups.items()):
            if len(outcomes) < _MIN_SAMPLE_SIZE:
                continue
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
