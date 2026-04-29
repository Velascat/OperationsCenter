# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
"""Regression tests for task parser edge cases (kodo test)."""
import pytest
from operations_center.application.task_parser import TaskParser


def test_goal_text_excludes_execution_yaml():
    """F2: When no ## Goal section and only execution YAML, should raise rather than leak YAML into goal."""
    parser = TaskParser()
    desc = "## Execution\nrepo: myrepo\nmode: goal\n"
    with pytest.raises(ValueError, match="Missing goal text"):
        parser.parse(desc)


def test_goal_with_preamble_excludes_execution():
    """F2: Preamble text before ## Execution should become the goal, without the YAML."""
    parser = TaskParser()
    desc = "Fix the login bug.\n\n## Execution\nrepo: myrepo\nmode: goal\n"
    result = parser.parse(desc)
    assert "Fix the login bug" in result.goal_text
    assert "repo: myrepo" not in result.goal_text
