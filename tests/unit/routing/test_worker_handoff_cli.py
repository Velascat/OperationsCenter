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
