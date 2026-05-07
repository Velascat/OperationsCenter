# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Card synthesizer — derives capability/runtime cards from real samples.

Per the spec (Phase 8): "Cards are outputs of integration discovery, not
aspirational design docs. Must be derived from actual adapter behavior."

This module turns the principle into code. Two modes:

- ``synthesize_from_samples(backend_id)`` — batch synthesis from all
  current sample files. Idempotent. Use this from CI / one-shot scripts.

- ``OngoingSynthesizer`` — incremental updater. Call ``observe(capture)``
  after each real backend run; the synthesizer updates the in-memory
  card aggregate, flushes to disk on a configurable cadence (default:
  every 10 observations or 60 seconds).

Subjective fields are never touched (capability_card.yaml's hard rule:
objective only). The synthesizer writes only:
  - measured_constraints.{max_observed_files_changed, ...}
  - advertised_capabilities (only adds; never removes — humans curate)
  - supported_runtime_kinds (only adds)
  - supported_selection_modes (only adds)

Diff-on-disk safety: the synthesizer writes to ``<card>.synthesized.yaml``
and emits a CLI-friendly diff. Humans merge by hand. This avoids an
auto-rewriter silently overwriting human curation.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml



_DEFAULT_FLUSH_EVERY_OBSERVATIONS = 10
_DEFAULT_FLUSH_EVERY_SECONDS = 60.0


@dataclass
class SynthesizedFacts:
    """Aggregate facts derived from a backend's sample corpus."""

    backend_id: str
    sample_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    max_files_changed: int = 0
    max_commands_run: int = 0
    max_tests_run: int = 0
    observed_capabilities: set[str] = field(default_factory=set)
    observed_runtime_kinds: set[str] = field(default_factory=set)
    observed_selection_modes: set[str] = field(default_factory=set)

    def to_capability_card_diff(self, baseline: dict[str, Any] | None = None) -> dict[str, Any]:
        baseline = baseline or {}
        existing_caps = set(baseline.get("advertised_capabilities") or [])
        new_caps = sorted(existing_caps | self.observed_capabilities)
        return {
            "backend_id": self.backend_id,
            "backend_version": baseline.get("backend_version", "unknown"),
            "advertised_capabilities": new_caps,
            "measured_constraints": {
                "max_observed_files_changed": self.max_files_changed,
                "max_observed_commands_run": self.max_commands_run,
                "max_observed_tests_run": self.max_tests_run,
                "sample_count": self.sample_count,
                "success_rate": (
                    round(self.success_count / self.sample_count, 3)
                    if self.sample_count else 0.0
                ),
            },
            "known_capability_gaps": baseline.get("known_capability_gaps", []),
        }

    def to_runtime_support_diff(self, baseline: dict[str, Any] | None = None) -> dict[str, Any]:
        baseline = baseline or {}
        existing_kinds = set(baseline.get("supported_runtime_kinds") or [])
        existing_modes = set(baseline.get("supported_selection_modes") or [])
        return {
            "backend_id": self.backend_id,
            "backend_version": baseline.get("backend_version", "unknown"),
            "supported_runtime_kinds": sorted(existing_kinds | self.observed_runtime_kinds),
            "supported_selection_modes": sorted(existing_modes | self.observed_selection_modes),
            "known_runtime_gaps": baseline.get("known_runtime_gaps", []),
        }


