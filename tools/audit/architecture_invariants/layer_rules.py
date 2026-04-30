# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Rules enforcing the unidirectional import graph inside src/operations_center/.

Enforced edges (may import →):
  audit_governance      → audit_dispatch
  mini_regression       → slice_replay
  slice_replay          → fixture_harvesting
  fixture_harvesting    → artifact_index
  behavior_calibration  → artifact_index
  artifact_index        → audit_contracts

Forbidden cross-edges (these are the rules we check):
  DISPATCH_ISOLATION  — slice_replay / mini_regression / fixture_harvesting
                        must NOT import audit_dispatch
  GOVERNANCE_ISOLATION — audit_governance must NOT import
                         fixture_harvesting / slice_replay / mini_regression
  REPLAY_STACK        — mini_regression must NOT import audit_governance
                        slice_replay must NOT import mini_regression or audit_governance
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from tools.audit.architecture_invariants.invariant_models import (
    Finding,
    Severity,
    Status,
)

_FAMILY = "layer_direction"
_counters: dict[str, int] = {}


def _next_id(prefix: str) -> str:
    _counters[prefix] = _counters.get(prefix, 0) + 1
    return f"OC-ARCH-LAYER-{prefix}-{_counters[prefix]:03d}"


def _imports_in_file(path: Path) -> list[tuple[int, str]]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return []
    results: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            results.append((node.lineno, node.module))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                results.append((node.lineno, alias.name))
    return results


@dataclass
class _LayerRule:
    """One forbidden edge: files under `source_pkg` must not import `forbidden_pkg`."""
    rule_id_prefix: str
    source_pkg: str        # e.g. "slice_replay"
    forbidden_pkg: str     # e.g. "audit_dispatch"
    message_tmpl: str


_RULES: list[_LayerRule] = [
    _LayerRule(
        "DISPATCH-ISO-SR",
        source_pkg="slice_replay",
        forbidden_pkg="operations_center.audit_dispatch",
        message_tmpl="slice_replay must not import audit_dispatch (Invariant 10: replay is dispatch-free)",
    ),
    _LayerRule(
        "DISPATCH-ISO-MR",
        source_pkg="mini_regression",
        forbidden_pkg="operations_center.audit_dispatch",
        message_tmpl="mini_regression must not import audit_dispatch (Invariant 11: regression does not escalate)",
    ),
    _LayerRule(
        "DISPATCH-ISO-FH",
        source_pkg="fixture_harvesting",
        forbidden_pkg="operations_center.audit_dispatch",
        message_tmpl="fixture_harvesting must not import audit_dispatch (fast-feedback chain must be dispatch-free)",
    ),
    _LayerRule(
        "GOV-ISO-FH",
        source_pkg="audit_governance",
        forbidden_pkg="operations_center.fixture_harvesting",
        message_tmpl="audit_governance must not import fixture_harvesting (governance only calls dispatch)",
    ),
    _LayerRule(
        "GOV-ISO-SR",
        source_pkg="audit_governance",
        forbidden_pkg="operations_center.slice_replay",
        message_tmpl="audit_governance must not import slice_replay",
    ),
    _LayerRule(
        "GOV-ISO-MR",
        source_pkg="audit_governance",
        forbidden_pkg="operations_center.mini_regression",
        message_tmpl="audit_governance must not import mini_regression",
    ),
    _LayerRule(
        "REPLAY-STACK-MR",
        source_pkg="mini_regression",
        forbidden_pkg="operations_center.audit_governance",
        message_tmpl="mini_regression must not import audit_governance (no upward escalation)",
    ),
    _LayerRule(
        "REPLAY-STACK-SR",
        source_pkg="slice_replay",
        forbidden_pkg="operations_center.audit_governance",
        message_tmpl="slice_replay must not import audit_governance",
    ),
    _LayerRule(
        "REPLAY-STACK-SR2",
        source_pkg="slice_replay",
        forbidden_pkg="operations_center.mini_regression",
        message_tmpl="slice_replay must not import mini_regression (wrong direction in fast-feedback ladder)",
    ),
]


def check_layer_direction(repo_root: Path) -> list[Finding]:
    """Fail if any forbidden cross-layer import is found."""
    src_root = repo_root / "src" / "operations_center"
    findings: list[Finding] = []

    for rule in _RULES:
        pkg_dir = src_root / rule.source_pkg
        if not pkg_dir.is_dir():
            continue
        for py_file in sorted(pkg_dir.rglob("*.py")):
            rel = py_file.relative_to(repo_root).as_posix()
            for lineno, module in _imports_in_file(py_file):
                if module == rule.forbidden_pkg or module.startswith(rule.forbidden_pkg + "."):
                    findings.append(Finding(
                        id=_next_id(rule.rule_id_prefix),
                        family=_FAMILY,
                        severity=Severity.FAIL,
                        status=Status.FAIL,
                        path=rel,
                        line=lineno,
                        message=rule.message_tmpl,
                        evidence=f"import {module}",
                        suggested_fix=(
                            f"Remove the import of {rule.forbidden_pkg!r}. "
                            "Check the allowed import graph in docs/architecture/managed_repo_audit_system_final_verification.md"
                        ),
                    ))

    return findings


__all__ = ["check_layer_direction"]
