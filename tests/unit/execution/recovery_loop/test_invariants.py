# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Invariant lockdown tests for the recovery loop.

These tests prevent recovery-policy logic from leaking across architectural
boundaries (R1-R12 + I1-I5 from docs/architecture/recovery/recovery_loop_design.md).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]
SRC_ROOT = REPO_ROOT / "src" / "operations_center"
RECOVERY_LOOP_DIR = SRC_ROOT / "execution" / "recovery_loop"
COORDINATOR_FILE = SRC_ROOT / "execution" / "coordinator.py"


def _python_files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*.py") if "__pycache__" not in p.parts]


def _imports_in(path: Path) -> set[str]:
    """All dotted module names imported by ``path``."""
    out: set[str] = set()
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, OSError):
        return out
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name:
                    out.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                out.add(node.module)
    return out


# ---------------------------------------------------------------------------
# I1 / R1 — Naming: no `Custodian` anywhere in the recovery loop or its docs
# ---------------------------------------------------------------------------


class TestNoCustodianNaming:
    def test_no_custodian_class_or_module_name_in_recovery_loop(self):
        bad: list[str] = []
        for py in _python_files(RECOVERY_LOOP_DIR):
            text = py.read_text(encoding="utf-8")
            # Look for Custodian as a class/identifier (case-insensitive,
            # bounded by word boundaries). Permissive on docstring mentions
            # of materialsproject/custodian since the design doc references it.
            if re.search(r"\bcustodian\b", text, re.IGNORECASE):
                # Allow only inside top-of-file docstring mention pointing
                # at the inspiration repo.
                if "materialsproject/custodian" in text:
                    rest = text.replace("materialsproject/custodian", "")
                    if re.search(r"\bcustodian\b", rest, re.IGNORECASE):
                        bad.append(str(py))
                else:
                    bad.append(str(py))
        assert not bad, f"`custodian` naming found in recovery loop files: {bad}"

    def test_no_custodian_module_files(self):
        for py in _python_files(RECOVERY_LOOP_DIR):
            assert "custodian" not in py.name.lower(), f"banned module name: {py}"


# ---------------------------------------------------------------------------
# R2 — No second generic `FailureKind` enum in src/operations_center/
# ---------------------------------------------------------------------------


class TestNoDuplicateFailureKindEnum:
    def test_only_audit_dispatch_has_FailureKind(self):
        offenders: list[str] = []
        for py in _python_files(SRC_ROOT):
            text = py.read_text(encoding="utf-8")
            if re.search(r"^class FailureKind\b", text, re.MULTILINE):
                rel = py.relative_to(SRC_ROOT)
                # Whitelist the existing dispatch enum; everything else fails.
                if rel != Path("audit_dispatch/models.py"):
                    offenders.append(str(rel))
        assert not offenders, (
            "second `FailureKind` enum found outside audit_dispatch/models.py: "
            f"{offenders}. Recovery loop must use ExecutionFailureKind."
        )


# ---------------------------------------------------------------------------
# I3 — Adapters remain bounded executors
# ---------------------------------------------------------------------------


class TestAdaptersDoNotImportRecoveryLoop:
    def test_no_backend_adapter_imports_recovery_loop(self):
        backends_dir = SRC_ROOT / "backends"
        offenders: list[str] = []
        for py in _python_files(backends_dir):
            for module in _imports_in(py):
                if "recovery_loop" in module:
                    offenders.append(f"{py.relative_to(SRC_ROOT)}: imports {module}")
        assert not offenders, f"backend adapters must not import recovery_loop: {offenders}"

    def test_adapters_dir_does_not_import_recovery_loop(self):
        adapters_dir = SRC_ROOT / "adapters"
        # adapters/ exists in this repo; if it ever moves the test should
        # fail loudly rather than silently skip.
        assert adapters_dir.is_dir(), (
            f"expected adapters dir at {adapters_dir}; if relocated, update this test"
        )
        offenders: list[str] = []
        for py in _python_files(adapters_dir):
            for module in _imports_in(py):
                if "recovery_loop" in module:
                    offenders.append(f"{py.relative_to(SRC_ROOT)}: imports {module}")
        assert not offenders, f"adapter implementations must not import recovery_loop: {offenders}"


