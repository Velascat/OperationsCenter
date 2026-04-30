# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Governance report persistence.

write_governance_report() writes an AuditGovernanceReport to a durable path.
load_governance_report() deserializes and validates a previously written report.

Every governance request must produce a report — even denied or deferred ones.

Schema versions
---------------
1.0 — original schema (no governance_status field; defaults to "denied" on load)
1.1 — added governance_status field (populated by runner in all 4 code paths)
"""

from __future__ import annotations

import json
import warnings
from pathlib import Path

from .errors import GovernanceReportError
from .models import AuditGovernanceReport


def _report_dir(output_dir: Path, report: AuditGovernanceReport) -> Path:
    req = report.request
    return output_dir / req.repo_id / req.audit_type / req.request_id


def write_governance_report(report: AuditGovernanceReport, output_dir: Path) -> Path:
    """Write a governance report to output_dir/{repo_id}/{audit_type}/{request_id}/governance_report.json.

    Parameters
    ----------
    report:
        The AuditGovernanceReport to persist.
    output_dir:
        Root output directory.

    Returns
    -------
    Path
        The path where the report was written.

    Raises
    ------
    GovernanceReportError
        On filesystem failure.
    """
    report_dir = _report_dir(output_dir, report)
    try:
        report_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise GovernanceReportError(f"Cannot create governance report directory: {exc}") from exc

    report_path = report_dir / "governance_report.json"
    try:
        report_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    except OSError as exc:
        raise GovernanceReportError(f"Cannot write governance report: {exc}") from exc

    return report_path


def load_governance_report(path: Path | str) -> AuditGovernanceReport:
    """Load and validate an AuditGovernanceReport from a JSON file.

    Parameters
    ----------
    path:
        Path to the governance_report.json file.

    Returns
    -------
    AuditGovernanceReport

    Raises
    ------
    FileNotFoundError
        If the path does not exist.
    GovernanceReportError
        If the file is not valid JSON or fails schema validation.
    """
    report_path = Path(path)
    if not report_path.exists():
        raise FileNotFoundError(f"Governance report not found: {report_path}")

    try:
        raw = report_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise GovernanceReportError(f"Cannot read governance report: {exc}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GovernanceReportError(f"Governance report is not valid JSON: {exc}") from exc

    if data.get("schema_version", "1.0") == "1.0":
        warnings.warn(
            f"Governance report at {report_path} was written with schema_version='1.0' "
            "which predates the governance_status field. The loaded report will default "
            "governance_status='denied' regardless of the actual outcome.",
            UserWarning,
            stacklevel=2,
        )

    try:
        return AuditGovernanceReport.model_validate(data)
    except Exception as exc:
        raise GovernanceReportError(f"Governance report schema validation failed: {exc}") from exc


__all__ = [
    "load_governance_report",
    "write_governance_report",
]
