# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
"""Errors for the mini regression suite subsystem."""

from __future__ import annotations


class MiniRegressionError(Exception):
    """Base error for the mini regression subsystem."""


class SuiteDefinitionError(MiniRegressionError):
    """Invalid or inconsistent suite definition."""


class SuiteRunError(MiniRegressionError):
    """Infrastructure failure during a suite run."""


class SuiteReportWriteError(MiniRegressionError):
    """Failure writing a suite report to disk."""


class SuiteReportLoadError(MiniRegressionError):
    """Failure loading or validating a suite report."""


__all__ = [
    "MiniRegressionError",
    "SuiteDefinitionError",
    "SuiteRunError",
    "SuiteReportWriteError",
    "SuiteReportLoadError",
]