# ---------------------------------------------------------------------------
# I3b — Recovery loop does not import backend implementation internals
# ---------------------------------------------------------------------------


class TestRecoveryLoopDoesNotImportBackends:
    def test_no_backend_adapter_imports_in_recovery_loop(self):
        offenders: list[str] = []
        for py in _python_files(RECOVERY_LOOP_DIR):
            for module in _imports_in(py):
                # Allow contracts/* (request/result types are part of the
                # public contract layer). Block backend implementations.
                if module.startswith("operations_center.backends"):
                    offenders.append(f"{py.relative_to(RECOVERY_LOOP_DIR)}: imports {module}")
                if module.startswith("operations_center.adapters"):
                    offenders.append(f"{py.relative_to(RECOVERY_LOOP_DIR)}: imports {module}")
        assert not offenders, (
            f"recovery loop must not depend on backend implementations: {offenders}"
        )


# ---------------------------------------------------------------------------
# I1 — ExecutionCoordinator owns the recovery loop integration
# ---------------------------------------------------------------------------


class TestCoordinatorIsOnlyEntryPoint:
    def test_recovery_engine_is_invoked_only_from_coordinator(self):
        # Find every src file that calls RecoveryEngine.evaluate.
        callers: list[str] = []
        for py in _python_files(SRC_ROOT):
            text = py.read_text(encoding="utf-8")
            if "recovery_engine.evaluate" in text or "_recovery_engine.evaluate" in text:
                callers.append(str(py.relative_to(SRC_ROOT)))
        # Only the coordinator should drive the engine.
        allowed = {"execution/coordinator.py"}
        bad = [c for c in callers if c not in allowed]
        assert not bad, (
            "RecoveryEngine.evaluate should only be invoked from "
            f"ExecutionCoordinator; found unexpected callers: {bad}"
        )


# ---------------------------------------------------------------------------
# R5 — Modified requests must be revalidated through PolicyEngine
# ---------------------------------------------------------------------------


class TestModifiedRequestRevalidation:
    def test_coordinator_revalidates_on_request_change(self):
        text = COORDINATOR_FILE.read_text(encoding="utf-8")
        # The integration must contain the revalidation pattern.
        assert "request_changed" in text
        assert "requires_policy_revalidation" in text
        assert "self._policy.evaluate" in text
        # Revalidation must happen INSIDE the recovery loop branch, not
        # only at the top of execute(). Quick check: the second policy
        # evaluate appears inside _run_with_recovery_loop.
        loop_section = text.split("_run_with_recovery_loop", 1)[1]
        assert "self._policy.evaluate" in loop_section, (
            "coordinator's recovery loop must re-evaluate PolicyEngine "
            "when the request changes or requires revalidation"
        )


# ---------------------------------------------------------------------------
# Defensive exception handling
# ---------------------------------------------------------------------------


class TestDefensiveExceptionHandling:
    def test_coordinator_catches_recovery_engine_exceptions(self):
        text = COORDINATOR_FILE.read_text(encoding="utf-8")
        loop = text.split("_run_with_recovery_loop", 1)[1]
        # Both engine.evaluate and policy.evaluate calls inside the loop
        # must be guarded by try/except so recovery-layer exceptions don't
        # propagate to the caller.
        assert "self._recovery_engine.evaluate" in loop
        assert "_recovery_engine_crash_result" in text
        assert "_policy_engine_crash_result" in text


# ---------------------------------------------------------------------------
# No watcher / scheduler / daemon resurrection
# ---------------------------------------------------------------------------


class TestNoWatcherOrSchedulerInRecoveryLoop:
    def test_recovery_loop_has_no_threading_or_async_scheduler(self):
        # Banned tokens are concrete library calls, not generic English words
        # (the design doc legitimately uses "scheduler" prose).
        banned = (
            "threading.Thread(",
            "asyncio.create_task(",
            "asyncio.run(",
            "asyncio.gather(",
            "schedule.every(",
            "daemon=True",
        )
        offenders: list[str] = []
        for py in _python_files(RECOVERY_LOOP_DIR):
            text = py.read_text(encoding="utf-8")
            for token in banned:
                if token in text:
                    offenders.append(f"{py.relative_to(RECOVERY_LOOP_DIR)}: contains {token!r}")
        assert not offenders, f"recovery loop must not introduce schedulers/daemons: {offenders}"
