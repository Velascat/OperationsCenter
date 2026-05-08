# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""ContractChangePropagator — orchestrates one propagation run.

Inputs:
    - target_repo_id (e.g. "cxrp")
    - target_version (commit SHA / version tag — used for dedup)
    - EffectiveRepoGraph (provides the impact set)
    - PropagationPolicy (decides per-pair: skip / backlog / ready)
    - PropagationRegistry (task title/body/labels per pair)
    - PropagationDedupStore (idempotency)
    - TaskCreator (Plane client adapter — Protocol, so tests inject a fake)

Output:
    PropagationRecord — structured artifact written to state/propagation/.
    Operators read these to answer "why did/didn't propagation fire?"
    even when no Plane tasks were created.
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from operations_center.impact_analysis import compute_contract_impact

from .dedup import DedupKey, PropagationDedupStore
from .links import ParentLink
from .policy import PropagationPolicy, _Action
from .registry import PropagationRegistry, RenderedTask

logger = logging.getLogger(__name__)


@runtime_checkable
class _TaskCreator(Protocol):
    """Minimal Plane-client interface the propagator needs.

    Real adapter wraps `PlaneClient.create_issue` plus an optional
    transition to "Ready for AI" state when the policy says so.
    """

    def create_issue(
        self,
        *,
        title: str,
        body: str,
        labels: tuple[str, ...],
        promote_to_ready: bool,
    ) -> str:
        """Create the issue. Returns Plane's issue ID."""
        ...


@dataclass(frozen=True)
class PropagationOutcome:
    """One per consumer in the impact set — what we did + why."""

    consumer_repo_id: str
    consumer_canonical: str
    decision_action: str       # "skip" | "backlog" | "ready_for_ai"
    decision_reason: str
    issue_id: str | None = None  # Plane issue when fired; None when skipped
    error: str | None = None    # populated only if create_issue raised


@dataclass
class PropagationRecord:
    """Structured artifact for one propagation run.

    Always written, regardless of whether tasks fired. The operator's
    audit trail. Schema version-tagged so future evolution stays
    backward-compatible.
    """

    schema_version: str = "1.0"
    propagator_run_id: str = ""
    target_repo_id: str = ""
    target_canonical: str = ""
    target_version: str = ""
    triggered_at: str = ""
    policy_summary: dict[str, Any] = field(default_factory=dict)
    outcomes: list[PropagationOutcome] = field(default_factory=list)
    impact_summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "propagator_run_id": self.propagator_run_id,
            "target_repo_id": self.target_repo_id,
            "target_canonical": self.target_canonical,
            "target_version": self.target_version,
            "triggered_at": self.triggered_at,
            "policy_summary": self.policy_summary,
            "outcomes": [
                {
                    "consumer_repo_id": o.consumer_repo_id,
                    "consumer_canonical": o.consumer_canonical,
                    "decision_action": o.decision_action,
                    "decision_reason": o.decision_reason,
                    "issue_id": o.issue_id,
                    "error": o.error,
                }
                for o in self.outcomes
            ],
            "impact_summary": self.impact_summary,
        }


