# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Suite definition loader.

load_mini_regression_suite() reads a JSON suite definition, validates its
schema, checks for duplicate entry_ids, and returns a validated
MiniRegressionSuiteDefinition.
"""

from __future__ import annotations

import json
from pathlib import Path

from .errors import SuiteDefinitionError
from .models import MiniRegressionSuiteDefinition


def load_mini_regression_suite(path: Path | str) -> MiniRegressionSuiteDefinition:
    """Load and validate a suite definition from a JSON file.

    Parameters
    ----------
    path:
        Path to the suite definition JSON file.

    Returns
    -------
    MiniRegressionSuiteDefinition
        Validated suite definition with entries in order.

    Raises
    ------
    FileNotFoundError
        If the suite file does not exist.
    SuiteDefinitionError
        If the JSON is invalid, schema validation fails, entries have duplicate
        IDs, or any replay_profile value is unknown.
    """
    suite_path = Path(path)
    if not suite_path.exists():
        raise FileNotFoundError(f"Suite definition not found: {suite_path}")

    try:
        raw = suite_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SuiteDefinitionError(f"Cannot read suite definition: {exc}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SuiteDefinitionError(f"Suite definition is not valid JSON: {exc}") from exc

    try:
        suite = MiniRegressionSuiteDefinition.model_validate(data)
    except Exception as exc:
        raise SuiteDefinitionError(f"Suite definition schema validation failed: {exc}") from exc

    return suite


__all__ = ["load_mini_regression_suite"]
