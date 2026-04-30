# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Calibration report serialization and file writing.

Reports are written to OperationsCenter-owned paths. They are derived
from manifests and never modify producer artifacts or managed repo files.

Suggested path layout:
  {output_dir}/{repo_id}/{run_id}/{profile}.json
"""

from __future__ import annotations

import json
from pathlib import Path

from .errors import ReportWriteError
from .models import BehaviorCalibrationReport


def write_calibration_report(
    report: BehaviorCalibrationReport,
    output_dir: Path | str,
) -> Path:
    """Serialize a BehaviorCalibrationReport to JSON at a standard path.

    The file is written to:
      {output_dir}/{repo_id}/{run_id}/{profile}.json

    Parameters
    ----------
    report:
        The calibration report to write.
    output_dir:
        Root directory for calibration reports. Must be an OpsCenter-owned path.
        The subdirectory structure is created automatically.

    Returns
    -------
    Path
        Absolute path to the written report file.

    Raises
    ------
    ReportWriteError
        If the file cannot be written.
    """
    root = Path(output_dir).resolve()
    report_dir = root / report.repo_id / report.run_id
    report_path = report_dir / f"{report.analysis_profile.value}.json"

    try:
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            report.model_dump_json(indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        raise ReportWriteError(
            f"could not write calibration report to {report_path}: {exc}"
        ) from exc

    return report_path


def load_calibration_report(path: Path | str) -> BehaviorCalibrationReport:
    """Load a previously-written calibration report from JSON.

    Raises
    ------
    FileNotFoundError
        The report file does not exist.
    ValueError
        The file is not valid JSON or fails model validation.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"calibration report not found: {p}")

    raw = p.read_text(encoding="utf-8")
    data = json.loads(raw)
    return BehaviorCalibrationReport.model_validate(data)


__all__ = ["load_calibration_report", "write_calibration_report"]
