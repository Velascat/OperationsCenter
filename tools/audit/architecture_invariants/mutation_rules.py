# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Rule: the anti-collapse guardrail must be structurally present and non-empty.

Two checks:
1. _FORBIDDEN_MUTATION_FIELDS must exist as a frozenset assignment in guardrails.py
   and must contain at least the core fields: auto_apply, execute, dispatch.
2. No Pydantic model field defined anywhere in behavior_calibration/ may use a
   name that appears in _FORBIDDEN_MUTATION_FIELDS (models must not accidentally
   declare what they guard against).
"""
from __future__ import annotations

import ast
from pathlib import Path

from tools.audit.architecture_invariants.invariant_models import (
    Finding,
    Severity,
    Status,
)

_FAMILY = "anti_collapse"
_counter = 0

_CORE_FORBIDDEN = frozenset({"auto_apply", "execute"})


def _next_id() -> str:
    global _counter
    _counter += 1
    return f"OC-ARCH-COLLAPSE-{_counter:03d}"


def _extract_frozenset_strings(node: ast.expr) -> set[str]:
    """Pull string constants out of a frozenset({...}) or frozenset([...]) call."""
    strings: set[str] = set()
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "frozenset"):
        return strings
    if not node.args:
        return strings
    inner = node.args[0]
    elts = getattr(inner, "elts", [])
    for elt in elts:
        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
            strings.add(elt.value)
    return strings


def _check_guardrail_present(repo_root: Path) -> list[Finding]:
    guardrails_path = (
        repo_root / "src" / "operations_center" / "behavior_calibration" / "guardrails.py"
    )
    rel = guardrails_path.relative_to(repo_root).as_posix()

    if not guardrails_path.exists():
        return [Finding(
            id=_next_id(),
            family=_FAMILY,
            severity=Severity.FAIL,
            status=Status.FAIL,
            path=rel,
            line=0,
            message="behavior_calibration/guardrails.py is missing",
            evidence="file not found",
            suggested_fix="Restore guardrails.py — it enforces the anti-collapse invariant.",
        )]

    try:
        tree = ast.parse(guardrails_path.read_text(encoding="utf-8"))
    except SyntaxError as exc:
        return [Finding(
            id=_next_id(),
            family=_FAMILY,
            severity=Severity.FAIL,
            status=Status.FAIL,
            path=rel,
            line=0,
            message=f"guardrails.py has a syntax error: {exc}",
            evidence=str(exc),
            suggested_fix="Fix the syntax error in guardrails.py.",
        )]

    # Find _FORBIDDEN_MUTATION_FIELDS assignment
    found_fields: set[str] = set()
    found_line = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "_FORBIDDEN_MUTATION_FIELDS":
                found_fields = _extract_frozenset_strings(node.value)
                found_line = node.lineno

    findings: list[Finding] = []

    if not found_fields:
        findings.append(Finding(
            id=_next_id(),
            family=_FAMILY,
            severity=Severity.FAIL,
            status=Status.FAIL,
            path=rel,
            line=found_line,
            message="_FORBIDDEN_MUTATION_FIELDS is missing or empty in guardrails.py",
            evidence="_FORBIDDEN_MUTATION_FIELDS not found or contains no string literals",
            suggested_fix=(
                "Restore _FORBIDDEN_MUTATION_FIELDS = frozenset({...}) "
                "with at least: auto_apply, execute, dispatch."
            ),
        ))
        return findings

    missing_core = _CORE_FORBIDDEN - found_fields
    if missing_core:
        findings.append(Finding(
            id=_next_id(),
            family=_FAMILY,
            severity=Severity.FAIL,
            status=Status.FAIL,
            path=rel,
            line=found_line,
            message=f"_FORBIDDEN_MUTATION_FIELDS is missing core fields: {sorted(missing_core)}",
            evidence=f"found fields: {sorted(found_fields)}",
            suggested_fix=f"Add {sorted(missing_core)} back to _FORBIDDEN_MUTATION_FIELDS.",
        ))

    return findings


def _check_model_fields_clean(repo_root: Path, forbidden_fields: set[str]) -> list[Finding]:
    """Fail if any Pydantic model in behavior_calibration/ declares a forbidden field name."""
    calibration_dir = repo_root / "src" / "operations_center" / "behavior_calibration"
    findings: list[Finding] = []

    for py_file in sorted(calibration_dir.rglob("*.py")):
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue

        rel = py_file.relative_to(repo_root).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for item in node.body:
                # Pydantic field: field_name: type = Field(...) or field_name: type
                if not isinstance(item, (ast.AnnAssign,)):
                    continue
                target = item.target
                if not isinstance(target, ast.Name):
                    continue
                if target.id in forbidden_fields:
                    findings.append(Finding(
                        id=_next_id(),
                        family=_FAMILY,
                        severity=Severity.FAIL,
                        status=Status.FAIL,
                        path=rel,
                        line=item.lineno,
                        message=(
                            f"Model {node.name!r} declares forbidden field {target.id!r} — "
                            "this field name is in _FORBIDDEN_MUTATION_FIELDS"
                        ),
                        evidence=f"class {node.name}: {target.id}: ...",
                        suggested_fix=(
                            f"Rename or remove the field {target.id!r}. "
                            "Forbidden mutation fields must not appear on calibration models."
                        ),
                    ))

    return findings


def check_anti_collapse_guardrail(repo_root: Path) -> list[Finding]:
    """Run both anti-collapse checks and return all findings."""
    guardrail_findings = _check_guardrail_present(repo_root)

    # Only run field check if we could read the guardrail (avoid cascading noise)
    guardrails_path = (
        repo_root / "src" / "operations_center" / "behavior_calibration" / "guardrails.py"
    )
    forbidden_fields: set[str] = set()
    if guardrails_path.exists():
        try:
            tree = ast.parse(guardrails_path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and target.id == "_FORBIDDEN_MUTATION_FIELDS":
                            forbidden_fields = _extract_frozenset_strings(node.value)
        except SyntaxError:
            pass

    if not forbidden_fields:
        forbidden_fields = _CORE_FORBIDDEN

    field_findings = _check_model_fields_clean(repo_root, forbidden_fields)
    return guardrail_findings + field_findings


__all__ = ["check_anti_collapse_guardrail"]
