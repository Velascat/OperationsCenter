# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Tests for LaneDecision."""

from __future__ import annotations

import json

import pytest

from operations_center.contracts.routing import LaneDecision
from operations_center.contracts.enums import BackendName, LaneName


def _minimal_decision(**kw) -> LaneDecision:
    defaults = dict(
        proposal_id="prop-123",
        selected_lane=LaneName.AIDER_LOCAL,
        selected_backend=BackendName.DIRECT_LOCAL,
    )
    defaults.update(kw)
    return LaneDecision(**defaults)


class TestLaneDecisionConstruction:
    def test_minimal(self):
        d = _minimal_decision()
        assert d.proposal_id == "prop-123"
        assert d.selected_lane == LaneName.AIDER_LOCAL
        assert d.selected_backend == BackendName.DIRECT_LOCAL

    def test_auto_decision_id(self):
        d1 = _minimal_decision()
        d2 = _minimal_decision()
        assert d1.decision_id != d2.decision_id

    def test_defaults(self):
        d = _minimal_decision()
        assert d.confidence == 1.0
        assert d.policy_rule_matched is None
        assert d.rationale is None
        assert d.alternatives_considered == []
        assert d.switchboard_version is None

    def test_full_construction(self):
        d = LaneDecision(
            proposal_id="prop-456",
            selected_lane=LaneName.CLAUDE_CLI,
            selected_backend=BackendName.KODO,
            confidence=0.92,
            policy_rule_matched="high-risk-to-premium",
            rationale="Task risk_level=HIGH; policy mandates premium lane",
            alternatives_considered=[LaneName.AIDER_LOCAL],
            switchboard_version="1.2.3",
        )
        assert d.confidence == 0.92
        assert d.policy_rule_matched == "high-risk-to-premium"
        assert LaneName.AIDER_LOCAL in d.alternatives_considered

    def test_frozen(self):
        d = _minimal_decision()
        with pytest.raises(Exception):
            d.proposal_id = "other"  # type: ignore[misc]


class TestLaneDecisionConfidenceBounds:
    def test_zero_confidence_allowed(self):
        d = _minimal_decision(confidence=0.0)
        assert d.confidence == 0.0

    def test_one_confidence_allowed(self):
        d = _minimal_decision(confidence=1.0)
        assert d.confidence == 1.0

    def test_above_one_raises(self):
        with pytest.raises(Exception):
            _minimal_decision(confidence=1.1)

    def test_below_zero_raises(self):
        with pytest.raises(Exception):
            _minimal_decision(confidence=-0.1)


class TestLaneDecisionSerialization:
    def test_json_round_trip(self):
        d = _minimal_decision()
        restored = LaneDecision.model_validate_json(d.model_dump_json())
        assert restored == d

    def test_json_contains_string_enum_values(self):
        d = _minimal_decision()
        parsed = json.loads(d.model_dump_json())
        assert parsed["selected_lane"] == "aider_local"
        assert parsed["selected_backend"] == "direct_local"

    def test_alternatives_serialised_as_list(self):
        d = LaneDecision(
            proposal_id="p",
            selected_lane=LaneName.CLAUDE_CLI,
            selected_backend=BackendName.KODO,
            alternatives_considered=[LaneName.AIDER_LOCAL, LaneName.CODEX_CLI],
        )
        parsed = json.loads(d.model_dump_json())
        assert parsed["alternatives_considered"] == ["aider_local", "codex_cli"]
