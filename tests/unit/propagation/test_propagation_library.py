# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for the propagation library (R5.1).

Covers each module independently + end-to-end propagator orchestration.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


from operations_center.propagation import (
    ContractChangePropagator,
    DedupKey,
    ParentLink,
    PropagationDedupStore,
    PropagationPolicy,
    PropagationRegistry,
    PropagationSettings,
    TaskTemplate,
    format_parent_link,
)
from operations_center.propagation.policy import _Action, _PairOverride
from operations_center.repo_graph_factory import build_effective_repo_graph


# ---------------------------------------------------------------------------
# policy.py
# ---------------------------------------------------------------------------


class TestPolicy:
    def test_disabled_default_skips_everything(self) -> None:
        pol = PropagationPolicy.disabled()
        d = pol.decide(target_repo_id="cxrp", consumer_repo_id="oc", edge_type="depends_on_contracts_from")
        assert not d.fires_task()
        assert "disabled globally" in d.reason

    def test_enabled_without_edge_type_opt_in_skips(self) -> None:
        pol = PropagationPolicy(settings=PropagationSettings(enabled=True))
        d = pol.decide(target_repo_id="cxrp", consumer_repo_id="oc", edge_type="depends_on_contracts_from")
        assert not d.fires_task()
        assert "not in auto_trigger_edge_types" in d.reason

    def test_edge_type_opt_in_fires_to_backlog(self) -> None:
        pol = PropagationPolicy(settings=PropagationSettings(
            enabled=True,
            auto_trigger_edge_types=frozenset({"depends_on_contracts_from"}),
        ))
        d = pol.decide(target_repo_id="cxrp", consumer_repo_id="oc", edge_type="depends_on_contracts_from")
        assert d.action is _Action.BACKLOG
        assert d.fires_task()

    def test_pair_override_promotes_to_ready(self) -> None:
        override = _PairOverride(action=_Action.READY_FOR_AI, reason="trusted pair")
        pol = PropagationPolicy(settings=PropagationSettings(
            enabled=True,
            auto_trigger_edge_types=frozenset(),
            pair_overrides=(("cxrp", "oc", override),),
        ))
        d = pol.decide(target_repo_id="cxrp", consumer_repo_id="oc", edge_type="depends_on_contracts_from")
        assert d.action is _Action.READY_FOR_AI

    def test_pair_override_can_suppress(self) -> None:
        override = _PairOverride(action=_Action.SKIP, reason="manual blocklist")
        pol = PropagationPolicy(settings=PropagationSettings(
            enabled=True,
            auto_trigger_edge_types=frozenset({"depends_on_contracts_from"}),
            pair_overrides=(("cxrp", "switchboard", override),),
        ))
        d = pol.decide(target_repo_id="cxrp", consumer_repo_id="switchboard", edge_type="depends_on_contracts_from")
        assert not d.fires_task()


# ---------------------------------------------------------------------------
# registry.py
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_default_template_renders_with_substitutions(self) -> None:
        registry = PropagationRegistry.from_mapping()
        template = registry.lookup("cxrp", "operations_center")
        rendered = template.render(
            target="CxRP",
            target_repo_id="cxrp",
            consumer="OperationsCenter",
            consumer_repo_id="operations_center",
            edge_type="depends_on_contracts_from",
            target_version="abc123",
        )
        assert "CxRP" in rendered.title
        assert "OperationsCenter" in rendered.title
        assert "abc123" in rendered.body_prelude
        assert "revalidation" in rendered.labels
        assert "pending-review" in rendered.labels

    def test_pair_specific_override_wins(self) -> None:
        custom = TaskTemplate(title="Custom: {target} → {consumer}")
        registry = PropagationRegistry.from_mapping({("cxrp", "oc"): custom})
        result = registry.lookup("cxrp", "oc")
        assert result.title == "Custom: {target} → {consumer}"

    def test_target_wildcard_fallback(self) -> None:
        wildcard = TaskTemplate(title="Wildcard for {target}")
        registry = PropagationRegistry.from_mapping({("cxrp", "*"): wildcard})
        # Pair-specific not set; falls back to (target, "*")
        result = registry.lookup("cxrp", "any_consumer")
        assert result.title == "Wildcard for {target}"

    def test_register_returns_new_registry(self) -> None:
        original = PropagationRegistry.from_mapping()
        custom = TaskTemplate(title="Custom")
        updated = original.register("cxrp", "oc", custom)
        # Original unchanged
        assert original.lookup("cxrp", "oc").title != "Custom"
        # Updated has the override
        assert updated.lookup("cxrp", "oc").title == "Custom"


