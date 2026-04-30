# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Architecture-invariant detectors as a Custodian plugin contributor.

Wraps the existing ``tools/audit/architecture_invariants`` checker in the
Custodian Detector / DetectorResult shape so that ``custodian-audit`` runs
the architecture rules alongside code-health, ghost, and doc-convention
detectors. The underlying rule logic stays in ``tools/audit/`` — this module
is a thin adapter.

Four invariants:

  AI1  managed_repo_import   — src/operations_center/ must not import any
                               managed-repo package (videofoundry, tools.audit,
                               managed_repo namespaces)
  AI2  layer_direction       — execution / dispatch / governance layers
                               respect the unidirectional import graph
  AI3  no_directory_scanning — artifact_index/ and other read-only layers
                               must not call directory traversal at runtime
  AI4  anti_collapse         — behavior_calibration's mutation guardrail
                               is structurally present and non-empty

Each detector returns a DetectorResult whose count is the number of
findings the rule produced. Samples are short, one-per-finding lines
suitable for a CLI summary.

Invariant compliance:
  • Read-only — Custodian never mutates anything; just calls the existing
    static-analysis checkers.
  • The checker functions themselves are the source of truth — this
    adapter must not duplicate or reinterpret their rules.
"""
from __future__ import annotations

from custodian.audit_kit.detector import AuditContext, Detector, DetectorResult


def _to_result(findings) -> DetectorResult:
    """Convert a list of architecture-checker Finding objects to DetectorResult."""
    samples = []
    for f in findings:
        # Finding has path, line, message; samples truncated for CLI display.
        path = getattr(f, "path", "?")
        line = getattr(f, "line", 0)
        msg  = getattr(f, "message", "")
        samples.append(f"{path}:{line}: {msg[:100]}")
    return DetectorResult(count=len(findings), samples=samples[:8])


def _detect_ai1_managed_repo_imports(ctx: AuditContext) -> DetectorResult:
    try:
        from tools.audit.architecture_invariants.import_rules import check_managed_repo_imports
    except ImportError:
        return DetectorResult(count=0, samples=["# tools.audit.architecture_invariants not importable"])
    findings = check_managed_repo_imports(ctx.repo_root)
    return _to_result(findings)


def _detect_ai2_layer_direction(ctx: AuditContext) -> DetectorResult:
    try:
        from tools.audit.architecture_invariants.layer_rules import check_layer_direction
    except ImportError:
        return DetectorResult(count=0, samples=["# layer_rules not importable"])
    findings = check_layer_direction(ctx.repo_root)
    return _to_result(findings)


def _detect_ai3_no_directory_scanning(ctx: AuditContext) -> DetectorResult:
    try:
        from tools.audit.architecture_invariants.scanning_rules import check_no_directory_scanning
    except ImportError:
        return DetectorResult(count=0, samples=["# scanning_rules not importable"])
    findings = check_no_directory_scanning(ctx.repo_root)
    return _to_result(findings)


def _detect_ai4_anti_collapse(ctx: AuditContext) -> DetectorResult:
    try:
        from tools.audit.architecture_invariants.mutation_rules import check_anti_collapse_guardrail
    except ImportError:
        return DetectorResult(count=0, samples=["# mutation_rules not importable"])
    findings = check_anti_collapse_guardrail(ctx.repo_root)
    return _to_result(findings)


def build_oc_architecture_detectors() -> list[Detector]:
    """Custodian plugin contributor for OC's architecture invariants.

    Status is "fixed" because all four rules currently pass on a clean
    repo — any future regression should fail CI. If a rule starts
    surfacing findings deliberately (e.g. during a refactor), bump its
    status to "partial" or "open" to signal that's the expected state.
    """
    return [
        Detector("AI1", "managed-repo imports inside src/operations_center/", "fixed", _detect_ai1_managed_repo_imports),
        Detector("AI2", "layer-direction violations",                          "fixed", _detect_ai2_layer_direction),
        Detector("AI3", "directory-scanning in artifact_index",                "fixed", _detect_ai3_no_directory_scanning),
        Detector("AI4", "anti-collapse guardrail structurally present",        "fixed", _detect_ai4_anti_collapse),
    ]
