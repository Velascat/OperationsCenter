# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Hardening arc item 5 — routing rationale completeness smoke."""

from __future__ import annotations

import pytest

from operations_center.contracts.cxrp_mapper import (
    from_cxrp_lane_decision,
    to_cxrp_lane_decision,
)
from operations_center.contracts.enums import BackendName, LaneName
from operations_center.contracts.routing import LaneDecision
from operations_center.routing.smoke import (
    IncompleteRoutingDecisionError,
    assert_decision_complete,
)


# ---------------------------------------------------------------------------
# assert_decision_complete
# ---------------------------------------------------------------------------


def _full_decision(**overrides) -> LaneDecision:
    base = dict(
        proposal_id="prop-1",
        selected_lane=LaneName.AIDER_LOCAL,
        selected_backend=BackendName.DIRECT_LOCAL,
        policy_rule_matched="lint_fix_to_aider_local",
        rationale="lint_fix tasks default to aider_local",
        switchboard_version="0.4.2",
    )
    base.update(overrides)
    return LaneDecision(**base)


def test_complete_decision_passes() -> None:
    assert_decision_complete(_full_decision())


def test_missing_policy_rule_fails() -> None:
    with pytest.raises(IncompleteRoutingDecisionError) as exc_info:
        assert_decision_complete(_full_decision(policy_rule_matched=None))
    assert "policy_rule_matched" in str(exc_info.value)


def test_missing_rationale_fails() -> None:
    with pytest.raises(IncompleteRoutingDecisionError) as exc_info:
        assert_decision_complete(_full_decision(rationale=None))
    assert "rationale" in str(exc_info.value)


def test_missing_switchboard_version_fails_for_non_stub() -> None:
    with pytest.raises(IncompleteRoutingDecisionError) as exc_info:
        assert_decision_complete(_full_decision(switchboard_version=None))
    assert "switchboard_version" in str(exc_info.value)


def test_missing_switchboard_version_allowed_for_stub() -> None:
    """Demo / offline stub routing has no switchboard — allow_stub=True skips that field."""
    assert_decision_complete(
        _full_decision(switchboard_version=None),
        allow_stub=True,
    )


def test_error_lists_every_missing_field() -> None:
    with pytest.raises(IncompleteRoutingDecisionError) as exc_info:
        assert_decision_complete(_full_decision(
            policy_rule_matched=None,
            rationale=None,
            switchboard_version=None,
        ))
    msg = str(exc_info.value)
    assert "policy_rule_matched" in msg
    assert "rationale" in msg
    assert "switchboard_version" in msg


# ---------------------------------------------------------------------------
# CxRP round-trip — switchboard_version must survive
# ---------------------------------------------------------------------------


def test_cxrp_round_trip_preserves_switchboard_version() -> None:
    """The wire-level fix from this PR: CxRP envelope round-trips switchboard_version."""
    original = _full_decision()
    cxrp = to_cxrp_lane_decision(original)

    # Serialised as in HttpLaneRoutingClient: model_dump-equivalent dict.
    payload = {
        "decision_id": cxrp.decision_id,
        "proposal_id": cxrp.proposal_id,
        "executor": cxrp.executor.value if cxrp.executor else None,
        "backend": cxrp.backend.value if cxrp.backend else None,
        "rationale": cxrp.rationale,
        "confidence": cxrp.confidence,
        "metadata": dict(cxrp.metadata),
        "alternatives": [
            {"executor": alt.executor.value if alt.executor else None}
            for alt in cxrp.alternatives
        ],
    }
    decoded = from_cxrp_lane_decision(payload)

    assert decoded.switchboard_version == "0.4.2"
    assert decoded.policy_rule_matched == "lint_fix_to_aider_local"
    assert_decision_complete(decoded)


def test_cxrp_round_trip_drops_none_switchboard_version_cleanly() -> None:
    """When the original has no switchboard_version, the round-trip stays None — no synthetic value."""
    original = _full_decision(switchboard_version=None)
    cxrp = to_cxrp_lane_decision(original)
    payload = {
        "decision_id": cxrp.decision_id,
        "proposal_id": cxrp.proposal_id,
        "executor": cxrp.executor.value if cxrp.executor else None,
        "backend": cxrp.backend.value if cxrp.backend else None,
        "rationale": cxrp.rationale,
        "confidence": cxrp.confidence,
        "metadata": dict(cxrp.metadata),
        "alternatives": [],
    }
    decoded = from_cxrp_lane_decision(payload)
    assert decoded.switchboard_version is None
