# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Wave 4 — multi-step planning helpers."""
from __future__ import annotations


def test_is_multi_step_keyword_match():
    from operations_center.multi_step_planning import _is_multi_step_task
    assert _is_multi_step_task("Refactor the auth module", None)
    assert _is_multi_step_task("Migrate db schema to v2", None)
    assert _is_multi_step_task("Audit dependency licenses", None)
    assert not _is_multi_step_task("Fix typo in readme", None)


def test_is_multi_step_label_match():
    from operations_center.multi_step_planning import _is_multi_step_task
    labels = [{"name": "plan: multi-step"}, {"name": "repo: foo"}]
    assert _is_multi_step_task("Add a single test", labels)


def test_is_multi_step_handles_none_inputs():
    from operations_center.multi_step_planning import _is_multi_step_task
    assert not _is_multi_step_task(None, None)
    assert not _is_multi_step_task("", [])


def test_build_multi_step_plan_three_steps():
    from operations_center.multi_step_planning import build_multi_step_plan
    plan = build_multi_step_plan(
        parent_id="abc-123",
        parent_title="Refactor execution boundary",
        parent_goal="Split the coordinator into prep/execute/finalize phases",
        repo_key="OperationsCenter",
    )
    assert plan.parent_id == "abc-123"
    assert len(plan.steps) == 3
    titles = [s["title"] for s in plan.steps]
    assert "Analyze" in titles[0]
    assert "Implement" in titles[1]
    assert "Verify" in titles[2]


def test_build_multi_step_plan_first_step_is_goal_kind():
    from operations_center.multi_step_planning import build_multi_step_plan
    plan = build_multi_step_plan(
        parent_id="x", parent_title="Migrate", parent_goal="g", repo_key="r"
    )
    assert plan.steps[0]["kind"] == "goal"
    assert plan.steps[1]["kind"] == "goal"
    assert plan.steps[2]["kind"] == "test"


def test_score_proposal_utility_components():
    from operations_center.multi_step_planning import _score_proposal_utility
    high = _score_proposal_utility(
        family_acceptance_rate=1.0, family_recency_hours=0, repo_priority=10,
    )
    low = _score_proposal_utility(
        family_acceptance_rate=0.0, family_recency_hours=200, repo_priority=0,
    )
    assert high > low
    # All-zeros = 0
    assert _score_proposal_utility(family_acceptance_rate=0, family_recency_hours=0, repo_priority=0) == 0.3  # recency=full when hours=0


def test_score_proposal_utility_clamps_inputs():
    from operations_center.multi_step_planning import _score_proposal_utility
    # Out-of-range values clamped — no crashes, no scores >1
    s = _score_proposal_utility(
        family_acceptance_rate=2.0, family_recency_hours=-50, repo_priority=999,
    )
    assert 0.0 <= s <= 1.0


def test_requeue_as_goal_inherits_source_labels():
    from operations_center.multi_step_planning import _requeue_as_goal
    parent = {
        "id": "parent-1",
        "name": "Refactor X",
        "labels": [
            {"name": "source: autonomy"},
            {"name": "source: board_worker"},  # should be filtered
            {"name": "task-kind: goal"},
        ],
    }
    spec = _requeue_as_goal(parent, reason="step_2_failed")
    labels = spec["label_names"]
    assert "source: autonomy" in labels
    assert "source: board_worker" in labels  # caller adds it back
    assert "original-task-id: parent-1" in labels
    assert "handoff-reason: step_2_failed" in labels
    assert "[goal]" in spec["name"]