@dataclass
class ContractChangePropagator:
    """Orchestrate one propagation run across the impact set."""

    policy: PropagationPolicy
    registry: PropagationRegistry
    dedup: PropagationDedupStore
    task_creator: _TaskCreator
    record_dir: Path  # state/propagation/ — must exist or will be created

    def propagate(
        self,
        *,
        target_repo_id: str,
        target_version: str,
        graph,  # platform_manifest.RepoGraph (duck-typed for ease-of-test)
        now: datetime | None = None,
    ) -> PropagationRecord:
        """Walk the impact set; apply policy/dedup; create tasks; record outcome.

        Returns the record (also persisted to ``record_dir``).
        """
        triggered_at = now or datetime.now(timezone.utc)
        run_id = uuid.uuid4().hex
        record = PropagationRecord(
            propagator_run_id=run_id,
            target_repo_id=target_repo_id,
            target_version=target_version,
            triggered_at=triggered_at.isoformat(),
            policy_summary={
                "enabled": self.policy.settings.enabled,
                "auto_trigger_edge_types": sorted(self.policy.settings.auto_trigger_edge_types),
                "dedup_window_hours": self.policy.settings.dedup_window_hours,
            },
        )

        target_node = graph.resolve(target_repo_id)
        if target_node is None:
            record.target_canonical = "(unknown)"
            record.impact_summary = {"affected_count": 0, "note": "target not in graph"}
            self._persist(record)
            return record

        record.target_canonical = target_node.canonical_name

        impact = compute_contract_impact(graph, target_repo_id)
        if impact is None or not impact.has_impact():
            record.impact_summary = {
                "affected_count": 0,
                "public_affected": [],
                "private_affected": [],
            }
            self._persist(record)
            return record

        record.impact_summary = {
            "affected_count": len(impact.affected),
            "public_affected": [n.canonical_name for n in impact.public_affected],
            "private_affected": [n.canonical_name for n in impact.private_affected],
        }

        for consumer in impact.affected:
            outcome = self._propagate_one(
                target_node=target_node,
                consumer=consumer,
                target_version=target_version,
                run_id=run_id,
                triggered_at=triggered_at,
            )
            record.outcomes.append(outcome)

        self._persist(record)
        return record

    def _propagate_one(
        self,
        *,
        target_node,
        consumer,
        target_version: str,
        run_id: str,
        triggered_at: datetime,
    ) -> PropagationOutcome:
        edge_type = "depends_on_contracts_from"  # the only impact source v1

        decision = self.policy.decide(
            target_repo_id=target_node.repo_id,
            consumer_repo_id=consumer.repo_id,
            edge_type=edge_type,
        )
        if not decision.fires_task():
            return PropagationOutcome(
                consumer_repo_id=consumer.repo_id,
                consumer_canonical=consumer.canonical_name,
                decision_action=decision.action.value,
                decision_reason=decision.reason,
            )

        dedup_key = DedupKey(
            target_repo_id=target_node.repo_id,
            consumer_repo_id=consumer.repo_id,
            target_version=target_version,
        )
        if self.dedup.is_recent(
            dedup_key,
            window_hours=self.policy.settings.dedup_window_hours,
            now=triggered_at,
        ):
            return PropagationOutcome(
                consumer_repo_id=consumer.repo_id,
                consumer_canonical=consumer.canonical_name,
                decision_action="skip",
                decision_reason="dedup window not yet elapsed",
            )

        template = self.registry.lookup(target_node.repo_id, consumer.repo_id)
        rendered: RenderedTask = template.render(
            target=target_node.canonical_name,
            target_repo_id=target_node.repo_id,
            consumer=consumer.canonical_name,
            consumer_repo_id=consumer.repo_id,
            edge_type=edge_type,
            target_version=target_version,
        )
        link = ParentLink(
            target=target_node.canonical_name,
            target_repo_id=target_node.repo_id,
            target_version=target_version,
            edge_type=edge_type,
            triggered_at=triggered_at,
            propagator_run_id=run_id,
        )
        body = rendered.body_prelude + "\n\n" + link.render()

        try:
            issue_id = self.task_creator.create_issue(
                title=rendered.title,
                body=body,
                labels=rendered.labels,
                promote_to_ready=(decision.action is _Action.READY_FOR_AI),
            )
        except Exception as exc:  # noqa: BLE001 — defensive: never crash the run
            logger.warning(
                "propagation task creation failed for %s → %s: %s",
                target_node.canonical_name, consumer.canonical_name, exc,
            )
            return PropagationOutcome(
                consumer_repo_id=consumer.repo_id,
                consumer_canonical=consumer.canonical_name,
                decision_action=decision.action.value,
                decision_reason=decision.reason,
                error=f"create_issue failed: {exc}",
            )

        # Stamp dedup only on successful creation.
        self.dedup.record(dedup_key, now=triggered_at)
        return PropagationOutcome(
            consumer_repo_id=consumer.repo_id,
            consumer_canonical=consumer.canonical_name,
            decision_action=decision.action.value,
            decision_reason=decision.reason,
            issue_id=issue_id,
        )

    def _persist(self, record: PropagationRecord) -> None:
        """Write the record artifact. Always runs — observability floor."""
        self.record_dir.mkdir(parents=True, exist_ok=True)
        out = self.record_dir / f"{record.propagator_run_id}.json"
        out.write_text(
            json.dumps(record.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info(
            "propagation record written: %s (target=%s, version=%s, "
            "impact=%d, outcomes=%d)",
            out,
            record.target_canonical,
            record.target_version,
            record.impact_summary.get("affected_count", 0),
            len(record.outcomes),
        )


__all__ = ["ContractChangePropagator", "PropagationOutcome", "PropagationRecord"]
