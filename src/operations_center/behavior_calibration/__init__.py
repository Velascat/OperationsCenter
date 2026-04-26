"""Phase 8 — Behavior Calibration for Managed Repo Audit Artifacts.

Provides advisory analysis over Phase 7 artifact indexes.

Output is always advisory. Calibration reports may suggest future actions
but never apply them automatically.
"""

from .analyzer import analyze_artifacts
from .errors import (
    AnalysisProfileError,
    BehaviorCalibrationError,
    CalibrationInputError,
    ReportWriteError,
)
from .models import (
    AnalysisProfile,
    ArtifactIndexSummary,
    BehaviorCalibrationInput,
    BehaviorCalibrationReport,
    CalibrationFinding,
    CalibrationRecommendation,
    FindingCategory,
    FindingSeverity,
    RecommendationPriority,
)
from .recommendations import produce_recommendations
from .reports import load_calibration_report, write_calibration_report

__all__ = [
    # functions
    "analyze_artifacts",
    "load_calibration_report",
    "produce_recommendations",
    "write_calibration_report",
    # errors
    "AnalysisProfileError",
    "BehaviorCalibrationError",
    "CalibrationInputError",
    "ReportWriteError",
    # models
    "AnalysisProfile",
    "ArtifactIndexSummary",
    "BehaviorCalibrationInput",
    "BehaviorCalibrationReport",
    "CalibrationFinding",
    "CalibrationRecommendation",
    "FindingCategory",
    "FindingSeverity",
    "RecommendationPriority",
]
