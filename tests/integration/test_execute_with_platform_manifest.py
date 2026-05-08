# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Integration test: operations-center-execute reads the platform_manifest block.

R4.2 of the manifest-primitive operational rollout. Exercises the full
production entrypoint as a subprocess to confirm the wiring chain:

    operations-center-execute --config <yaml-with-platform_manifest> ...
       └─ load_settings() reads platform_manifest block (R3.1 path resolution)
       └─ build_effective_repo_graph_from_settings() composes the graph
       └─ ExecutionCoordinator(repo_graph=<EffectiveRepoGraph>) constructs
       └─ coordinator.execute() runs to completion (policy-block path used to
          avoid needing real workspace/adapter)

Marks `integration` so it stays out of the default `tests/unit` suite.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]


pytestmark = pytest.mark.integration


_PROJECT_MANIFEST = """\
manifest_kind: project
manifest_version: "1.0.0"
repos:
  vfa_api:
    canonical_name: VFAApi
    visibility: private
edges:
  - {from: VFAApi, to: OperationsCenter, type: dispatches_to}
"""


def _write_config(tmp_path: Path, project_manifest_path: Path) -> Path:
    cfg = tmp_path / "operations_center.yaml"
    cfg.write_text(
        f"""
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
platform_manifest:
  enabled: true
  project_slug: example-project
  project_manifest_path: {project_manifest_path}
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return cfg


def _bundle_high_risk(tmp_path: Path) -> Path:
    """High-risk bundle so PolicyEngine returns a block — exits coordinator
    cleanly without trying to dispatch against a real backend."""
    bundle = tmp_path / "bundle.json"
    bundle.write_text(
        json.dumps(
            {
                "proposal": {
                    "proposal_id": "prop-r4",
                    "task_id": "TASK-R4",
                    "project_id": "cp-test",
                    "task_type": "feature",
                    "execution_mode": "goal",
                    "goal_text": "Integration smoke for platform_manifest wiring",
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
                    "proposal_id": "prop-r4",
                    "selected_lane": "aider_local",
                    "selected_backend": "direct_local",
                    "confidence": 0.9,
                },
            }
        ),
        encoding="utf-8",
    )
    return bundle


def _run_execute(config: Path, bundle: Path, tmp_path: Path) -> subprocess.CompletedProcess:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    cmd = [
        sys.executable,
        "-m",
        "operations_center.entrypoints.execute.main",
        "--config", str(config),
        "--bundle", str(bundle),
        "--workspace-path", str(workspace),
        "--task-branch", "auto/r4-smoke",
        "--no-artifacts",
    ]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    return subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_execute_loads_platform_manifest_block_without_crash(tmp_path: Path) -> None:
    """The full execute entrypoint loads the platform_manifest block and
    constructs the coordinator with a real EffectiveRepoGraph. Policy-block
    path takes over so we don't need a real backend."""
    proj = tmp_path / "project_manifest.yaml"
    proj.write_text(_PROJECT_MANIFEST, encoding="utf-8")
    cfg = _write_config(tmp_path, proj)
    bundle = _bundle_high_risk(tmp_path)

    result = _run_execute(cfg, bundle, tmp_path)
    assert result.returncode == 0, f"stderr:\n{result.stderr}\nstdout:\n{result.stdout}"
    payload = json.loads(result.stdout)
    assert payload["request"]["proposal_id"] == "prop-r4"
    # Policy blocked the high-risk task — proves coordinator constructed
    # cleanly with the graph and reached the policy stage.
    assert payload["policy_decision"]["status"] in {"block", "require_review"}
    assert payload["executed"] is False


def test_execute_with_disabled_platform_manifest_still_works(tmp_path: Path) -> None:
    """enabled=false skips composition; entrypoint must still run cleanly."""
    cfg = tmp_path / "operations_center.yaml"
    cfg.write_text(
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
platform_manifest:
  enabled: false
""".strip()
        + "\n",
        encoding="utf-8",
    )
    bundle = _bundle_high_risk(tmp_path)

    result = _run_execute(cfg, bundle, tmp_path)
    assert result.returncode == 0, f"stderr:\n{result.stderr}\nstdout:\n{result.stdout}"
    payload = json.loads(result.stdout)
    assert payload["executed"] is False


def test_execute_swallows_malformed_project_manifest(tmp_path: Path) -> None:
    """Malformed project manifest → factory returns None with WARNING log;
    OC startup never blocks. Coordinator still constructs with repo_graph=None."""
    proj = tmp_path / "project_manifest.yaml"
    # Project node missing canonical_name → loader error
    proj.write_text(
        'manifest_kind: project\n'
        'manifest_version: "1.0.0"\n'
        'repos:\n'
        '  bad: {visibility: private}\n',
        encoding="utf-8",
    )
    cfg = _write_config(tmp_path, proj)
    bundle = _bundle_high_risk(tmp_path)

    result = _run_execute(cfg, bundle, tmp_path)
    # Entrypoint succeeds despite manifest error — graceful degradation
    assert result.returncode == 0, f"stderr:\n{result.stderr}\nstdout:\n{result.stdout}"
    payload = json.loads(result.stdout)
    assert payload["executed"] is False