# ---------------------------------------------------------------------------
# dedup.py
# ---------------------------------------------------------------------------


class TestDedup:
    def test_empty_store_is_not_recent(self, tmp_path: Path) -> None:
        store = PropagationDedupStore(path=tmp_path / "dedup.json")
        key = DedupKey(target_repo_id="cxrp", consumer_repo_id="oc", target_version="v1")
        assert not store.is_recent(key, window_hours=24)

    def test_record_then_is_recent_within_window(self, tmp_path: Path) -> None:
        store = PropagationDedupStore(path=tmp_path / "dedup.json")
        key = DedupKey(target_repo_id="cxrp", consumer_repo_id="oc", target_version="v1")
        now = datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc)
        store.record(key, now=now)
        # Within window
        assert store.is_recent(key, window_hours=24, now=now + timedelta(hours=12))
        # Outside window
        assert not store.is_recent(key, window_hours=24, now=now + timedelta(hours=25))

    def test_different_versions_dont_collide(self, tmp_path: Path) -> None:
        store = PropagationDedupStore(path=tmp_path / "dedup.json")
        key1 = DedupKey(target_repo_id="cxrp", consumer_repo_id="oc", target_version="v1")
        key2 = DedupKey(target_repo_id="cxrp", consumer_repo_id="oc", target_version="v2")
        now = datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc)
        store.record(key1, now=now)
        # v2 is fresh; not deduped
        assert not store.is_recent(key2, window_hours=24, now=now)

    def test_corrupt_file_returns_empty(self, tmp_path: Path) -> None:
        path = tmp_path / "dedup.json"
        path.write_text("not valid json{{{", encoding="utf-8")
        store = PropagationDedupStore(path=path)
        assert store.load() == {}


# ---------------------------------------------------------------------------
# links.py
# ---------------------------------------------------------------------------


class TestLinks:
    def test_render_includes_all_fields(self) -> None:
        link = ParentLink(
            target="CxRP",
            target_repo_id="cxrp",
            target_version="abc123",
            edge_type="depends_on_contracts_from",
            triggered_at=datetime(2026, 5, 8, 14, 23, 11, tzinfo=timezone.utc),
            propagator_run_id="run-1f9c",
        )
        rendered = format_parent_link(link)
        assert "<!-- propagation:source -->" in rendered
        assert "<!-- /propagation:source -->" in rendered
        assert "target: CxRP" in rendered
        assert "target_repo_id: cxrp" in rendered
        assert "target_version: abc123" in rendered
        assert "propagator_run_id: run-1f9c" in rendered
        assert "2026-05-08T14:23:11" in rendered


# ---------------------------------------------------------------------------
# propagator.py — orchestrator integration
# ---------------------------------------------------------------------------


