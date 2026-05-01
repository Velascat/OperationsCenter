# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Architecture-invariant detectors as a Custodian plugin contributor.

Four invariants enforced via static AST analysis:

  AI1  managed_repo_import   — src/operations_center/ must not import any
                               managed-repo package (videofoundry, tools.audit,
                               managed_repo namespaces)
  AI2  layer_direction       — execution / dispatch / governance layers
                               respect the unidirectional import graph
  AI3  no_directory_scanning — artifact_index/ must not call directory
                               traversal at runtime (glob, rglob, scandir, etc.)
  AI4  anti_collapse         — behavior_calibration's _FORBIDDEN_MUTATION_FIELDS
                               guardrail is structurally present and non-empty

Each detector returns a DetectorResult whose count is the number of
findings the rule produced. Samples are short, one-per-finding lines
suitable for a CLI summary.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from custodian.audit_kit.detector import AuditContext, Detector, DetectorResult, HIGH, MEDIUM


# ── AI1: managed-repo imports ─────────────────────────────────────────────────

_AI1_FORBIDDEN_PREFIXES = ("videofoundry", "tools.audit", "managed_repo")


def _imported_modules(tree: ast.Module) -> list[tuple[int, str]]:
    results: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                results.append((node.lineno, node.module))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                results.append((node.lineno, alias.name))
    return results


def _detect_ai1_managed_repo_imports(ctx: AuditContext) -> DetectorResult:
    src_root = ctx.repo_root / "src" / "operations_center"
    samples: list[str] = []
    count = 0
    for py_file in sorted(src_root.rglob("*.py")):
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        rel = py_file.relative_to(ctx.repo_root).as_posix()
        for lineno, module in _imported_modules(tree):
            for prefix in _AI1_FORBIDDEN_PREFIXES:
                if module == prefix or module.startswith(prefix + "."):
                    count += 1
                    if len(samples) < 8:
                        samples.append(f"{rel}:{lineno}: managed repo import {module!r}")
    return DetectorResult(count=count, samples=samples)


# ── AI2: layer-direction violations ───────────────────────────────────────────

@dataclass
class _LayerRule:
    source_pkg: str
    forbidden_pkg: str
    label: str


_AI2_RULES: list[_LayerRule] = [
    _LayerRule("slice_replay",        "operations_center.audit_dispatch",
               "slice_replay must not import audit_dispatch (replay is dispatch-free)"),
    _LayerRule("mini_regression",     "operations_center.audit_dispatch",
               "mini_regression must not import audit_dispatch (regression does not escalate)"),
    _LayerRule("fixture_harvesting",  "operations_center.audit_dispatch",
               "fixture_harvesting must not import audit_dispatch (fast-feedback chain must be dispatch-free)"),
    _LayerRule("audit_governance",    "operations_center.fixture_harvesting",
               "audit_governance must not import fixture_harvesting (governance only calls dispatch)"),
    _LayerRule("audit_governance",    "operations_center.slice_replay",
               "audit_governance must not import slice_replay"),
    _LayerRule("audit_governance",    "operations_center.mini_regression",
               "audit_governance must not import mini_regression"),
    _LayerRule("mini_regression",     "operations_center.audit_governance",
               "mini_regression must not import audit_governance (no upward escalation)"),
    _LayerRule("slice_replay",        "operations_center.audit_governance",
               "slice_replay must not import audit_governance"),
    _LayerRule("slice_replay",        "operations_center.mini_regression",
               "slice_replay must not import mini_regression (wrong direction in fast-feedback ladder)"),
]


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


def _detect_ai2_layer_direction(ctx: AuditContext) -> DetectorResult:
    src_root = ctx.repo_root / "src" / "operations_center"
    samples: list[str] = []
    count = 0
    for rule in _AI2_RULES:
        pkg_dir = src_root / rule.source_pkg
        if not pkg_dir.is_dir():
            continue
        for py_file in sorted(pkg_dir.rglob("*.py")):
            rel = py_file.relative_to(ctx.repo_root).as_posix()
            for lineno, module in _imports_in_file(py_file):
                if module == rule.forbidden_pkg or module.startswith(rule.forbidden_pkg + "."):
                    count += 1
                    if len(samples) < 8:
                        samples.append(f"{rel}:{lineno}: {rule.label}")
    return DetectorResult(count=count, samples=samples)


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
    """Custodian plugin contributor for OC's architecture invariants."""
    return [
        Detector("AI1", "managed-repo imports inside src/operations_center/", "fixed", _detect_ai1_managed_repo_imports, HIGH),
        Detector("AI2", "layer-direction violations",                          "fixed", _detect_ai2_layer_direction,      HIGH),
        Detector("AI3", "directory-scanning in artifact_index",                "fixed", _detect_ai3_no_directory_scanning, MEDIUM),
        Detector("AI4", "anti-collapse guardrail structurally present",        "fixed", _detect_ai4_anti_collapse,         HIGH),
    ]
