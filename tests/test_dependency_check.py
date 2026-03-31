from types import SimpleNamespace

from control_plane.entrypoints.maintenance.dependency_check import (
    DependencyStatus,
    actionable_statuses,
    dependency_task_description,
    normalize_version,
)


def test_normalize_version_extracts_semver() -> None:
    assert normalize_version("codex-cli 0.117.0") == "0.117.0"
    assert normalize_version("v1.2.3") == "v1.2.3"


def test_actionable_statuses_filters_to_items_with_notes() -> None:
    statuses = [
        DependencyStatus("kodo", "Kodo", "cli", "0.4.1", "0.4.1", "0.4.2", True, []),
        DependencyStatus("codex", "Codex", "provider", "0.117.0", "0.117.0", "0.118.0", True, ["Pinned version differs"]),
    ]
    assert [status.key for status in actionable_statuses(statuses)] == ["codex"]


def test_dependency_task_description_uses_default_repo_and_context() -> None:
    settings = SimpleNamespace(
        repos={
            "control-plane": SimpleNamespace(default_branch="main"),
        }
    )
    description = dependency_task_description(
        settings=settings,
        status=DependencyStatus(
            key="codex",
            label="Codex",
            kind="provider",
            installed_version="0.117.0",
            pinned_version="0.117.0",
            upstream_latest="0.118.0",
            healthy=True,
            notes=["Pinned version differs"],
        ),
    )
    assert "repo: control-plane" in description
    assert "base_branch: main" in description
    assert "mode: goal" in description
    assert "dependency: codex" in description
