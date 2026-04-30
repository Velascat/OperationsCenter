# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Rule: artifact_index/ must not perform directory scanning.

The only permitted path to artifact data is:
  run_status.json → artifact_manifest_path → artifact_manifest.json → artifact_index

No code in artifact_index/ may discover artifacts by scanning the filesystem.
Forbidden call patterns (detected via AST):
  glob.glob / glob.iglob / Path.glob / Path.rglob
  os.scandir / os.walk / os.listdir
"""
from __future__ import annotations

import ast
from pathlib import Path

from tools.audit.architecture_invariants.invariant_models import (
    Finding,
    Severity,
    Status,
)

_FAMILY = "no_scanning"
_counter = 0


def _next_id() -> str:
    global _counter
    _counter += 1
    return f"OC-ARCH-SCAN-{_counter:03d}"


# (object_attr, method) pairs that constitute directory scanning.
# Also catches bare function calls like glob.glob(), os.walk() etc.
_FORBIDDEN_CALLS: set[str] = {
    "glob",       # glob.glob / glob.iglob / Path.glob / Path.rglob as bare names
    "iglob",
    "scandir",
    "listdir",
}

# Attribute access patterns: obj.method where method is forbidden
_FORBIDDEN_ATTRS: set[str] = {
    "glob",
    "rglob",
    "scandir",
    "listdir",
    "walk",       # os.walk
}


def _scan_violations(tree: ast.Module) -> list[tuple[int, str]]:
    """Return (lineno, evidence) for each scanning call found in the AST."""
    violations: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id in _FORBIDDEN_CALLS:
            violations.append((node.lineno, f"{func.id}(...)"))
        elif isinstance(func, ast.Attribute) and func.attr in _FORBIDDEN_ATTRS:
            violations.append((node.lineno, f".{func.attr}(...)"))
    return violations


def check_no_directory_scanning(repo_root: Path) -> list[Finding]:
    """Fail if artifact_index/ contains any directory-scanning call."""
    index_dir = repo_root / "src" / "operations_center" / "artifact_index"
    findings: list[Finding] = []

    for py_file in sorted(index_dir.rglob("*.py")):
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue

        rel = py_file.relative_to(repo_root).as_posix()
        for lineno, evidence in _scan_violations(tree):
            findings.append(Finding(
                id=_next_id(),
                family=_FAMILY,
                severity=Severity.FAIL,
                status=Status.FAIL,
                path=rel,
                line=lineno,
                message="Directory scanning in artifact_index/ violates the manifest-as-source-of-truth invariant",
                evidence=evidence,
                suggested_fix=(
                    "Remove the scanning call. All artifact discovery must go through "
                    "load_artifact_manifest() → build_artifact_index(). "
                    "See Invariant 4 in the lockdown document."
                ),
            ))

    return findings


__all__ = ["check_no_directory_scanning"]
