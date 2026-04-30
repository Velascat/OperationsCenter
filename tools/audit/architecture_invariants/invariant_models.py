# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Finding, AuditReport, Status, and Severity models for OpsCenter architecture invariants.

These are OpsCenter-owned types — not imported from any managed repo.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class Severity(str, Enum):
    FAIL = "fail"
    WARN = "warn"
    INFO = "info"


class Status(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    KNOWN_LEGACY = "known_legacy"


@dataclass
class Finding:
    id: str
    family: str
    severity: Severity
    status: Status
    path: str
    line: int
    message: str
    evidence: str
    suggested_fix: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "family": self.family,
            "severity": self.severity.value,
            "status": self.status.value,
            "path": self.path,
            "line": self.line,
            "message": self.message,
            "evidence": self.evidence,
            "suggested_fix": self.suggested_fix,
        }


@dataclass
class AuditReport:
    repo_root: str
    findings: list[Finding] = field(default_factory=list)

    def overall_status(self) -> str:
        statuses = {f.status for f in self.findings}
        if Status.FAIL in statuses:
            return "fail"
        if Status.WARN in statuses:
            return "warn"
        return "pass"

    def summary_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {"pass": 0, "warn": 0, "fail": 0, "known_legacy": 0}
        for f in self.findings:
            counts[f.status.value] += 1
        return counts

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.overall_status(),
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "repo_root": self.repo_root,
            "summary": self.summary_counts(),
            "findings": [f.to_dict() for f in self.findings],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)


__all__ = ["AuditReport", "Finding", "Severity", "Status"]
