# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Hardening arc item 5 — routing rationale completeness smoke check.

A non-stub ``LaneDecision`` should always carry ``policy_rule_matched``
and ``switchboard_version``. ``rationale`` is enforced by current code.
This module exposes a small helper used by tests / operator tooling
to flag partial decisions before they reach ``execution_record.json``.

Stub routing (e.g. the demo entrypoint's offline path) is allowed to
omit ``switchboard_version`` because no real switchboard was involved
— callers opt into that via ``allow_stub=True``.
"""

from __future__ import annotations

from operations_center.contracts.routing import LaneDecision


class IncompleteRoutingDecisionError(ValueError):
    """Raised when a LaneDecision is missing fields an audit consumer needs."""


def assert_decision_complete(decision: LaneDecision, *, allow_stub: bool = False) -> None:
    """Assert ``decision`` carries the routing-provenance fields auditors expect.

    Required fields (always):
      - ``policy_rule_matched``
      - ``rationale``

    Required for non-stub decisions (when ``allow_stub=False``):
      - ``switchboard_version``

    Raises
    ------
    IncompleteRoutingDecisionError
        With a message naming every missing field.
    """
    missing: list[str] = []
    if not decision.policy_rule_matched:
        missing.append("policy_rule_matched")
    if not decision.rationale:
        missing.append("rationale")
    if not allow_stub and not decision.switchboard_version:
        missing.append("switchboard_version")

    if missing:
        raise IncompleteRoutingDecisionError(
            "LaneDecision is missing required routing-provenance fields: "
            + ", ".join(missing)
            + f" (decision_id={decision.decision_id})"
        )
