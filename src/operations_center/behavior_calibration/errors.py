# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Errors raised by the behavior calibration module."""

from __future__ import annotations


class BehaviorCalibrationError(Exception):
    """Base class for all calibration errors."""


class CalibrationInputError(BehaviorCalibrationError):
    """The calibration input is invalid or missing required fields."""


class AnalysisProfileError(BehaviorCalibrationError):
    """An unknown or unsupported analysis profile was requested."""


class ReportWriteError(BehaviorCalibrationError):
    """A calibration report could not be written to disk."""


__all__ = [
    "AnalysisProfileError",
    "BehaviorCalibrationError",
    "CalibrationInputError",
    "ReportWriteError",
]
