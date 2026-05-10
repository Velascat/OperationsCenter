# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Task templates per (target_repo_id, consumer_repo_id).

Each consumer that fires gets a Plane task with a templated title +
body + label set. The registry holds the templates; lookup is by
(target, consumer) with a sensible default fallback.

Templates support a small set of substitution keys:
    {target}        — target's canonical_name
    {target_repo_id}
    {consumer}      — consumer's canonical_name
    {consumer_repo_id}
    {edge_type}
    {target_version}  — optional commit/version provided by caller

The defaults are deliberately operational, not LLM-prompty: operators
expand them later. The registry's role is "what task should we file?",
not "what prompt drives the agent?".
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Mapping


_DEFAULT_TITLE = "Re-validate {consumer} after {target} change"
_DEFAULT_BODY_PRELUDE = (
    "{consumer} depends on {target} via {edge_type}. {target} changed "
    "(version: {target_version}); please re-run the consumer's validation "
    "pipeline (tests, schema checks, sample fixtures) and confirm green."
)
_DEFAULT_LABELS = ("revalidation", "pending-review")


@dataclass(frozen=True)
class TaskTemplate:
    """One template — title/body/labels for a propagation task."""

    title: str = _DEFAULT_TITLE
    body_prelude: str = _DEFAULT_BODY_PRELUDE
    labels: tuple[str, ...] = _DEFAULT_LABELS

    def render(
        self,
        *,
        target: str,
        target_repo_id: str,
        consumer: str,
        consumer_repo_id: str,
        edge_type: str,
        target_version: str | None = None,
    ) -> "RenderedTask":
        """Apply substitution keys; returns a `RenderedTask`."""
        ctx = {
            "target": target,
            "target_repo_id": target_repo_id,
            "consumer": consumer,
            "consumer_repo_id": consumer_repo_id,
            "edge_type": edge_type,
            "target_version": target_version or "unspecified",
        }
        return RenderedTask(
            title=self.title.format(**ctx),
            body_prelude=self.body_prelude.format(**ctx),
            labels=self.labels,
        )


@dataclass(frozen=True)
class RenderedTask:
    """Concrete title/body/labels — what a Plane client receives.

    The propagator builds the *full* body by combining ``body_prelude``
    with the parent-link block (see ``links.py``). The registry only
    owns the prelude.
    """

    title: str
    body_prelude: str
    labels: tuple[str, ...]


@dataclass
class PropagationRegistry:
    """Lookup table: (target_repo_id, consumer_repo_id) → TaskTemplate.

    Falls back to (target_repo_id, "*") then ("*", consumer_repo_id)
    then a built-in default. This lets operators ship per-target or
    per-consumer overrides without enumerating every pair.
    """

    _by_pair: dict[tuple[str, str], TaskTemplate] = field(default_factory=dict)

    @classmethod
    def from_mapping(
        cls,
        overrides: Mapping[tuple[str, str], TaskTemplate] | None = None,
    ) -> "PropagationRegistry":
        return cls(_by_pair=dict(overrides or {}))

    def lookup(
        self,
        target_repo_id: str,
        consumer_repo_id: str,
    ) -> TaskTemplate:
        """Return the most-specific matching template, or the default."""
        candidates = [
            (target_repo_id, consumer_repo_id),
            (target_repo_id, "*"),
            ("*", consumer_repo_id),
        ]
        for key in candidates:
            if key in self._by_pair:
                return self._by_pair[key]
        return TaskTemplate()

    def register(
        self,
        target_repo_id: str,
        consumer_repo_id: str,
        template: TaskTemplate,
    ) -> "PropagationRegistry":
        """Return a new registry with `template` registered for the pair.

        Frozen-style mutation — caller gets a new registry, original
        unchanged. Lets operators build registries incrementally
        without state surprises.
        """
        merged = dict(self._by_pair)
        merged[(target_repo_id, consumer_repo_id)] = template
        return replace(self, _by_pair=merged)


__all__ = [
    "PropagationRegistry",
    "RenderedTask",
    "TaskTemplate",
]
