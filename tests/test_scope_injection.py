from __future__ import annotations

from control_plane.application.service import _build_scope_constraints_section
from control_plane.application.scope_policy import ChangedFilePolicyChecker
from control_plane.domain.models import BoardTask


def _make_task(
    *,
    allowed_paths: list[str] | None = None,
    constraints_text: str | None = None,
) -> BoardTask:
    return BoardTask(
        task_id="TASK-1",
        project_id="proj",
        title="Test task",
        status="Ready for AI",
        repo_key="repo_a",
        base_branch="main",
        execution_mode="goal",
        goal_text="Implement the feature.",
        allowed_paths=allowed_paths or [],
        constraints_text=constraints_text,
    )


def test_scope_section_present_when_allowed_paths_set() -> None:
    task = _make_task(allowed_paths=["src/module/", "tools/scripts/"])
    section = _build_scope_constraints_section(task)

    assert section is not None
    assert "## Scope Constraints" in section
    assert "- src/module/" in section
    assert "- tools/scripts/" in section
    assert "You MUST only modify files within these allowed paths:" in section


def test_scope_section_absent_when_no_allowed_paths() -> None:
    task = _make_task(allowed_paths=[])
    section = _build_scope_constraints_section(task)

    assert section is None


def test_scope_section_includes_avoid_paths_from_constraints() -> None:
    task = _make_task(
        allowed_paths=["src/module/"],
        constraints_text=(
            "- prefer small changes\n"
            "- avoid_paths: deployment/docker.yml, config/settings.py\n"
            "- keep tests passing"
        ),
    )
    section = _build_scope_constraints_section(task)

    assert section is not None
    assert "Do NOT modify these paths (prior scope violations):" in section
    assert "- deployment/docker.yml" in section
    assert "- config/settings.py" in section
    # Also confirm allowed paths are present
    assert "- src/module/" in section


def test_scope_section_omits_avoid_block_when_no_avoid_paths() -> None:
    task = _make_task(
        allowed_paths=["src/module/"],
        constraints_text="- prefer small changes\n- keep tests passing",
    )
    section = _build_scope_constraints_section(task)

    assert section is not None
    assert "## Scope Constraints" in section
    assert "- src/module/" in section
    assert "Do NOT modify" not in section


def test_scope_section_with_none_constraints_text() -> None:
    task = _make_task(allowed_paths=["src/module/"], constraints_text=None)
    section = _build_scope_constraints_section(task)

    assert section is not None
    assert "## Scope Constraints" in section
    assert "- src/module/" in section
    assert "Do NOT modify" not in section


def test_scope_section_with_empty_string_constraints_text() -> None:
    task = _make_task(allowed_paths=["src/module/"], constraints_text="")
    section = _build_scope_constraints_section(task)

    assert section is not None
    assert "## Scope Constraints" in section
    assert "- src/module/" in section
    assert "Do NOT modify" not in section


def test_scope_section_prepended_to_goal_text() -> None:
    task = _make_task(allowed_paths=["src/module/"])
    scope_section = _build_scope_constraints_section(task)

    assert scope_section is not None

    assembled_goal_text = scope_section + task.goal_text

    assert "## Scope Constraints" in assembled_goal_text
    assert assembled_goal_text.index("## Scope Constraints") < assembled_goal_text.index(
        task.goal_text
    )
    assert assembled_goal_text.endswith(task.goal_text)


def test_scope_injection_and_policy_checker_coexist() -> None:
    task = _make_task(allowed_paths=["src/module/"])
    scope_section = _build_scope_constraints_section(task)

    assert scope_section is not None

    goal_text = scope_section + task.goal_text
    checker = ChangedFilePolicyChecker()
    violations = checker.find_violations(
        changed_files=["src/module/foo.py", "deployment/config.yml"],
        allowed_paths=["src/module/"],
    )

    assert "## Scope Constraints" in goal_text
    assert violations == ["deployment/config.yml"]


def test_avoid_paths_with_whitespace_variations() -> None:
    task = _make_task(
        allowed_paths=["src/module/"],
        constraints_text=(
            "- prefer small changes\n"
            "  avoid_paths:  foo.py ,  bar.py  \n"
            "- keep tests passing"
        ),
    )
    section = _build_scope_constraints_section(task)

    assert section is not None
    assert "Do NOT modify these paths (prior scope violations):" in section
    assert "- foo.py" in section
    assert "- bar.py" in section
