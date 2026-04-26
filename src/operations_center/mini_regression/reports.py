"""Suite report persistence.

write_suite_report() writes a MiniRegressionSuiteReport to a stable path.
load_suite_report() deserializes and validates a previously written report.
_suite_replay_output_dir() returns the per-run directory for slice replay reports.
"""

from __future__ import annotations

import json
from pathlib import Path

from .errors import SuiteReportLoadError, SuiteReportWriteError
from .models import MiniRegressionSuiteReport


def _suite_replay_output_dir(output_dir: Path, suite_run_id: str) -> Path:
    """Return the directory where per-entry slice replay reports are written."""
    return output_dir / "_replay" / suite_run_id


def write_suite_report(report: MiniRegressionSuiteReport, output_dir: Path) -> Path:
    """Write a suite report to output_dir/{suite_id}/{suite_run_id}/suite_report.json.

    Parameters
    ----------
    report:
        The MiniRegressionSuiteReport to persist.
    output_dir:
        Root directory. Sub-paths are created automatically.

    Returns
    -------
    Path
        The path where the report was written.

    Raises
    ------
    SuiteReportWriteError
        On filesystem failure.
    """
    report_dir = output_dir / report.suite_id / report.suite_run_id
    try:
        report_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SuiteReportWriteError(f"Cannot create suite report directory: {exc}") from exc

    report_path = report_dir / "suite_report.json"
    try:
        report_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    except OSError as exc:
        raise SuiteReportWriteError(f"Cannot write suite report: {exc}") from exc

    return report_path


def load_suite_report(path: Path | str) -> MiniRegressionSuiteReport:
    """Load and validate a MiniRegressionSuiteReport from a JSON file.

    Parameters
    ----------
    path:
        Path to the suite report JSON file.

    Returns
    -------
    MiniRegressionSuiteReport

    Raises
    ------
    FileNotFoundError
        If the path does not exist.
    SuiteReportLoadError
        If the file is not valid JSON or fails schema validation.
    """
    report_path = Path(path)
    if not report_path.exists():
        raise FileNotFoundError(f"Suite report not found: {report_path}")

    try:
        raw = report_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SuiteReportLoadError(f"Cannot read suite report: {exc}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SuiteReportLoadError(f"Suite report is not valid JSON: {exc}") from exc

    try:
        return MiniRegressionSuiteReport.model_validate(data)
    except Exception as exc:
        raise SuiteReportLoadError(f"Suite report schema validation failed: {exc}") from exc


__all__ = [
    "_suite_replay_output_dir",
    "load_suite_report",
    "write_suite_report",
]
