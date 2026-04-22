from __future__ import annotations

import os
import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]


def test_worker_entrypoint_emits_proposal_and_decision_bundle() -> None:
    cmd = [
        sys.executable,
        "-m",
        "control_plane.entrypoints.worker.main",
        "--goal",
        "Refresh the architecture summary",
        "--task-type",
        "documentation",
        "--repo-key",
        "docs",
        "--clone-url",
        "https://example.invalid/docs.git",
        "--project-id",
        "cp-test",
        "--task-id",
        "TASK-1",
    ]
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [
            str(REPO_ROOT / "src"),
            str(REPO_ROOT.parent / "SwitchBoard" / "src"),
        ]
    )
    result = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["proposal"]["task_id"] == "TASK-1"
    assert payload["decision"]["proposal_id"] == payload["proposal"]["proposal_id"]


def test_domain_no_longer_exports_competing_execution_contracts() -> None:
    domain_init = (REPO_ROOT / "src" / "control_plane" / "domain" / "__init__.py").read_text(encoding="utf-8")
    assert '"ExecutionRequest"' not in domain_init
    assert '"ExecutionResult"' not in domain_init


def test_legacy_execution_service_is_quarantined_outside_application_namespace() -> None:
    assert not (REPO_ROOT / "src" / "control_plane" / "application" / "service.py").exists()
    assert (REPO_ROOT / "src" / "control_plane" / "legacy_execution" / "service.py").exists()


def test_supported_execute_entrypoint_uses_canonical_boundary_not_legacy_runtime() -> None:
    execute_main = (
        REPO_ROOT / "src" / "control_plane" / "entrypoints" / "execute" / "main.py"
    ).read_text(encoding="utf-8")
    assert "ExecutionCoordinator" in execute_main
    assert "legacy_execution" not in execute_main


def test_execute_entrypoint_builds_request_and_enforces_policy_before_execution(tmp_path) -> None:
    config = tmp_path / "control_plane.yaml"
    config.write_text(
        """
plane:
  base_url: http://localhost:8080
  api_token_env: PLANE_API_TOKEN
  workspace_slug: test
  project_id: proj
git:
  provider: github
kodo:
  binary: kodo
repos:
  docs:
    clone_url: https://example.invalid/docs.git
    default_branch: main
        """.strip()
        + "\n",
        encoding="utf-8",
    )

    bundle = tmp_path / "bundle.json"
    bundle.write_text(
        json.dumps(
            {
                "proposal": {
                    "proposal_id": "prop-1",
                    "task_id": "TASK-2",
                    "project_id": "cp-test",
                    "task_type": "feature",
                    "execution_mode": "goal",
                    "goal_text": "Refresh the docs landing page",
                    "constraints_text": None,
                    "target": {
                        "repo_key": "docs",
                        "clone_url": "https://example.invalid/docs.git",
                        "base_branch": "main",
                        "allowed_paths": ["docs/**"],
                    },
                    "priority": "normal",
                    "risk_level": "high",
                    "constraints": {
                        "max_changed_files": None,
                        "timeout_seconds": 300,
                        "allowed_paths": ["docs/**"],
                        "require_clean_validation": True,
                        "skip_baseline_validation": False,
                    },
                    "validation_profile": {"profile_name": "default", "commands": []},
                    "branch_policy": {
                        "branch_prefix": "auto/",
                        "push_on_success": True,
                        "open_pr": False,
                        "allowed_base_branches": [],
                    },
                    "labels": [],
                },
                "decision": {
                    "proposal_id": "prop-1",
                    "selected_lane": "aider_local",
                    "selected_backend": "direct_local",
                    "confidence": 0.9,
                },
            }
        ),
        encoding="utf-8",
    )

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    cmd = [
        str(REPO_ROOT / ".venv" / "bin" / "python"),
        "-m",
        "control_plane.entrypoints.execute.main",
        "--config",
        str(config),
        "--bundle",
        str(bundle),
        "--workspace-path",
        str(workspace),
        "--task-branch",
        "auto/task-2",
    ]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    result = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["request"]["proposal_id"] == "prop-1"
    assert payload["request"]["task_branch"] == "auto/task-2"
    assert payload["policy_decision"]["status"] in {"block", "require_review"}
    assert payload["executed"] is False
    assert payload["result"]["failure_category"] == "policy_blocked"
