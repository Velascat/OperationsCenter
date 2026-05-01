# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Architecture-invariant detectors as a Custodian plugin contributor.

Two invariants requiring custom AST analysis:

  AI3  no_directory_scanning — artifact_index/ must not call directory
                               traversal at runtime (glob, rglob, scandir, etc.)
                               (call-pattern check — no Custodian built-in for this;
                               use semgrep when available)
  AI4  anti_collapse         — behavior_calibration's _FORBIDDEN_MUTATION_FIELDS
                               guardrail is structurally present and non-empty
                               (structural assignment check — custom logic required)

Import-direction rules live in .custodian.yaml:
  AI1 (managed-repo imports) → architecture.invariants[forbidden_import_prefix] (A1)
  AI2 (layer direction)      → architecture.layers                               (S1)
"""
from __future__ import annotations

import ast
from pathlib import Path

from custodian.audit_kit.detector import AuditContext, Detector, DetectorResult, HIGH, MEDIUM


# ── AI3: no directory scanning in artifact_index ──────────────────────────────

_AI3_FORBIDDEN_NAMES: set[str] = {"glob", "iglob", "scandir", "listdir"}
_AI3_FORBIDDEN_ATTRS: set[str] = {"glob", "rglob", "scandir", "listdir", "walk"}


def _detect_ai3_no_directory_scanning(ctx: AuditContext) -> DetectorResult:
    index_dir = ctx.repo_root / "src" / "operations_center" / "artifact_index"
    samples: list[str] = []
    count = 0
    for py_file in sorted(index_dir.rglob("*.py")):
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        rel = py_file.relative_to(ctx.repo_root).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            evidence: str | None = None
            if isinstance(func, ast.Name) and func.id in _AI3_FORBIDDEN_NAMES:
                evidence = f"{func.id}(...)"
            elif isinstance(func, ast.Attribute) and func.attr in _AI3_FORBIDDEN_ATTRS:
                evidence = f".{func.attr}(...)"
            if evidence:
                count += 1
                if len(samples) < 8:
                    samples.append(f"{rel}:{node.lineno}: {evidence}")
    return DetectorResult(count=count, samples=samples)


# ── AI4: anti-collapse guardrail structurally present ─────────────────────────

_AI4_CORE_FORBIDDEN = frozenset({"auto_apply", "execute"})


def _extract_frozenset_strings(node: ast.expr) -> set[str]:
    strings: set[str] = set()
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "frozenset"):
        return strings
    if not node.args:
        return strings
    for elt in getattr(node.args[0], "elts", []):
        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
            strings.add(elt.value)
    return strings


def _detect_ai4_anti_collapse(ctx: AuditContext) -> DetectorResult:
    guardrails_path = (
        ctx.repo_root / "src" / "operations_center" / "behavior_calibration" / "guardrails.py"
    )
    rel = guardrails_path.relative_to(ctx.repo_root).as_posix()

    if not guardrails_path.exists():
        return DetectorResult(count=1, samples=[f"{rel}: guardrails.py missing"])

    try:
        tree = ast.parse(guardrails_path.read_text(encoding="utf-8"))
    except SyntaxError as exc:
        return DetectorResult(count=1, samples=[f"{rel}: syntax error — {exc}"])

    found_fields: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "_FORBIDDEN_MUTATION_FIELDS":
                found_fields = _extract_frozenset_strings(node.value)

    if not found_fields:
        return DetectorResult(
            count=1,
            samples=[f"{rel}: _FORBIDDEN_MUTATION_FIELDS missing or empty"],
        )

    missing_core = _AI4_CORE_FORBIDDEN - found_fields
    if missing_core:
        return DetectorResult(
            count=1,
            samples=[f"{rel}: _FORBIDDEN_MUTATION_FIELDS missing core fields: {sorted(missing_core)}"],
        )

    return DetectorResult(count=0, samples=[])


# ── plugin entry point ────────────────────────────────────────────────────────

def build_oc_architecture_detectors() -> list[Detector]:
    """Custodian plugin contributor for OC's architecture invariants.

    AI1 (managed-repo imports) and AI2 (layer direction) are now enforced
    declaratively via architecture.invariants and architecture.layers in
    .custodian.yaml (A1 and S1 detectors respectively).
    """
    return [
        Detector("AI3", "directory-scanning in artifact_index",         "fixed", _detect_ai3_no_directory_scanning, MEDIUM),
        Detector("AI4", "anti-collapse guardrail structurally present", "fixed", _detect_ai4_anti_collapse,         HIGH),
    ]
