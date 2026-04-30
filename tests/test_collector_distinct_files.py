# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Tests that lint and type collectors compute true distinct_file_count from full output.

The collectors cap top_violations / top_errors at _MAX_ERRORS (20), but distinct_file_count
must reflect the full violation / error set — not just the sampled top-N.
"""
from __future__ import annotations

import json

from operations_center.observer.collectors.lint_signal import LintSignalCollector
from operations_center.observer.collectors.type_check import TypeSignalCollector


# ── ruff JSON parser ────────────────────────────────────────────────────────


def _ruff_item(filename: str, code: str = "E501", message: str = "line too long") -> dict:
    return {
        "filename": filename,
        "code": code,
        "message": message,
        "location": {"row": 1, "column": 0},
    }


def _ruff_json(items: list[dict]) -> str:
    return json.dumps(items)


def test_lint_distinct_file_count_from_full_output() -> None:
    """distinct_file_count counts unique filenames across ALL items, not just top-20."""
    items = [_ruff_item(f"src/f{i}.py") for i in range(25)]
    signal = LintSignalCollector._parse_ruff_output(_ruff_json(items))
    assert signal.violation_count == 25
    assert len(signal.top_violations) == 20
    assert signal.distinct_file_count == 25


def test_lint_distinct_file_count_with_repeated_files() -> None:
    items = [_ruff_item("src/a.py") for _ in range(10)] + [_ruff_item("src/b.py") for _ in range(5)]
    signal = LintSignalCollector._parse_ruff_output(_ruff_json(items))
    assert signal.distinct_file_count == 2


def test_lint_empty_output_is_clean() -> None:
    signal = LintSignalCollector._parse_ruff_output("")
    assert signal.status == "clean"
    assert signal.distinct_file_count == 0


def test_lint_empty_list_is_clean() -> None:
    signal = LintSignalCollector._parse_ruff_output("[]")
    assert signal.status == "clean"
    assert signal.distinct_file_count == 0


# ── ty JSON parser ─────────────────────────────────────────────────────────


def _ty_json(diagnostics: list[dict]) -> str:
    return json.dumps({"diagnostics": diagnostics})


def _ty_diag(file: str, code: str = "attr-defined", message: str = "err") -> dict:
    return {
        "file": file,
        "code": code,
        "message": message,
        "range": {"start": {"line": 1, "character": 0}},
    }


def test_ty_distinct_file_count_from_full_diagnostics() -> None:
    """distinct_file_count counts unique files across ALL diagnostics, not just top-20."""
    # 25 diagnostics across 25 different files — only top 20 end up in top_errors
    diags = [_ty_diag(f"src/file_{i}.py") for i in range(25)]
    signal = TypeSignalCollector._parse_ty_output(_ty_json(diags))
    assert signal.error_count == 25
    assert len(signal.top_errors) == 20
    assert signal.distinct_file_count == 25  # true count, not 20


def test_ty_distinct_file_count_with_repeated_files() -> None:
    """Multiple diagnostics on the same file count as one distinct file."""
    diags = [_ty_diag("src/a.py") for _ in range(10)] + [_ty_diag("src/b.py") for _ in range(5)]
    signal = TypeSignalCollector._parse_ty_output(_ty_json(diags))
    assert signal.distinct_file_count == 2


def test_ty_distinct_file_count_many_files_many_errors() -> None:
    """30 files with 2 errors each → distinct_file_count=30, error_count=60, top_errors=20."""
    diags = [_ty_diag(f"src/f{i}.py") for i in range(30) for _ in range(2)]
    signal = TypeSignalCollector._parse_ty_output(_ty_json(diags))
    assert signal.error_count == 60
    assert len(signal.top_errors) == 20
    assert signal.distinct_file_count == 30


def test_ty_empty_output_gives_zero_distinct() -> None:
    signal = TypeSignalCollector._parse_ty_output("")
    assert signal.distinct_file_count == 0


def test_ty_clean_output_gives_zero_distinct() -> None:
    signal = TypeSignalCollector._parse_ty_output(_ty_json([]))
    assert signal.distinct_file_count == 0


# ── mypy JSON-per-line parser ───────────────────────────────────────────────


def _mypy_line(file: str, severity: str = "error", code: str = "attr-defined") -> str:
    return json.dumps(
        {
            "file": file,
            "line": 1,
            "column": 0,
            "severity": severity,
            "message": "err",
            "error_code": code,
        }
    )


def test_mypy_distinct_file_count_from_full_output() -> None:
    """distinct_file_count counts unique files across all error lines, not just top-20."""
    lines = "\n".join(_mypy_line(f"src/f{i}.py") for i in range(25))
    signal = TypeSignalCollector._parse_mypy_output(lines)
    assert signal.error_count == 25
    assert len(signal.top_errors) == 20
    assert signal.distinct_file_count == 25


def test_mypy_distinct_file_count_with_notes_excluded() -> None:
    """Note severity lines are excluded from both error_count and distinct_file_count."""
    lines = "\n".join(
        [_mypy_line("src/a.py", severity="note")]
        + [_mypy_line("src/b.py") for _ in range(3)]
    )
    signal = TypeSignalCollector._parse_mypy_output(lines)
    assert signal.error_count == 3
    assert signal.distinct_file_count == 1  # only src/b.py


def test_mypy_distinct_file_count_repeated_file() -> None:
    lines = "\n".join(_mypy_line("src/a.py") for _ in range(8))
    signal = TypeSignalCollector._parse_mypy_output(lines)
    assert signal.distinct_file_count == 1


def test_mypy_empty_gives_zero_distinct() -> None:
    signal = TypeSignalCollector._parse_mypy_output("")
    assert signal.distinct_file_count == 0
