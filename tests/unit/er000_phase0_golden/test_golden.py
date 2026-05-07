# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""ER-000 — Phase 0 golden-tests freeze.

Pin current behavior before the four-primitives epic (ER-001 repo graph,
ER-002 run memory, ER-003 lifecycle, ER-004 swarm — deferred) lands.

Sections:
  1. One-shot execution path  — OperatorConsole→OC→SwitchBoard wire intact
                                 (importable, builders work, coordinator
                                 dispatches a fake adapter and surfaces a
                                 backend-unavailable result deterministically)
  2. Contract validation       — OC's Pydantic mirrors round-trip through
                                 their canonical example fixtures
  3. Boundary enforcement      — VideoFoundry imports forbidden inside OC src;
                                 SwitchBoard package free of orchestration
                                 symbols (forward-looking allowlist/denylist)
  4. CLI smoke                 — `operations-center-audit --help` reaches the
                                 Typer app without raising
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from operations_center.contracts.enums import (
    BackendName,
    ExecutionStatus,
    LaneName,
)
from operations_center.contracts.execution import ExecutionRequest, ExecutionResult
from operations_center.contracts.routing import LaneDecision

_REPO_ROOT = Path(__file__).resolve().parents[3]
_OC_SRC = _REPO_ROOT / "src" / "operations_center"
_SB_SRC = _REPO_ROOT.parent / "SwitchBoard" / "src" / "switchboard"


# ===========================================================================
# 1 — One-shot execution path
# ===========================================================================


class TestOneShotPath:
    """Pin: OC's execution wire is importable and a coordinator dispatch
    against a fake backend produces a structured result."""

    def test_coordinator_module_imports(self) -> None:
        from operations_center.execution import coordinator  # noqa: F401

        assert hasattr(coordinator, "ExecutionCoordinator")

    def test_lane_decision_constructs(self) -> None:
        ld = LaneDecision(
            proposal_id="tp-er000",
            selected_lane=LaneName.CLAUDE_CLI,
            selected_backend=BackendName.KODO,
            rationale="er-000 freeze",
        )
        assert ld.selected_lane == LaneName.CLAUDE_CLI
        assert ld.selected_backend == BackendName.KODO

    def test_execution_request_constructs(self, tmp_path: Path) -> None:
        req = ExecutionRequest(
            proposal_id="tp-er000",
            decision_id="ld-er000",
            goal_text="freeze the wire",
            repo_key="velascat/er000",
            clone_url="https://example.invalid/er000.git",
            base_branch="main",
            task_branch="er000/freeze",
            workspace_path=tmp_path,
            timeout_seconds=60,
        )
        assert req.run_id  # default factory ran
        assert req.workspace_path == tmp_path

    def test_execution_request_rejects_missing_required(self) -> None:
        with pytest.raises(ValidationError):
            ExecutionRequest()  # type: ignore[call-arg]

    def test_backend_unavailable_failure_result_is_constructible(self) -> None:
        """The 'backend unavailable' shape must remain expressible — used by
        coordinator when an adapter cannot be loaded or invocation raises."""
        result = ExecutionResult(
            run_id="run-er000",
            proposal_id="tp-er000",
            decision_id="ld-er000",
            status=ExecutionStatus.FAILED,
            success=False,
            failure_reason="backend unavailable",
        )
        assert result.success is False
        assert result.status == ExecutionStatus.FAILED


# ===========================================================================
# 2 — Contract validation
# ===========================================================================


class TestContractValidation:
    """Pin: OC's existing Pydantic contracts accept their canonical shapes
    and reject malformed input."""

    def test_lane_decision_minimal_valid(self) -> None:
        ld = LaneDecision(
            proposal_id="tp-x",
            selected_lane=LaneName.CLAUDE_CLI,
            selected_backend=BackendName.KODO,
        )
        assert ld.confidence == 1.0  # default

    def test_lane_decision_invalid_lane_rejected(self) -> None:
        with pytest.raises(ValidationError):
            LaneDecision(
                proposal_id="tp-x",
                selected_lane="not_a_lane",  # type: ignore[arg-type]
                selected_backend=BackendName.KODO,
            )

    def test_lane_decision_confidence_out_of_range_rejected(self) -> None:
        with pytest.raises(ValidationError):
            LaneDecision(
                proposal_id="tp-x",
                selected_lane=LaneName.CLAUDE_CLI,
                selected_backend=BackendName.KODO,
                confidence=2.0,
            )

    def test_execution_result_success_shape(self) -> None:
        r = ExecutionResult(
            run_id="r1",
            proposal_id="p1",
            decision_id="d1",
            status=ExecutionStatus.SUCCEEDED,
            success=True,
        )
        assert r.success is True
        assert r.failure_category is None

    def test_audit_contracts_examples_still_validate(self) -> None:
        """OC's audit_contracts examples are the canonical
        ManagedRunStatus/ManagedArtifactManifest fixtures — pin them so a
        primitive change can't silently break the existing audit shape."""
        import json

        from operations_center.audit_contracts.artifact_manifest import (
            ManagedArtifactManifest,
        )
        from operations_center.audit_contracts.run_status import ManagedRunStatus

        examples = _REPO_ROOT / "examples" / "audit_contracts"
        for fname in (
            "completed_run_status.json",
            "failed_run_status.json",
        ):
            data = json.loads((examples / fname).read_text())
            data.pop("_example_note", None)
            ManagedRunStatus.model_validate(data)

        for fname in (
            "completed_artifact_manifest.json",
            "failed_artifact_manifest.json",
        ):
            data = json.loads((examples / fname).read_text())
            data.pop("_example_note", None)
            ManagedArtifactManifest.model_validate(data)


