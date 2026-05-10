# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
import pytest

from operations_center.application.task_parser import TaskParser


def test_parser_extracts_execution_goal_constraints() -> None:
    parser = TaskParser()
    parsed = parser.parse(
        """## Execution
repo: repo_a
base_branch: main
mode: GOAL
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


def test_parser_rejects_missing_repo_with_no_label() -> None:
    parser = TaskParser()

    with pytest.raises(ValueError, match="Missing execution metadata fields"):
        parser.parse(
            """## Execution
base_branch: main
mode: goal

## Goal
Do the thing.
"""
        )


def test_parser_fills_repo_from_label_when_execution_block_absent() -> None:
    parser = TaskParser()

    parsed = parser.parse("Fix the bug.", labels=["repo: repo_a", "task-kind: goal"])

    assert parsed.execution_metadata["repo"] == "repo_a"
    assert parsed.execution_metadata["base_branch"] == ""
    assert parsed.execution_metadata["mode"] == "goal"
    assert parsed.goal_text == "Fix the bug."


def test_parser_base_branch_defaults_to_empty_when_omitted() -> None:
    parser = TaskParser()

    parsed = parser.parse(
        """## Execution
repo: repo_a

## Goal
Do the thing.
"""
    )

    assert parsed.execution_metadata["base_branch"] == ""


def test_parser_rejects_invalid_execution_mode() -> None:
    parser = TaskParser()
    with pytest.raises(ValueError, match="Unsupported execution mode"):
        parser.parse(
            """## Execution
repo: repo_a
base_branch: main
mode: totally_invalid

## Goal
Do the thing.
"""
        )


def test_parser_accepts_mode_test_alias() -> None:
    """'mode: test' is a human-friendly alias for 'test_campaign'."""
    parser = TaskParser()
    parsed = parser.parse(
        """## Execution
repo: repo_a
base_branch: main
mode: test

## Goal
Run verification tests.
"""
    )
    assert parsed.execution_metadata["mode"] == "test_campaign"


def test_parser_accepts_mode_improve_alias() -> None:
    """'mode: improve' is a human-friendly alias for 'improve_campaign'."""
    parser = TaskParser()
    parsed = parser.parse(
        """## Execution
repo: repo_a
base_branch: main
mode: improve

## Goal
Simplify the implementation.
"""
    )
    assert parsed.execution_metadata["mode"] == "improve_campaign"
