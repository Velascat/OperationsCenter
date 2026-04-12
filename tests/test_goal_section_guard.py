"""Tests for the _assert_goal_sections_unique regression guard."""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from control_plane.application.service import ExecutionService


def _make_service() -> ExecutionService:
    """Build a minimal ExecutionService with a mocked logger."""
    svc = object.__new__(ExecutionService)
    svc.logger = logging.getLogger("test_goal_section_guard")
    return svc


class TestAssertGoalSectionsUnique:
    def test_clean_file_returns_true(self, tmp_path: Path) -> None:
        goal_file = tmp_path / "goal.md"
        goal_file.write_text("## Goal\nImplement feature X\n\n## Constraints\nKeep it simple.\n")

        svc = _make_service()
        assert svc._assert_goal_sections_unique(goal_file) is True

    def test_duplicate_goal_returns_false_and_logs_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        goal_file = tmp_path / "goal.md"
        goal_file.write_text(
            "## Goal\nFirst goal\n\n## Constraints\nConstraint\n\n## Goal\nDuplicate goal\n"
        )

        svc = _make_service()
        with caplog.at_level(logging.WARNING, logger="test_goal_section_guard"):
            result = svc._assert_goal_sections_unique(goal_file)

        assert result is False
        assert any("## Goal" in rec.message and "2" in rec.message for rec in caplog.records)

    def test_duplicate_constraints_returns_false_and_logs_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        goal_file = tmp_path / "goal.md"
        goal_file.write_text(
            "## Goal\nGoal text\n\n## Constraints\nFirst\n\n## Constraints\nSecond\n"
        )

        svc = _make_service()
        with caplog.at_level(logging.WARNING, logger="test_goal_section_guard"):
            result = svc._assert_goal_sections_unique(goal_file)

        assert result is False
        assert any("## Constraints" in rec.message for rec in caplog.records)

    def test_no_constraints_section_returns_true(self, tmp_path: Path) -> None:
        """A goal file with only ## Goal and no ## Constraints is valid."""
        goal_file = tmp_path / "goal.md"
        goal_file.write_text("## Goal\nDo something.\n")

        svc = _make_service()
        assert svc._assert_goal_sections_unique(goal_file) is True

    def test_missing_file_returns_true(self, tmp_path: Path) -> None:
        """Non-existent file should not blow up — just return True."""
        goal_file = tmp_path / "does_not_exist.md"

        svc = _make_service()
        assert svc._assert_goal_sections_unique(goal_file) is True
