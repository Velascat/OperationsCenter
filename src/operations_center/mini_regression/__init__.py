"""Phase 11: Mini regression suite subsystem.

Public API:
  load_mini_regression_suite()   — load a suite definition from JSON
  run_mini_regression_suite()    — execute a suite and return a report
  write_suite_report()           — persist a suite report
  load_suite_report()            — load a persisted suite report
"""

from .errors import (
    MiniRegressionError,
    SuiteDefinitionError,
    SuiteReportLoadError,
    SuiteReportWriteError,
    SuiteRunError,
)
from .models import (
    MiniRegressionEntryResult,
    MiniRegressionRunRequest,
    MiniRegressionSuiteDefinition,
    MiniRegressionSuiteEntry,
    MiniRegressionSuiteReport,
    MiniRegressionSuiteSummary,
    make_suite_run_id,
)
from .reports import load_suite_report, write_suite_report
from .runner import run_mini_regression_suite
from .suite_loader import load_mini_regression_suite

__all__ = [
    "MiniRegressionEntryResult",
    "MiniRegressionError",
    "MiniRegressionRunRequest",
    "MiniRegressionSuiteDefinition",
    "MiniRegressionSuiteEntry",
    "MiniRegressionSuiteReport",
    "MiniRegressionSuiteSummary",
    "SuiteDefinitionError",
    "SuiteReportLoadError",
    "SuiteReportWriteError",
    "SuiteRunError",
    "load_mini_regression_suite",
    "load_suite_report",
    "make_suite_run_id",
    "run_mini_regression_suite",
    "write_suite_report",
]
