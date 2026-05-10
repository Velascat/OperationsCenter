# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Errors for the slice replay subsystem."""

from __future__ import annotations


class SliceReplayError(Exception):
    """Base error for the slice replay subsystem."""


class ReplayInputError(SliceReplayError):
    """Invalid or incomplete replay request."""


class ReplayReportWriteError(SliceReplayError):
    """Failure writing a replay report to disk."""


class ReplayReportLoadError(SliceReplayError):
    """Failure loading or validating a replay report."""


__all__ = [
    "SliceReplayError",
    "ReplayInputError",
    "ReplayReportWriteError",
    "ReplayReportLoadError",
]
