# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
"""Tests for BacklogPromoterService.

Covers:
- Tasks with tier >= 2 and source:autonomy label in Backlog are promoted.
- Tasks whose current tier < 2 are skipped with reason tier_below_2.
- Tasks without source:autonomy label are ignored entirely.
- Tasks not in Backlog state are ignored entirely.
- Tasks with no source_family in provenance are skipped with reason
  no_source_family_in_provenance.
- family_filter limits promotion to the specified family.
- dry_run=True never calls transition_issue.
- dry_run=False calls transition_issue for each promotable task.
- Plane API errors are captured in result.errors without aborting the run.
- recorded_tier is parsed from the task body when present.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock


from operations_center.proposer.backlog_promoter import (
    BacklogPromoterService,
    _parse_recorded_tier,
    _parse_source_family,
)


# ── helpers ──────────────────────────────────────────────────────────────────


def _issue(
    *,
    task_id: str = "abc-123",
    title: str = "Fix lint",
    state: str = "Backlog",
    labels: list[str] | None = None,
    family: str | None = "lint_fix",
    recorded_tier: int | None = 2,
) -> dict[str, Any]:
    if labels is None:
        labels = ["source: autonomy", f"source-family: {family}"]
    provenance_lines = []
    if family:
        provenance_lines.append(f"source_family: {family}")
    if recorded_tier is not None:
        provenance_lines.append(f"autonomy_tier: {recorded_tier}")
    description = "\n".join(["## Provenance"] + provenance_lines) if provenance_lines else ""
    return {
        "id": task_id,
        "name": title,
        "state": {"name": state},
        "labels": [{"name": n} for n in labels],
        "description": description,
    }


def _tier_map(**overrides: int):
    """Return a get_tier callable using provided overrides, defaulting to 1."""
    def get_tier(family: str) -> int:
        return overrides.get(family, 1)
    return get_tier


def _service(
    issues: list[dict[str, Any]],
    *,
    tiers: dict[str, int] | None = None,
    dry_run: bool = True,
) -> tuple[BacklogPromoterService, MagicMock]:
    client = MagicMock()
    client.list_issues.return_value = issues
    get_tier = _tier_map(**(tiers or {}))
    svc = BacklogPromoterService(plane_client=client, get_tier=get_tier, dry_run=dry_run)
    return svc, client


# ── parsing helpers ───────────────────────────────────────────────────────────


def test_parse_source_family_present() -> None:
    body = "source_family: lint_fix\nconfidence: high"
    assert _parse_source_family(body) == "lint_fix"


def test_parse_source_family_absent() -> None:
    assert _parse_source_family("source: autonomy") is None


def test_parse_recorded_tier_present() -> None:
    body = "autonomy_tier: 2\nexpires_at: 2026-05-01"
    assert _parse_recorded_tier(body) == 2


def test_parse_recorded_tier_absent() -> None:
    assert _parse_recorded_tier("source_family: lint_fix") is None


# ── promotion: happy path ─────────────────────────────────────────────────────


def test_tier2_backlog_autonomy_task_is_promoted() -> None:
    svc, client = _service(
        [_issue(family="lint_fix", recorded_tier=1)],
        tiers={"lint_fix": 2},
        dry_run=False,
    )
    result = svc.promote()
    assert result.promote_count == 1
    assert result.promoted[0].family == "lint_fix"
    assert result.promoted[0].current_tier == 2
    assert result.promoted[0].recorded_tier == 1
    client.transition_issue.assert_called_once_with("abc-123", "Ready for AI")


def test_dry_run_does_not_call_transition() -> None:
    svc, client = _service(
        [_issue(family="lint_fix")],
        tiers={"lint_fix": 2},
        dry_run=True,
    )
    result = svc.promote()
    assert result.dry_run is True
    assert result.promote_count == 1
    client.transition_issue.assert_not_called()


def test_multiple_promotable_tasks_all_promoted() -> None:
    issues = [
        _issue(task_id="t1", family="lint_fix"),
        _issue(task_id="t2", family="type_fix", state="Backlog",
               labels=["source: autonomy"]),
    ]
    svc, client = _service(issues, tiers={"lint_fix": 2, "type_fix": 2}, dry_run=False)
    result = svc.promote()
    assert result.promote_count == 2
    assert client.transition_issue.call_count == 2


# ── tier < 2: skip ────────────────────────────────────────────────────────────


def test_tier1_task_is_skipped() -> None:
    svc, client = _service(
        [_issue(family="type_fix")],
        tiers={"type_fix": 1},
    )
    result = svc.promote()
    assert result.promote_count == 0
    assert len(result.skipped) == 1
    assert result.skipped[0].reason == "tier_below_2"
    assert result.skipped[0].family == "type_fix"
    assert result.skipped[0].current_tier == 1


def test_tier0_task_is_skipped() -> None:
    svc, _ = _service([_issue(family="arch_promotion")], tiers={"arch_promotion": 0})
    result = svc.promote()
    assert result.skipped[0].reason == "tier_below_2"


# ── not in Backlog: ignored ───────────────────────────────────────────────────


def test_ready_for_ai_task_is_ignored() -> None:
    svc, _ = _service(
        [_issue(state="Ready for AI", family="lint_fix")],
        tiers={"lint_fix": 2},
    )
    result = svc.promote()
    assert result.promote_count == 0
    assert len(result.skipped) == 0


def test_done_task_is_ignored() -> None:
    svc, _ = _service([_issue(state="Done", family="lint_fix")], tiers={"lint_fix": 2})
    result = svc.promote()
    assert result.promote_count == 0
    assert len(result.skipped) == 0


# ── no source:autonomy label: ignored ────────────────────────────────────────


def test_task_without_autonomy_label_is_ignored() -> None:
    issue = _issue(family="lint_fix", labels=["task-kind: improve"])
    svc, _ = _service([issue], tiers={"lint_fix": 2})
    result = svc.promote()
    assert result.promote_count == 0
    assert len(result.skipped) == 0  # not skipped, just not considered


# ── no source_family in body: skipped ────────────────────────────────────────


def test_missing_source_family_is_skipped() -> None:
    issue = {
        "id": "x1",
        "name": "Unknown task",
        "state": {"name": "Backlog"},
        "labels": [{"name": "source: autonomy"}],
        "description": "## Provenance\nsource: autonomy",
    }
    svc, _ = _service([issue], tiers={"lint_fix": 2})
    result = svc.promote()
    assert result.promote_count == 0
    assert result.skipped[0].reason == "no_source_family_in_provenance"


# ── family_filter ─────────────────────────────────────────────────────────────


def test_family_filter_only_promotes_matching_family() -> None:
    issues = [
        _issue(task_id="t1", family="lint_fix"),
        _issue(task_id="t2", family="type_fix", labels=["source: autonomy"]),
    ]
    svc, client = _service(issues, tiers={"lint_fix": 2, "type_fix": 2}, dry_run=False)
    result = svc.promote(family_filter="lint_fix")
    assert result.promote_count == 1
    assert result.promoted[0].task_id == "t1"
    client.transition_issue.assert_called_once_with("t1", "Ready for AI")


def test_family_filter_no_match_promotes_nothing() -> None:
    svc, client = _service(
        [_issue(family="lint_fix")],
        tiers={"lint_fix": 2},
        dry_run=False,
    )
    result = svc.promote(family_filter="type_fix")
    assert result.promote_count == 0
    client.transition_issue.assert_not_called()


# ── error handling ────────────────────────────────────────────────────────────


def test_list_issues_failure_captured_in_errors() -> None:
    client = MagicMock()
    client.list_issues.side_effect = RuntimeError("connection refused")
    svc = BacklogPromoterService(
        plane_client=client,
        get_tier=lambda f: 2,
        dry_run=False,
    )
    result = svc.promote()
    assert result.promote_count == 0
    assert len(result.errors) == 1
    assert "connection refused" in result.errors[0]


def test_transition_failure_captured_without_aborting() -> None:
    issues = [
        _issue(task_id="t1", family="lint_fix"),
        _issue(task_id="t2", family="lint_fix"),
    ]
    client = MagicMock()
    client.list_issues.return_value = issues
    client.transition_issue.side_effect = [RuntimeError("timeout"), None]
    svc = BacklogPromoterService(
        plane_client=client,
        get_tier=lambda f: 2,
        dry_run=False,
    )
    result = svc.promote()
    # t1 errored, t2 succeeded
    assert result.promote_count == 1
    assert result.promoted[0].task_id == "t2"
    assert len(result.errors) == 1
    assert "t1" in result.errors[0]
