# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Architecture-invariant detectors as a Custodian plugin contributor.

Single remaining custom detector:

  AI4  anti_collapse — behavior_calibration's _FORBIDDEN_MUTATION_FIELDS
                       guardrail is structurally present and non-empty
                       (structural assignment check on a specific file —
                       custom Python policy required).

Boundary refinement (per Custodian Boundary Refinement spec):

  AI1 (managed-repo imports) → architecture.invariants[forbidden_import_prefix] (A1)
  AI2 (layer direction)      → architecture.layers (S1)
  AI3 (no directory scanning) → Semgrep rule at
       .custodian/rules/semgrep/ai3_no_directory_scanning.yaml
  AI4 (anti-collapse)        → custom Python (this file) — structural
                                assignment check requires AST walking.

The legacy Python AI3 detector was removed once Semgrep parity landed.
"""
from __future__ import annotations

import ast

from custodian.audit_kit.detector import AuditContext, Detector, DetectorResult, HIGH


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

    Per the boundary refinement:
      AI1, AI2 → declarative .custodian.yaml (forbidden_import_prefix, layers)
      AI3      → Semgrep rule (.custodian/rules/semgrep/ai3_no_directory_scanning.yaml)
      AI4      → custom Python (this file)
    """
    return [
        Detector("AI4", "anti-collapse guardrail structurally present", "fixed", _detect_ai4_anti_collapse, HIGH),
    ]