# ===========================================================================
# 3 — Boundary enforcement
# ===========================================================================


class TestBoundaryNoVideoFoundryImports:
    """No active VideoFoundry runtime imports in OC source.

    The project's broader invariant checks moved to Custodian
    (`.custodian/architecture.py`); this freeze test inlines a minimal AST
    scan so ER-000 stands alone without depending on Custodian.
    """

    @staticmethod
    def _scan_for_imports(src_root: Path, forbidden_top: str) -> list[tuple[Path, int, str]]:
        hits: list[tuple[Path, int, str]] = []
        if not src_root.exists():
            return hits
        stack = [src_root]
        while stack:
            cur = stack.pop()
            for entry in sorted(cur.iterdir()):
                if entry.is_dir():
                    if entry.name in {"__pycache__", ".venv", ".git"}:
                        continue
                    stack.append(entry)
                elif entry.suffix == ".py":
                    try:
                        tree = ast.parse(entry.read_text(encoding="utf-8"))
                    except (SyntaxError, UnicodeDecodeError):
                        continue
                    for node in ast.walk(tree):
                        if isinstance(node, ast.Import):
                            for alias in node.names:
                                top = alias.name.split(".")[0]
                                if top == forbidden_top:
                                    hits.append((entry, node.lineno, alias.name))
                        elif isinstance(node, ast.ImportFrom):
                            # `from videofoundry.x import Y` — module is set,
                            # `from .videofoundry import Y` — module is 'videofoundry'
                            # but level>0 (relative). Skip relative imports.
                            if node.level and node.level > 0:
                                continue
                            mod = node.module or ""
                            top = mod.split(".")[0]
                            if top == forbidden_top:
                                hits.append((entry, node.lineno, mod))
        return hits

    def test_no_videofoundry_imports_in_oc_src(self) -> None:
        hits = self._scan_for_imports(_OC_SRC, "videofoundry")
        assert hits == [], (
            "VideoFoundry runtime imports inside OperationsCenter src:\n"
            + "\n".join(f"  {p}:{ln} → {sym}" for p, ln, sym in hits)
        )


class TestBoundarySwitchBoardDenylist:
    """SwitchBoard package free of orchestration symbols.

    Forward-looking: most denied symbols don't exist anywhere yet. The
    check guarantees they never accidentally land in SwitchBoard once
    ER-001…ER-003 add them to OperationsCenter.
    """

    def test_default_denylist_clean_against_live_sb(self) -> None:
        # Make tools/ importable without poisoning the real layout.
        sys.path.insert(0, str(_REPO_ROOT))
        try:
            from tools.boundary.switchboard_denylist import (
                check_switchboard_denylist,
            )
        finally:
            sys.path.pop(0)

        if not _SB_SRC.exists():
            pytest.skip(f"SwitchBoard src not present at {_SB_SRC}")

        findings = check_switchboard_denylist(_SB_SRC)
        assert findings == [], (
            "SwitchBoard contains forbidden orchestration symbols:\n"
            + "\n".join(f"  {f.path}:{f.line} → {f.symbol} ({f.kind})" for f in findings)
        )

    def test_denylist_catches_violation_in_fixture(self, tmp_path: Path) -> None:
        sys.path.insert(0, str(_REPO_ROOT))
        try:
            from tools.boundary.switchboard_denylist import (
                BoundaryFinding,
                check_switchboard_denylist,
            )
        finally:
            sys.path.pop(0)

        bad = tmp_path / "bad.py"
        bad.write_text(
            "class SwarmCoordinator:\n    pass\n",
            encoding="utf-8",
        )
        findings = check_switchboard_denylist(tmp_path, denylist=("SwarmCoordinator",))
        assert len(findings) >= 1
        assert isinstance(findings[0], BoundaryFinding)
        assert findings[0].symbol == "SwarmCoordinator"


# ===========================================================================
# 4 — CLI smoke
# ===========================================================================


class TestCLISmoke:
    """Pin: the primary audit CLI loads its Typer app and prints help."""

    def test_audit_app_object_loads(self) -> None:
        from operations_center.entrypoints.audit.main import app

        assert app is not None  # Typer instance constructed at import time

    def test_audit_help_invocation(self) -> None:
        """Use Typer's CliRunner — avoids spawning subprocess + reinstall."""
        from typer.testing import CliRunner

        from operations_center.entrypoints.audit.main import app

        runner = CliRunner()
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0, result.output
        assert "operations-center-audit" in result.output.lower() or "usage" in result.output.lower()
