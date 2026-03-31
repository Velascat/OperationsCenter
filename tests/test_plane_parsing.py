from control_plane.application.task_parser import TaskParser


def test_parser_extracts_execution_goal_constraints() -> None:
    parser = TaskParser()
    parsed = parser.parse(
        """## Execution
repo: repo_a
base_branch: main
mode: goal
allowed_paths:
  - src/
open_pr: true

## Goal
Do the thing.

## Constraints
- Keep tests green.
"""
    )

    assert parsed.execution_metadata["repo"] == "repo_a"
    assert parsed.execution_metadata["base_branch"] == "main"
    assert parsed.execution_metadata["mode"] == "goal"
    assert parsed.goal_text == "Do the thing."
    assert parsed.constraints_text == "- Keep tests green."


def test_parser_rejects_missing_required_execution_fields() -> None:
    parser = TaskParser()

    try:
        parser.parse(
            """## Execution
repo: repo_a

## Goal
Do the thing.
"""
        )
    except ValueError as exc:
        assert "Missing execution metadata fields" in str(exc)
    else:
        raise AssertionError("Expected ValueError")