def _read_sample(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _facts_from_sample(facts: SynthesizedFacts, raw_sample: dict[str, Any]) -> None:
    """Mutate ``facts`` to absorb one raw_output sample."""
    raw = raw_sample.get("raw_output") or {}
    if not isinstance(raw, dict):
        return
    facts.sample_count += 1

    exit_code = raw.get("exit_code")
    if exit_code == 0 or raw.get("outcome") in ("success", "succeeded"):
        facts.success_count += 1
    else:
        facts.failure_count += 1

    fc = raw.get("files_changed") or []
    cr = raw.get("commands_run") or []
    tr = raw.get("tests_run") or []
    if isinstance(fc, list):
        facts.max_files_changed = max(facts.max_files_changed, len(fc))
    if isinstance(cr, list):
        facts.max_commands_run = max(facts.max_commands_run, len(cr))
    if isinstance(tr, list):
        facts.max_tests_run = max(facts.max_tests_run, len(tr))

    # Capability inference: derive from observed actions.
    if fc:
        facts.observed_capabilities.add("repo_patch")
    if tr:
        facts.observed_capabilities.add("test_run")
    if cr:
        facts.observed_capabilities.add("shell_write")
    # If the sample mentions any path read at all, repo_read holds.
    if any(k in raw for k in ("files_read", "files_changed", "stdout")):
        facts.observed_capabilities.add("repo_read")

    # Runtime inference: walk extras + invocation for runtime metadata.
    extras = raw_sample.get("extras") or {}
    if isinstance(extras, dict):
        rk = extras.get("runtime_kind")
        sm = extras.get("selection_mode")
        if isinstance(rk, str):
            facts.observed_runtime_kinds.add(rk)
        if isinstance(sm, str):
            facts.observed_selection_modes.add(sm)


def synthesize_from_samples(
    backend_dir: Path,
    *,
    backend_id: str | None = None,
) -> SynthesizedFacts:
    """Walk ``samples/raw_output/*.json`` and synthesize aggregate facts."""
    backend_id = backend_id or backend_dir.name
    facts = SynthesizedFacts(backend_id=backend_id)
    raw_dir = backend_dir / "samples" / "raw_output"
    if not raw_dir.is_dir():
        return facts
    for sample_path in sorted(raw_dir.glob("*.json")):
        sample = _read_sample(sample_path)
        if sample is not None:
            _facts_from_sample(facts, sample)
    return facts


def _load_yaml_or_empty(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def write_synthesized_diffs(
    backend_dir: Path,
    facts: SynthesizedFacts,
    *,
    suffix: str = ".synthesized.yaml",
) -> tuple[Path, Path]:
    """Write proposed cards to ``capability_card<suffix>`` and
    ``runtime_support<suffix>``. Humans review + merge by hand."""
    cap_baseline = _load_yaml_or_empty(backend_dir / "capability_card.yaml")
    rs_baseline = _load_yaml_or_empty(backend_dir / "runtime_support.yaml")

    cap_diff = facts.to_capability_card_diff(cap_baseline)
    rs_diff = facts.to_runtime_support_diff(rs_baseline)

    cap_path = backend_dir / f"capability_card{suffix}"
    rs_path = backend_dir / f"runtime_support{suffix}"

    cap_path.write_text(yaml.safe_dump(cap_diff, sort_keys=False), encoding="utf-8")
    rs_path.write_text(yaml.safe_dump(rs_diff, sort_keys=False), encoding="utf-8")
    return cap_path, rs_path


# ── Ongoing synthesizer ────────────────────────────────────────────────


@dataclass
class OngoingSynthesizer:
    """Incremental updater. Call ``observe(capture)`` after each real run.

    Maintains an in-memory ``SynthesizedFacts`` per backend. Flushes the
    proposed cards to disk on a cadence to keep the synthesized diffs
    reasonably fresh without thrashing the filesystem.
    """

    backend_dir: Path
    backend_id: Optional[str] = None
    flush_every_observations: int = _DEFAULT_FLUSH_EVERY_OBSERVATIONS
    flush_every_seconds: float = _DEFAULT_FLUSH_EVERY_SECONDS

    _facts: SynthesizedFacts = field(init=False)
    _observations_since_flush: int = field(init=False, default=0)
    _last_flush_at: float = field(init=False, default=0.0)

    def __post_init__(self) -> None:
        self.backend_id = self.backend_id or self.backend_dir.name
        self._facts = synthesize_from_samples(self.backend_dir, backend_id=self.backend_id)
        self._last_flush_at = time.monotonic()

    @property
    def facts(self) -> SynthesizedFacts:
        return self._facts

    def observe(self, raw_sample: dict[str, Any]) -> bool:
        """Absorb one sample. Returns True when a flush happened."""
        _facts_from_sample(self._facts, raw_sample)
        self._observations_since_flush += 1
        return self._maybe_flush()

    def _maybe_flush(self) -> bool:
        now = time.monotonic()
        if (
            self._observations_since_flush >= self.flush_every_observations
            or (now - self._last_flush_at) >= self.flush_every_seconds
        ):
            self.flush()
            return True
        return False

    def flush(self) -> tuple[Path, Path]:
        out = write_synthesized_diffs(self.backend_dir, self._facts)
        self._observations_since_flush = 0
        self._last_flush_at = time.monotonic()
        return out
