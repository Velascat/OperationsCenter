# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Rule: src/operations_center/ must not import any managed repo code.

Forbidden patterns (line-start anchored by checking the AST module name):
  - videofoundry.*   — the VideoFoundry Python package
  - tools.audit.*    — VideoFoundry's audit tooling living in its tools/ tree
  - managed_repo.*   — any hypothetical managed-repo package

Note: VideoFoundryArtifactKind, VideoFoundryAuditType, VideoFoundrySourceStage are
OpsCenter-owned enums in audit_contracts/vocabulary.py — not imports of the VF package.
The AST check catches actual import statements, not string occurrences of "videofoundry".
"""
from __future__ import annotations

import ast
from pathlib import Path

from tools.audit.architecture_invariants.invariant_models import (
    Finding,
    Severity,
    Status,
)

_FAMILY = "managed_repo_import"
_FORBIDDEN_PREFIXES = ("videofoundry", "tools.audit", "managed_repo")

_counter = 0


def _next_id() -> str:
    global _counter
    _counter += 1
    return f"OC-ARCH-IMPORT-{_counter:03d}"


def _imported_modules(tree: ast.Module) -> list[tuple[int, str]]:
    """Return (lineno, module_name) pairs for every absolute import in the AST.

    Relative imports (from .videofoundry import ...) are excluded — they reference
    OpsCenter's own submodules, not the VideoFoundry Python package.
    """
    results: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:  # level > 0 = relative import
                results.append((node.lineno, node.module))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                results.append((node.lineno, alias.name))
    return results


def check_managed_repo_imports(repo_root: Path) -> list[Finding]:
    """Fail if any src/operations_center/ module imports a managed repo package."""
    src_root = repo_root / "src" / "operations_center"
    findings: list[Finding] = []

    for py_file in sorted(src_root.rglob("*.py")):
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue

        rel = py_file.relative_to(repo_root).as_posix()
        for lineno, module in _imported_modules(tree):
            for prefix in _FORBIDDEN_PREFIXES:
                if module == prefix or module.startswith(prefix + "."):
                    findings.append(Finding(
                        id=_next_id(),
                        family=_FAMILY,
                        severity=Severity.FAIL,
                        status=Status.FAIL,
                        path=rel,
                        line=lineno,
                        message=f"Managed repo import: {module!r}",
                        evidence=f"import {module}",
                        suggested_fix=(
                            "Remove the import. Coordinate via file contracts "
                            "(run_status.json / artifact_manifest.json), not Python imports."
                        ),
                    ))

    return findings


__all__ = ["check_managed_repo_imports"]
