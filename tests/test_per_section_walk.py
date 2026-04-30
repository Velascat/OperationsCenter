# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Per-section-walk helpers — last 2 real phantoms from autonomy_gaps.md."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace


# ── reconcile_stale_running_issues ───────────────────────────────────────────

def _running(name="t", kind="goal", started_minutes_ago=60):
    return {
        "id":         f"id-{name}",
        "name":       name,
        "state":      {"name": "Running"},
        "updated_at": (datetime.now(UTC) - timedelta(minutes=started_minutes_ago)).isoformat(),
        "labels":     [{"name": f"task-kind: {kind}"}],
    }


def test_reconcile_skips_non_running():
    from operations_center.reconcile_running import reconcile_stale_running_issues
    issues = [{
        "id": "1", "name": "t", "state": {"name": "Done"},
        "updated_at": datetime.now(UTC).isoformat(),
        "labels": [{"name": "task-kind: goal"}],
    }]
    assert reconcile_stale_running_issues(issues) == []


def test_reconcile_uses_per_kind_ttl():
    from operations_center.reconcile_running import reconcile_stale_running_issues
    # test default TTL is 45min, goal is 4h
    issues = [
        _running("test_long", kind="test",   started_minutes_ago=60),    # past test TTL
        _running("goal_short", kind="goal",  started_minutes_ago=60),    # within goal TTL
    ]
    out = reconcile_stale_running_issues(issues)
    assert len(out) == 1
    assert out[0].title == "test_long"
    assert out[0].task_kind == "test"


def test_reconcile_custom_ttl_overrides_defaults():
    from operations_center.reconcile_running import reconcile_stale_running_issues
    issues = [_running("g", kind="goal", started_minutes_ago=30)]
    # Tighten goal TTL to 15min — now the 30min task is stale
    out = reconcile_stale_running_issues(issues, ttls={"goal": 15})
    assert len(out) == 1
    assert out[0].ttl_minutes == 15


def test_reconcile_unknown_kind_uses_fallback():
    from operations_center.reconcile_running import reconcile_stale_running_issues
    issue = _running("x", kind="exotic", started_minutes_ago=300)
    out = reconcile_stale_running_issues([issue], fallback_minutes=120)
    assert len(out) == 1
    assert out[0].ttl_minutes == 120


def test_reconcile_handles_missing_timestamp():
    from operations_center.reconcile_running import reconcile_stale_running_issues
    issue = _running("x")
    issue["updated_at"] = ""
    issue["created_at"] = ""
    out = reconcile_stale_running_issues([issue])
    assert out == []  # can't compute age — skip


# ── _check_cross_repo_impact ─────────────────────────────────────────────────

def _repo_cfg(impact_paths=()):
    return SimpleNamespace(impact_report_paths=list(impact_paths))


def test_cross_repo_impact_empty_changed_files():
    from operations_center.cross_repo_impact import _check_cross_repo_impact
    repos = {"A": _repo_cfg(["src/api/"]), "B": _repo_cfg(["src/lib/"])}
    assert _check_cross_repo_impact([], repos=repos) == []


def test_cross_repo_impact_detects_match():
    from operations_center.cross_repo_impact import _check_cross_repo_impact
    repos = {
        "shared":   _repo_cfg(["src/api/"]),
        "consumer": _repo_cfg(["src/api/", "proto/"]),
        "unrelated": _repo_cfg(["docs/"]),
    }
    out = _check_cross_repo_impact(
        ["src/api/v1.py", "src/api/v2.py"],
        repos=repos,
        source_repo_key="shared",   # exclude self
    )
    keys = [c.repo_key for c in out]
    assert keys == ["consumer"]
    assert "src/api/v1.py" in out[0].changed_files


def test_cross_repo_impact_excludes_source():
    from operations_center.cross_repo_impact import _check_cross_repo_impact
    repos = {"A": _repo_cfg(["src/api/"]), "B": _repo_cfg(["src/api/"])}
    out = _check_cross_repo_impact(["src/api/x.py"], repos=repos, source_repo_key="A")
    assert [c.repo_key for c in out] == ["B"]


def test_cross_repo_impact_handles_no_declared_paths():
    from operations_center.cross_repo_impact import _check_cross_repo_impact
    repos = {"A": _repo_cfg([]), "B": _repo_cfg([])}
    assert _check_cross_repo_impact(["src/api/x.py"], repos=repos) == []


def test_cross_repo_impact_prefix_match_not_substring():
    """src/apifoo should NOT match src/api/ (boundary check)."""
    from operations_center.cross_repo_impact import _check_cross_repo_impact
    repos = {"A": _repo_cfg(["src/api/"])}
    out = _check_cross_repo_impact(
        ["src/apifoo/x.py"], repos=repos, source_repo_key="other",
    )
    assert out == []  # no false positive on substring


def test_cross_repo_impact_exact_path_match():
    """Path equal to the prefix (without trailing slash) still counts."""
    from operations_center.cross_repo_impact import _check_cross_repo_impact
    repos = {"A": _repo_cfg(["api"])}
    out = _check_cross_repo_impact(["api"], repos=repos, source_repo_key="other")
    assert len(out) == 1
