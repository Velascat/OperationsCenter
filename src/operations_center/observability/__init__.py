# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""
observability/ — Normalized execution observability and artifact indexing.

Public API:
    # Models
    BackendDetailRef         — reference to raw backend-native output
    ExecutionRecord          — retained normalized run record
    ExecutionTrace           — inspectable report-oriented view of a run
    ArtifactIndex            — classified artifact index
    ChangedFilesEvidence     — changed-file knowledge with honest uncertainty
    ChangedFilesStatus       — KNOWN / NONE / UNKNOWN / NOT_APPLICABLE
    ValidationEvidence       — normalized validation outcome

    # Normalizers
    ArtifactNormalizer       — classifies ExecutionArtifacts into primary/supplemental
    normalize_changed_files  — derives ChangedFilesEvidence from ExecutionResult
    normalize_validation     — derives ValidationEvidence from ValidationSummary

    # Services
    ExecutionRecorder        — record(result) -> ExecutionRecord
    RunReportBuilder         — build_report(record) -> ExecutionTrace
    ExecutionObservabilityService  — observe(result) -> (ExecutionRecord, ExecutionTrace)
"""

from .artifacts import ArtifactIndex, ArtifactNormalizer
from .changed_files import ChangedFilesEvidence, ChangedFilesStatus, normalize_changed_files
from .models import BackendDetailRef, ExecutionRecord
from .recorder import ExecutionRecorder
from .service import ExecutionObservabilityService
from .trace import ExecutionTrace, RunReportBuilder
from .validation import ValidationEvidence, normalize_validation

__all__ = [
    # Models
    "BackendDetailRef",
    "ExecutionRecord",
    "ExecutionTrace",
    "ArtifactIndex",
    "ChangedFilesEvidence",
    "ChangedFilesStatus",
    "ValidationEvidence",
    # Normalizers
    "ArtifactNormalizer",
    "normalize_changed_files",
    "normalize_validation",
    # Services
    "ExecutionRecorder",
    "RunReportBuilder",
    "ExecutionObservabilityService",
]
