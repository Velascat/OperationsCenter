"""Tests that goal-file mutations during retries are idempotent.

Verifies that validation feedback and scope-constraint violation rewrites
do not accumulate duplicate sections across retries — each rewrite
reconstructs the goal file from scratch.
"""
from __future__ import annotations

from pathlib import Path

from operations_center.adapters.kodo.adapter import KodoAdapter
from operations_center.config.settings import KodoSettings


def _make_adapter() -> KodoAdapter:
    return KodoAdapter(KodoSettings())


class TestGoalFileIdempotency:
    """After two consecutive retry mutations (validation feedback + scope
    constraint violation), the goal file must contain exactly ONE of each
    section — not duplicates."""

    def test_no_duplicate_sections_after_two_rewrites(self, tmp_path: Path) -> None:
        goal_file = tmp_path / "goal.md"
        adapter = _make_adapter()

        assembled_goal_text = "Implement feature X"
        original_constraints = "Must not break CI"

        # --- Simulate first retry: validation feedback ---
        validation_constraints = (
            original_constraints
            + "\n\nValidation Feedback:\npytest failed: 2 errors in test_foo.py"
        )
        adapter.write_goal_file(goal_file, assembled_goal_text, validation_constraints)

        content_after_first = goal_file.read_text()
        assert content_after_first.count("Validation Feedback:") == 1
        assert content_after_first.count("## Goal") == 1

        # --- Simulate second retry: scope constraint violation ---
        # In the real code this is a fresh rewrite, NOT appending to the
        # already-mutated file.
        scope_constraints = (
            original_constraints
            + "\n\nScope Constraint Violation:\n"
            "You modified files outside the allowed scope: `README.md`\n"
            "Allowed paths: src/\n"
            "Revert all changes to out-of-scope files. Keep only changes within the allowed paths."
        )
        adapter.write_goal_file(goal_file, assembled_goal_text, scope_constraints)

        content_after_second = goal_file.read_text()
        assert content_after_second.count("Scope Constraint Violation:") == 1
        assert content_after_second.count("## Goal") == 1
        assert content_after_second.count("## Constraints") == 1
        # The validation feedback from the first retry must NOT survive
        # the second rewrite (each rewrite is from scratch).
        assert "Validation Feedback:" not in content_after_second

    def test_repeated_validation_feedback_no_accumulation(self, tmp_path: Path) -> None:
        """Calling the validation-feedback rewrite twice should not produce
        two Validation Feedback sections."""
        goal_file = tmp_path / "goal.md"
        adapter = _make_adapter()

        assembled_goal_text = "Fix bug Y"
        original_constraints = "Keep backwards compat"

        for attempt in range(3):
            constraints = (
                original_constraints
                + f"\n\nValidation Feedback:\nAttempt {attempt}: test_bar.py FAILED"
            )
            adapter.write_goal_file(goal_file, assembled_goal_text, constraints)

        content = goal_file.read_text()
        assert content.count("Validation Feedback:") == 1
        assert content.count("## Goal") == 1
        # Only the last attempt's text survives
        assert "Attempt 2" in content
        assert "Attempt 0" not in content

    def test_old_append_pattern_would_accumulate(self, tmp_path: Path) -> None:
        """Demonstrate that the OLD append pattern would have caused
        duplicates — this is the regression we're preventing."""
        goal_file = tmp_path / "goal.md"
        goal_file.write_text("## Goal\nDo thing.\n\n## Constraints\nBe careful.\n")

        # OLD pattern: append
        with open(goal_file, "a") as f:
            f.write("\n\n## Validation Feedback\n\nerror 1\n")
        with open(goal_file, "a") as f:
            f.write("\n\n## Scope Constraint Violation\n\nviolation 1\n")
        with open(goal_file, "a") as f:
            f.write("\n\n## Validation Feedback\n\nerror 2\n")

        content = goal_file.read_text()
        # The old approach DOES accumulate — 2 Validation Feedback sections
        assert content.count("## Validation Feedback") == 2
        assert content.count("## Scope Constraint Violation") == 1

        # NEW pattern: idempotent rewrite via write_goal_file
        adapter = _make_adapter()
        adapter.write_goal_file(
            goal_file,
            "Do thing.",
            "Be careful.\n\nValidation Feedback:\nerror 2",
        )
        content = goal_file.read_text()
        assert content.count("Validation Feedback:") == 1
        assert content.count("## Goal") == 1
