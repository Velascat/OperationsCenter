"""Slice replay runner — run_slice_replay().

Loads a fixture pack, builds checks from the replay profile, executes checks
locally, and returns a SliceReplayReport.

This module never calls Phase 6 dispatch, never harvests new fixtures,
never imports managed repo code, and never modifies fixture pack files.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from operations_center.fixture_harvesting import load_fixture_pack
from operations_center.fixture_harvesting.models import FixtureArtifact, FixturePack

from .checks import CHECK_REGISTRY
from .errors import ReplayInputError
from .models import (
    SliceReplayCheck,
    SliceReplayCheckResult,
    SliceReplayProfile,
    SliceReplayReport,
    SliceReplayRequest,
)
from .profiles import get_artifact_filter, get_check_specs


def _determine_pack_dir(pack_path: Path) -> Path:
    """Return the fixture pack directory given a path to fixture_pack.json or the dir itself."""
    if pack_path.is_dir():
        return pack_path
    return pack_path.parent


def _filter_artifacts(
    pack: FixturePack,
    request: SliceReplayRequest,
) -> list[FixtureArtifact]:
    """Return the subset of FixtureArtifacts relevant to this request."""
    artifacts = list(pack.artifacts)

    if request.selected_fixture_artifact_ids is not None:
        ids = set(request.selected_fixture_artifact_ids)
        missing = ids - {a.source_artifact_id for a in artifacts}
        if missing:
            raise ReplayInputError(
                f"selected_fixture_artifact_ids not found in pack: {sorted(missing)}"
            )
        artifacts = [a for a in artifacts if a.source_artifact_id in ids]

    if request.source_stage is not None:
        artifacts = [a for a in artifacts if a.source_stage == request.source_stage]

    if request.artifact_kind is not None:
        artifacts = [a for a in artifacts if a.artifact_kind == request.artifact_kind]

    return artifacts


def _compute_report_status(results: list[SliceReplayCheckResult]) -> str:
    statuses = {r.status for r in results}
    if not results:
        return "passed"
    if "failed" in statuses:
        return "failed"
    if "error" in statuses:
        return "error"
    if statuses == {"skipped"}:
        return "partial"
    if "skipped" in statuses:
        return "partial"
    return "passed"


def _compute_summary(results: list[SliceReplayCheckResult]) -> str:
    passed = sum(1 for r in results if r.status == "passed")
    failed = sum(1 for r in results if r.status == "failed")
    errored = sum(1 for r in results if r.status == "error")
    skipped = sum(1 for r in results if r.status == "skipped")
    total = len(results)
    return (
        f"{total} checks: {passed} passed, {failed} failed, "
        f"{errored} error, {skipped} skipped"
    )


def run_slice_replay(request: SliceReplayRequest) -> SliceReplayReport:
    """Load a fixture pack, execute profile-driven replay checks, return a report.

    Parameters
    ----------
    request:
        The SliceReplayRequest specifying which pack to replay and which profile to use.

    Returns
    -------
    SliceReplayReport
        Always returned — check failures are recorded as results, not raised as exceptions
        (unless fail_fast=True and a required check fails, in which case execution stops
        early and a partial report is returned with status='failed').

    Raises
    ------
    ReplayInputError
        If the request is invalid (e.g. selected artifact IDs not in pack) or the
        fixture pack cannot be loaded.
    """
    # Load fixture pack via Phase 9 loader — the only way to access fixture data
    pack_path = Path(request.fixture_pack_path)
    try:
        pack = load_fixture_pack(pack_path)
    except FileNotFoundError as exc:
        raise ReplayInputError(f"Fixture pack not found: {exc}") from exc
    except Exception as exc:
        raise ReplayInputError(f"Cannot load fixture pack: {exc}") from exc

    pack_dir = _determine_pack_dir(pack_path)

    # Filter artifacts for this request
    artifacts = _filter_artifacts(pack, request)

    # Build and execute checks from the profile
    specs = get_check_specs(request.replay_profile)
    results: list[SliceReplayCheckResult] = []

    for spec in specs:
        check_fn = CHECK_REGISTRY.get(spec.check_type)
        if check_fn is None:
            # Unknown check type — record as error and continue
            results.append(SliceReplayCheckResult(
                check_id=str(uuid.uuid4()),
                status="error",
                fixture_artifact_ids=[],
                summary=f"Unknown check type: {spec.check_type!r}",
                error="not in CHECK_REGISTRY",
            ))
            continue

        artifact_filter = get_artifact_filter(spec.apply_only_when)

        if not spec.per_artifact:
            # Pack-level check — run once
            check = SliceReplayCheck(
                check_type=spec.check_type,
                fixture_artifact_ids=[],
                description=f"Pack-level: {spec.check_type}",
                required=spec.required,
            )
            result = check_fn(check, pack, pack_dir, None, request)
            results.append(result)
            if request.fail_fast and spec.required and result.status in ("failed", "error"):
                break
        else:
            # Per-artifact check — run for each selected artifact
            for artifact in artifacts:
                # Apply filter if set
                if artifact_filter is not None and not artifact_filter(artifact):
                    check = SliceReplayCheck(
                        check_type=spec.check_type,
                        fixture_artifact_ids=[artifact.source_artifact_id],
                        description=f"{spec.check_type} (filtered out)",
                        required=spec.required,
                    )
                    results.append(SliceReplayCheckResult(
                        check_id=check.check_id,
                        status="skipped",
                        fixture_artifact_ids=[artifact.source_artifact_id],
                        summary=f"Skipped: filter {spec.apply_only_when!r} not matched",
                    ))
                    continue

                check = SliceReplayCheck(
                    check_type=spec.check_type,
                    fixture_artifact_ids=[artifact.source_artifact_id],
                    description=f"{spec.check_type} for {artifact.source_artifact_id}",
                    required=spec.required,
                )
                result = check_fn(check, pack, pack_dir, artifact, request)
                results.append(result)
                if request.fail_fast and spec.required and result.status in ("failed", "error"):
                    break

            # Re-check fail_fast across the outer loop
            if (
                request.fail_fast
                and spec.required
                and any(r.status in ("failed", "error") for r in results)
            ):
                break

    status = _compute_report_status(results)
    summary = _compute_summary(results)

    return SliceReplayReport(
        fixture_pack_id=pack.fixture_pack_id,
        fixture_pack_path=str(pack_path),
        source_repo_id=pack.source_repo_id,
        source_run_id=pack.source_run_id,
        source_audit_type=pack.source_audit_type,
        replay_profile=request.replay_profile,
        status=status,
        summary=summary,
        check_results=results,
        limitations=list(pack.limitations),
        metadata=dict(request.metadata),
    )


__all__ = ["run_slice_replay"]
