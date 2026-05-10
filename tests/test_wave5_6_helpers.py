# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Wave 5 (kodo quality + escalation) + Wave 6 (priority/scheduling) helpers."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock



# ── Wave 5 ────────────────────────────────────────────────────────────────────

def test_comment_markdown_basic():
    from operations_center.quality_alerts import _comment_markdown
    out = _comment_markdown(
        headline="Build failed",
        bullets=["lint error", "test timeout"],
    )
    assert "<!-- operations-center:bot -->" in out
    assert "**Build failed**" in out
    assert "- lint error" in out
    assert "- test timeout" in out


def test_comment_markdown_with_code_block():
    from operations_center.quality_alerts import _comment_markdown
    out = _comment_markdown(headline="Trace", code_block="line1\nline2")
    assert "```" in out
    assert "line1" in out


def test_extract_rejection_patterns_top_n():
    from operations_center.quality_alerts import _extract_rejection_patterns
    records = [
        {"reason": "scope too wide"},
        {"reason": "scope too wide"},
        {"reason": "missing tests"},
        {"reason": "Missing Tests"},  # case-insensitive
        {"reason": "duplicate"},
    ]
    out = _extract_rejection_patterns(records)
    assert "scope too wide" in out
    assert "missing tests" in out
    assert len(out) <= 5


def test_extract_rejection_patterns_handles_non_dicts():
    from operations_center.quality_alerts import _extract_rejection_patterns
    assert _extract_rejection_patterns([None, "string", 42]) == []


def test_load_rejection_patterns_no_catalog(tmp_path, monkeypatch):
    from operations_center import quality_alerts
    monkeypatch.chdir(tmp_path)
    assert quality_alerts._load_rejection_patterns_for_proposal() == []


def test_escalate_to_human_writes_jsonl(tmp_path, monkeypatch):
    from operations_center import quality_alerts
    monkeypatch.chdir(tmp_path)
    ok = quality_alerts._escalate_to_human(
        task_id="abc", reason="repeated_failures", severity="error",
    )
    assert ok
    log = tmp_path / "state" / "escalations.jsonl"
    assert log.exists()
    payload = json.loads(log.read_text().strip())
    assert payload["task_id"] == "abc"
    assert payload["severity"] == "error"


def test_process_self_review_lgtm():
    from operations_center.quality_alerts import _process_self_review
    result, summary = _process_self_review({"result": "LGTM", "summary": "looks good"})
    assert result == "LGTM"
    assert summary == "looks good"


def test_process_self_review_fail_closed_on_missing():
    from operations_center.quality_alerts import _process_self_review
    result, summary = _process_self_review(None)
    assert result == "CONCERNS"
    assert "missing" in summary.lower() or "malformed" in summary.lower()


def test_process_self_review_truncates_long_summary():
    from operations_center.quality_alerts import _process_self_review
    long_text = "x" * 1000
    result, summary = _process_self_review({"result": "LGTM", "summary": long_text})
    assert len(summary) <= 400


# ── Wave 6 ────────────────────────────────────────────────────────────────────

def _issue(name="t", state="Backlog", created_days_ago=0, labels=None, priority="none"):
    return {
        "id": f"id-{name}",
        "name": name,
        "state": {"name": state},
        "created_at": (datetime.now(UTC) - timedelta(days=created_days_ago)).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
        "labels": labels or [],
        "priority": priority,
    }


def test_urgency_score_old_backlog_scores_higher():
    from operations_center.priority_scans import issue_urgency_score
    fresh = _issue(created_days_ago=0)
    old = _issue(created_days_ago=20)
    assert issue_urgency_score(old) > issue_urgency_score(fresh)


def test_urgency_score_escalated_label_boosts():
    from operations_center.priority_scans import issue_urgency_score
    plain = _issue(created_days_ago=5)
    escalated = _issue(created_days_ago=5, labels=[{"name": "lifecycle: escalated"}])
    assert issue_urgency_score(escalated) > issue_urgency_score(plain)


def test_urgency_score_retry_count_boosts():
    from operations_center.priority_scans import issue_urgency_score
    plain = _issue(created_days_ago=5)
    flailing = _issue(created_days_ago=5, labels=[{"name": "retry-count: 2"}])
    assert issue_urgency_score(flailing) > issue_urgency_score(plain)


def test_priority_rescore_promotes_old_low():
    from operations_center.priority_scans import handle_priority_rescore_scan
    issues = [_issue(created_days_ago=30, priority="low")]
    out = handle_priority_rescore_scan(issues)
    assert len(out) == 1
    assert out[0].proposed_priority == "high"


def test_priority_rescore_demotes_fresh_urgent():
    from operations_center.priority_scans import handle_priority_rescore_scan
    issues = [_issue(created_days_ago=0, priority="urgent")]
    out = handle_priority_rescore_scan(issues)
    assert len(out) == 1
    assert out[0].proposed_priority == "low"


def test_priority_rescore_skips_non_backlog():
    from operations_center.priority_scans import handle_priority_rescore_scan
    issues = [_issue(state="Done", created_days_ago=30, priority="low")]
    assert handle_priority_rescore_scan(issues) == []


def test_awaiting_input_scan_returns_tasks_with_operator_comments():
    from operations_center.priority_scans import handle_awaiting_input_scan
    issues = [_issue(state="Awaiting Input")]
    plane = MagicMock()
    plane.list_comments.return_value = [
        {"comment_stripped": "operator note here"},
        {"comment_stripped": "<!-- operations-center:bot -->\nbot reply"},  # filtered
    ]
    out = handle_awaiting_input_scan(issues, plane)
    assert len(out) == 1
    assert out[0].new_comment_count == 1


def test_signal_stale_threshold():
    from operations_center.priority_scans import signal_stale
    assert signal_stale(None)               # no data = stale
    assert signal_stale(72)                  # > 48h default
    assert not signal_stale(24)              # < 48h default
    assert signal_stale(24, threshold_hours=12)