class _RecordingTaskCreator:
    """Test double; records every call for assertion."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.next_issue_id: str = "ISSUE-1"
        self.fail_with: Exception | None = None

    def create_issue(self, *, title, body, labels, promote_to_ready):  # type: ignore[no-untyped-def]
        self.calls.append({
            "title": title,
            "body": body,
            "labels": labels,
            "promote_to_ready": promote_to_ready,
        })
        if self.fail_with is not None:
            raise self.fail_with
        return self.next_issue_id


def _enabled_policy_with_default_edge() -> PropagationPolicy:
    return PropagationPolicy(settings=PropagationSettings(
        enabled=True,
        auto_trigger_edge_types=frozenset({"depends_on_contracts_from"}),
        dedup_window_hours=24,
    ))


class TestPropagatorEndToEnd:
    def test_disabled_policy_writes_record_with_zero_outcomes(self, tmp_path: Path) -> None:
        creator = _RecordingTaskCreator()
        prop = ContractChangePropagator(
            policy=PropagationPolicy.disabled(),
            registry=PropagationRegistry.from_mapping(),
            dedup=PropagationDedupStore(path=tmp_path / "dedup.json"),
            task_creator=creator,
            record_dir=tmp_path / "records",
        )
        graph = build_effective_repo_graph()
        record = prop.propagate(target_repo_id="cxrp", target_version="v1", graph=graph)
        # Every consumer landed in outcomes with action=skip
        assert all(o.decision_action == "skip" for o in record.outcomes)
        # No tasks created
        assert creator.calls == []
        # Record persisted
        records_dir = tmp_path / "records"
        files = list(records_dir.glob("*.json"))
        assert len(files) == 1

    def test_enabled_policy_creates_tasks_for_contract_consumers(
        self, tmp_path: Path
    ) -> None:
        creator = _RecordingTaskCreator()
        prop = ContractChangePropagator(
            policy=_enabled_policy_with_default_edge(),
            registry=PropagationRegistry.from_mapping(),
            dedup=PropagationDedupStore(path=tmp_path / "dedup.json"),
            task_creator=creator,
            record_dir=tmp_path / "records",
        )
        graph = build_effective_repo_graph()
        record = prop.propagate(target_repo_id="cxrp", target_version="v1", graph=graph)
        # CxRP has 3 platform consumers — tasks for each
        assert len(creator.calls) == 3
        consumer_canonicals = {o.consumer_canonical for o in record.outcomes}
        assert {"OperationsCenter", "SwitchBoard", "OperatorConsole"}.issubset(consumer_canonicals)
        # All landed in BACKLOG (not promoted)
        assert all(call["promote_to_ready"] is False for call in creator.calls)
        # Each body contains the parent-link block
        assert all("<!-- propagation:source -->" in call["body"] for call in creator.calls)
        # CxRP version present in every body
        assert all("v1" in call["body"] for call in creator.calls)

    def test_dedup_blocks_immediate_re_fire(self, tmp_path: Path) -> None:
        creator = _RecordingTaskCreator()
        prop = ContractChangePropagator(
            policy=_enabled_policy_with_default_edge(),
            registry=PropagationRegistry.from_mapping(),
            dedup=PropagationDedupStore(path=tmp_path / "dedup.json"),
            task_creator=creator,
            record_dir=tmp_path / "records",
        )
        graph = build_effective_repo_graph()
        prop.propagate(target_repo_id="cxrp", target_version="v1", graph=graph)
        first_call_count = len(creator.calls)
        # Same version, same propagator — should all dedup
        prop.propagate(target_repo_id="cxrp", target_version="v1", graph=graph)
        assert len(creator.calls) == first_call_count  # no new calls

    def test_new_version_bypasses_dedup(self, tmp_path: Path) -> None:
        creator = _RecordingTaskCreator()
        prop = ContractChangePropagator(
            policy=_enabled_policy_with_default_edge(),
            registry=PropagationRegistry.from_mapping(),
            dedup=PropagationDedupStore(path=tmp_path / "dedup.json"),
            task_creator=creator,
            record_dir=tmp_path / "records",
        )
        graph = build_effective_repo_graph()
        prop.propagate(target_repo_id="cxrp", target_version="v1", graph=graph)
        first_count = len(creator.calls)
        prop.propagate(target_repo_id="cxrp", target_version="v2", graph=graph)
        # v2 is a different key — fires again
        assert len(creator.calls) == 2 * first_count

    def test_pair_override_promotes(self, tmp_path: Path) -> None:
        creator = _RecordingTaskCreator()
        override = _PairOverride(action=_Action.READY_FOR_AI, reason="trusted pair")
        policy = PropagationPolicy(settings=PropagationSettings(
            enabled=True,
            auto_trigger_edge_types=frozenset(),  # only fires via override
            pair_overrides=(("cxrp", "operations_center", override),),
        ))
        prop = ContractChangePropagator(
            policy=policy,
            registry=PropagationRegistry.from_mapping(),
            dedup=PropagationDedupStore(path=tmp_path / "dedup.json"),
            task_creator=creator,
            record_dir=tmp_path / "records",
        )
        graph = build_effective_repo_graph()
        prop.propagate(target_repo_id="cxrp", target_version="v1", graph=graph)
        # OC-to-cxrp got Ready promotion; SB and OperatorConsole got SKIP (no edge_type opt-in)
        promoted = [c for c in creator.calls if c["promote_to_ready"]]
        assert len(promoted) == 1

    def test_unknown_target_writes_record_no_calls(self, tmp_path: Path) -> None:
        creator = _RecordingTaskCreator()
        prop = ContractChangePropagator(
            policy=_enabled_policy_with_default_edge(),
            registry=PropagationRegistry.from_mapping(),
            dedup=PropagationDedupStore(path=tmp_path / "dedup.json"),
            task_creator=creator,
            record_dir=tmp_path / "records",
        )
        graph = build_effective_repo_graph()
        record = prop.propagate(target_repo_id="ghost-repo", target_version="v1", graph=graph)
        assert record.target_canonical == "(unknown)"
        assert record.outcomes == []
        assert creator.calls == []
        # Record still written — observability floor
        assert (tmp_path / "records").exists()

    def test_create_issue_failure_recorded_in_outcome(self, tmp_path: Path) -> None:
        creator = _RecordingTaskCreator()
        creator.fail_with = RuntimeError("plane API down")
        prop = ContractChangePropagator(
            policy=_enabled_policy_with_default_edge(),
            registry=PropagationRegistry.from_mapping(),
            dedup=PropagationDedupStore(path=tmp_path / "dedup.json"),
            task_creator=creator,
            record_dir=tmp_path / "records",
        )
        graph = build_effective_repo_graph()
        record = prop.propagate(target_repo_id="cxrp", target_version="v1", graph=graph)
        assert all(
            o.error and "plane API down" in o.error
            for o in record.outcomes
        )
        # Dedup was NOT stamped (so retry can re-fire after fixing Plane)
        store_path = tmp_path / "dedup.json"
        if store_path.exists():
            payload = json.loads(store_path.read_text())
            assert payload.get("entries") == {}

    def test_record_artifact_schema(self, tmp_path: Path) -> None:
        creator = _RecordingTaskCreator()
        prop = ContractChangePropagator(
            policy=_enabled_policy_with_default_edge(),
            registry=PropagationRegistry.from_mapping(),
            dedup=PropagationDedupStore(path=tmp_path / "dedup.json"),
            task_creator=creator,
            record_dir=tmp_path / "records",
        )
        graph = build_effective_repo_graph()
        record = prop.propagate(target_repo_id="cxrp", target_version="v1", graph=graph)
        # Read back from disk
        files = list((tmp_path / "records").glob("*.json"))
        assert len(files) == 1
        payload = json.loads(files[0].read_text(encoding="utf-8"))
        assert payload["schema_version"] == "1.0"
        assert payload["target_repo_id"] == "cxrp"
        assert payload["target_canonical"] == "CxRP"
        assert payload["target_version"] == "v1"
        assert "propagator_run_id" in payload
        assert "triggered_at" in payload
        assert "policy_summary" in payload
        assert "outcomes" in payload
        assert "impact_summary" in payload
        assert payload["impact_summary"]["affected_count"] == len(record.outcomes)
