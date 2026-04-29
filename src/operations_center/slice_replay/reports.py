# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
"""Slice replay report persistence.

write_replay_report() writes a SliceReplayReport to an OpsCenter-owned path.
load_replay_report() deserializes and validates a previously written report.
"""

from __future__ import annotations

import json
from pathlib import Path

from .errors import ReplayReportLoadError, ReplayReportWriteError
from .models import SliceReplayReport


def write_replay_report(report: SliceReplayReport, output_dir: Path) -> Path:
    """Write a replay report to output_dir/<repo_id>/<fixture_pack_id>/<replay_id>.json.

    Parameters
    ----------
    report:
        The SliceReplayReport to persist.
    output_dir:
        Root directory. The report is placed at
        output_dir/<source_repo_id>/<fixture_pack_id>/<replay_id>.json.

    Returns
    -------
    Path
        The path where the report was written.

    Raises
    ------
    ReplayReportWriteError
        On filesystem failure.
    """
    report_dir = output_dir / report.source_repo_id / report.fixture_pack_id
    try:
        report_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ReplayReportWriteError(f"Cannot create report directory: {exc}") from exc

    report_path = report_dir / f"{report.replay_id}.json"
    try:
        report_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    except OSError as exc:
        raise ReplayReportWriteError(f"Cannot write replay report: {exc}") from exc

    return report_path


def load_replay_report(path: Path | str) -> SliceReplayReport:
    """Load and validate a SliceReplayReport from a JSON file.

    Parameters
    ----------
    path:
        Path to the replay report JSON file.

    Returns
    -------
    SliceReplayReport

    Raises
    ------
    FileNotFoundError
        If the file does not exist.
    ReplayReportLoadError
        If the JSON is invalid or schema validation fails.
    """
    report_path = Path(path)
    if not report_path.exists():
        raise FileNotFoundError(f"Replay report not found: {report_path}")

    try:
        raw = report_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ReplayReportLoadError(f"Cannot read replay report: {exc}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ReplayReportLoadError(f"Replay report is not valid JSON: {exc}") from exc

    try:
        return SliceReplayReport.model_validate(data)
    except Exception as exc:
        raise ReplayReportLoadError(f"Replay report schema validation failed: {exc}") from exc


__all__ = ["load_replay_report", "write_replay_report"]
