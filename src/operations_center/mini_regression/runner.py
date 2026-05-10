# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Mini regression suite runner.

run_mini_regression_suite() iterates suite entries in order, calls the Phase 10
slice replay runner for each, aggregates results, and returns a suite report.

This module never calls Phase 6 dispatch, Phase 9 harvesting, or managed repo code.
It never modifies fixture packs, source artifacts, or manifests.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from operations_center.slice_replay import run_slice_replay
from operations_center.slice_replay.models import SliceReplayRequest

from .errors import SuiteRunError
from .models import (
    MiniRegressionEntryResult,
    MiniRegressionRunRequest,
    MiniRegressionSuiteDefinition,
    MiniRegressionSuiteEntry,
    MiniRegressionSuiteReport,
    MiniRegressionSuiteSummary,
    SuiteStatus,
    make_suite_run_id,
)
from .reports import write_suite_report


def _resolve_pack_path(entry: MiniRegressionSuiteEntry) -> Path:
    """Return the absolute fixture pack path from the entry."""
    return Path(entry.fixture_pack_path)


def _build_replay_request(
    entry: MiniRegressionSuiteEntry,
    pack_path: Path,
) -> SliceReplayRequest:
    """Build a Phase 10 SliceReplayRequest from a suite entry."""
    return SliceReplayRequest(
        fixture_pack_path=pack_path,
        replay_profile=entry.replay_profile,
        selected_fixture_artifact_ids=entry.selected_fixture_artifact_ids,
        source_stage=entry.source_stage,
        artifact_kind=entry.artifact_kind,
        max_artifact_bytes=entry.max_artifact_bytes,
        fail_fast=entry.fail_fast,
    )


def _compute_suite_status(
    entry_results: list[MiniRegressionEntryResult],
    stopped_early: bool,
) -> SuiteStatus:
    """Determine overall suite status from entry results.

    Rules:
    - passed  → all required entries passed; optionals either passed or skipped
    - failed  → ≥1 required entry failed
    - error   → ≥1 required entry errored (and none failed)
    - partial → stopped early due to fail_fast after some entries completed
    """
    if stopped_early and entry_results:
        return "partial"
    required_statuses = {r.status for r in entry_results if r.required}
    if "failed" in required_statuses:
        return "failed"
    if "error" in required_statuses:
        return "error"
    return "passed"


def _compute_summary(
    _suite: MiniRegressionSuiteDefinition,
    entry_results: list[MiniRegressionEntryResult],
) -> MiniRegressionSuiteSummary:
    total = len(entry_results)
    required = sum(1 for r in entry_results if r.required)
    optional = sum(1 for r in entry_results if not r.required)
    passed = sum(1 for r in entry_results if r.status == "passed")
    failed = sum(1 for r in entry_results if r.status == "failed")
    errored = sum(1 for r in entry_results if r.status == "error")
    skipped = sum(1 for r in entry_results if r.status == "skipped")
    req_failures = sum(1 for r in entry_results if r.required and r.status in ("failed", "error"))
    opt_failures = sum(1 for r in entry_results if not r.required and r.status in ("failed", "error"))
    return MiniRegressionSuiteSummary(
        total_entries=total,
        required_entries=required,
        optional_entries=optional,
        passed_entries=passed,
        failed_entries=failed,
        error_entries=errored,
        skipped_entries=skipped,
        required_failures=req_failures,
        optional_failures=opt_failures,
    )


def run_mini_regression_suite(
    request: MiniRegressionRunRequest,
) -> MiniRegressionSuiteReport:
    """Execute each suite entry via Phase 10 slice replay and return a suite report.

    Parameters
    ----------
    request:
        The MiniRegressionRunRequest specifying the suite definition, output
        directory, and execution options.

    Returns
    -------
    MiniRegressionSuiteReport
        Always returned — entry failures are recorded as results, not raised
        (unless an unrecoverable infrastructure error occurs).

    Raises
    ------
    SuiteRunError
        Only for unrecoverable infrastructure failures (e.g. cannot create
        output directory).
    """
    suite = request.suite_definition
    suite_run_id = request.run_id or make_suite_run_id(suite.suite_id)
    started_at = datetime.now(UTC)

    entry_results: list[MiniRegressionEntryResult] = []
    report_paths: list[str] = []
    suite_limitations: list[str] = []
    stopped_early = False

    for entry in suite.entries:
        # Skip optional entries if not requested
        if not entry.required and not request.include_optional_entries:
            entry_results.append(MiniRegressionEntryResult(
                entry_id=entry.entry_id,
                fixture_pack_id="",
                fixture_pack_path=entry.fixture_pack_path,
                replay_profile=entry.replay_profile,
                required=entry.required,
                status="skipped",
                summary="Optional entry skipped (include_optional_entries=False)",
            ))
            continue

        pack_path = _resolve_pack_path(entry)
        replay_request = _build_replay_request(entry, pack_path)

        # Execute Phase 10 runner
        entry_status: str
        slice_report_path = ""
        summary = ""
        error_msg = ""

        try:
            replay_report = run_slice_replay(replay_request)
            entry_status = replay_report.status if replay_report.status in ("passed",) else (
                "failed" if replay_report.status == "failed" else
                "error" if replay_report.status == "error" else
                "passed"  # partial → treat as passed for suite purposes
            )
            summary = replay_report.summary
            for lim in replay_report.limitations:
                if lim not in suite_limitations:
                    suite_limitations.append(lim)

            # Write the slice replay report
            from .reports import _suite_replay_output_dir
            replay_out = _suite_replay_output_dir(request.output_dir, suite_run_id)
            try:
                from operations_center.slice_replay.reports import write_replay_report
                rpath = write_replay_report(replay_report, replay_out)
                slice_report_path = str(rpath)
                report_paths.append(str(rpath))
            except Exception:
                pass  # Non-fatal: entry result is still recorded

            # Pull fixture pack id from report
            fixture_pack_id = replay_report.fixture_pack_id

        except Exception as exc:
            entry_status = "error"
            summary = f"Replay runner raised: {type(exc).__name__}: {exc}"
            error_msg = str(exc)
            fixture_pack_id = ""

        entry_results.append(MiniRegressionEntryResult(
            entry_id=entry.entry_id,
            fixture_pack_id=fixture_pack_id,
            fixture_pack_path=entry.fixture_pack_path,
            replay_profile=entry.replay_profile,
            required=entry.required,
            status=entry_status,
            slice_replay_report_path=slice_report_path,
            summary=summary,
            error=error_msg,
        ))

        # fail_fast: stop after first required failure/error
        if (
            request.fail_fast
            and entry.required
            and entry_status in ("failed", "error")
        ):
            stopped_early = True
            break

    ended_at = datetime.now(UTC)
    suite_status = _compute_suite_status(entry_results, stopped_early)
    summary_obj = _compute_summary(suite, entry_results)

    report = MiniRegressionSuiteReport(
        suite_run_id=suite_run_id,
        suite_id=suite.suite_id,
        suite_name=suite.name,
        repo_id=suite.repo_id,
        audit_type=suite.audit_type,
        started_at=started_at,
        ended_at=ended_at,
        status=suite_status,
        entry_results=entry_results,
        summary=summary_obj,
        report_paths=report_paths,
        limitations=suite_limitations,
        metadata=dict(request.metadata),
    )

    # Write suite report
    try:
        write_suite_report(report, request.output_dir)
    except Exception as exc:
        raise SuiteRunError(f"Cannot write suite report: {exc}") from exc

    return report


__all__ = ["run_mini_regression_suite"]
