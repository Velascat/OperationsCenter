# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Individual slice replay check implementations.

Each check is a pure, local, deterministic function over fixture pack data.
Checks never call Phase 6 dispatch, never run audits, never import managed
repo code, and never write to fixture pack files.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from operations_center.fixture_harvesting.models import FixtureArtifact, FixturePack

from .models import SliceReplayCheck, SliceReplayCheckResult, SliceReplayRequest

_TEXT_CONTENT_TYPES = frozenset({
    "application/json",
    "application/x-ndjson",
    "text/plain",
    "text/csv",
    "text/yaml",
    "application/yaml",
})


def _is_json_type(content_type: str) -> bool:
    ct = content_type.split(";")[0].strip().lower()
    return ct in ("application/json", "application/x-ndjson")


def _is_text_type(content_type: str) -> bool:
    ct = content_type.split(";")[0].strip().lower()
    return ct in _TEXT_CONTENT_TYPES or ct.startswith("text/")


def _pass(check: SliceReplayCheck, summary: str, detail: str = "") -> SliceReplayCheckResult:
    return SliceReplayCheckResult(
        check_id=check.check_id,
        status="passed",
        fixture_artifact_ids=check.fixture_artifact_ids,
        summary=summary,
        detail=detail,
    )


def _fail(check: SliceReplayCheck, summary: str, detail: str = "") -> SliceReplayCheckResult:
    return SliceReplayCheckResult(
        check_id=check.check_id,
        status="failed",
        fixture_artifact_ids=check.fixture_artifact_ids,
        summary=summary,
        detail=detail,
    )


def _skip(check: SliceReplayCheck, summary: str) -> SliceReplayCheckResult:
    return SliceReplayCheckResult(
        check_id=check.check_id,
        status="skipped",
        fixture_artifact_ids=check.fixture_artifact_ids,
        summary=summary,
    )


def _error(check: SliceReplayCheck, summary: str, err: str) -> SliceReplayCheckResult:
    return SliceReplayCheckResult(
        check_id=check.check_id,
        status="error",
        fixture_artifact_ids=check.fixture_artifact_ids,
        summary=summary,
        error=err,
    )


# ---------------------------------------------------------------------------
# Check implementations
# ---------------------------------------------------------------------------

def check_fixture_pack_loads(
    check: SliceReplayCheck,
    pack: FixturePack,
    pack_dir: Path,
    artifact: FixtureArtifact | None,
    request: SliceReplayRequest,
) -> SliceReplayCheckResult:
    """Pass if the fixture pack was loaded successfully (it was, or we wouldn't be here)."""
    return _pass(check, f"Fixture pack {pack.fixture_pack_id!r} loaded successfully")


def check_copied_file_exists(
    check: SliceReplayCheck,
    pack: FixturePack,
    pack_dir: Path,
    artifact: FixtureArtifact | None,
    request: SliceReplayRequest,
) -> SliceReplayCheckResult:
    """Pass if copied=True artifact file is present in artifacts/."""
    if artifact is None:
        return _skip(check, "No artifact to check")
    if not artifact.copied:
        return _skip(check, f"Artifact is metadata-only (copied=False): {artifact.copy_error}")
    if not artifact.fixture_relative_path:
        return _fail(check, "copied=True but fixture_relative_path is empty")
    artifact_file = pack_dir / "artifacts" / artifact.fixture_relative_path
    if artifact_file.exists():
        return _pass(check, f"Copied file exists: {artifact.fixture_relative_path}")
    return _fail(
        check,
        f"Copied file missing: {artifact.fixture_relative_path}",
        detail=f"Expected at {artifact_file}",
    )


def check_metadata_only_reason_present(
    check: SliceReplayCheck,
    pack: FixturePack,
    pack_dir: Path,
    artifact: FixtureArtifact | None,
    request: SliceReplayRequest,
) -> SliceReplayCheckResult:
    """Pass if metadata-only artifact has a copy_error explaining why it wasn't copied."""
    if artifact is None:
        return _skip(check, "No artifact to check")
    if artifact.copied:
        return _skip(check, "Artifact was copied — metadata-only check not applicable")
    if artifact.copy_error:
        return _pass(check, f"Metadata-only artifact has reason: {artifact.copy_error!r}")
    if artifact.limitations:
        return _pass(check, f"Metadata-only artifact has limitations: {artifact.limitations}")
    return _fail(
        check,
        "Metadata-only artifact has no copy_error or limitation explaining why it was not copied",
        detail=f"source_artifact_id={artifact.source_artifact_id!r}",
    )


def check_source_manifest_loads(
    check: SliceReplayCheck,
    pack: FixturePack,
    pack_dir: Path,
    artifact: FixtureArtifact | None,
    request: SliceReplayRequest,
) -> SliceReplayCheckResult:
    """Pass if source_manifest.json exists and parses as valid JSON."""
    manifest_file = pack_dir / "source_manifest.json"
    if not manifest_file.exists():
        return _fail(check, "source_manifest.json not found in fixture pack directory")
    try:
        data = json.loads(manifest_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return _fail(check, "source_manifest.json is not valid JSON", detail=str(exc))
    except OSError as exc:
        return _error(check, "Cannot read source_manifest.json", err=str(exc))
    # Minimal shape check
    if "repo_id" not in data and "artifacts" not in data:
        return _fail(
            check,
            "source_manifest.json missing expected fields (repo_id, artifacts)",
            detail=f"Keys present: {list(data.keys())[:10]}",
        )
    return _pass(check, "source_manifest.json is present and valid JSON")


def check_source_index_summary_loads(
    check: SliceReplayCheck,
    pack: FixturePack,
    pack_dir: Path,
    artifact: FixtureArtifact | None,
    request: SliceReplayRequest,
) -> SliceReplayCheckResult:
    """Pass if source_index_summary.json exists and parses as valid JSON."""
    summary_file = pack_dir / "source_index_summary.json"
    if not summary_file.exists():
        return _fail(check, "source_index_summary.json not found in fixture pack directory")
    try:
        data = json.loads(summary_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return _fail(check, "source_index_summary.json is not valid JSON", detail=str(exc))
    except OSError as exc:
        return _error(check, "Cannot read source_index_summary.json", err=str(exc))
    if "total_artifacts" not in data:
        return _fail(
            check,
            "source_index_summary.json missing 'total_artifacts' field",
            detail=f"Keys present: {list(data.keys())[:10]}",
        )
    return _pass(check, f"source_index_summary.json valid — total_artifacts={data['total_artifacts']}")


def check_json_artifact_reads(
    check: SliceReplayCheck,
    pack: FixturePack,
    pack_dir: Path,
    artifact: FixtureArtifact | None,
    request: SliceReplayRequest,
) -> SliceReplayCheckResult:
    """Pass if copied JSON artifact is readable and valid JSON within size limit."""
    if artifact is None:
        return _skip(check, "No artifact to check")
    if not artifact.copied or not artifact.fixture_relative_path:
        return _skip(check, "Artifact not copied — skipping content check")
    if not _is_json_type(artifact.content_type):
        return _skip(check, f"Not a JSON artifact: {artifact.content_type!r}")

    artifact_file = pack_dir / "artifacts" / artifact.fixture_relative_path
    if not artifact_file.exists():
        return _fail(check, f"Artifact file not found: {artifact.fixture_relative_path}")

    try:
        raw = artifact_file.read_bytes()
    except OSError as exc:
        return _error(check, "Cannot read artifact file", err=str(exc))

    if len(raw) > request.max_artifact_bytes:
        raw = raw[: request.max_artifact_bytes]

    try:
        json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        return _fail(
            check,
            "Artifact is not valid JSON (may be truncated by max_artifact_bytes)",
            detail=str(exc),
        )
    return _pass(check, f"JSON artifact readable: {artifact.fixture_relative_path}")


def check_text_artifact_reads(
    check: SliceReplayCheck,
    pack: FixturePack,
    pack_dir: Path,
    artifact: FixtureArtifact | None,
    request: SliceReplayRequest,
) -> SliceReplayCheckResult:
    """Pass if copied text artifact is readable as UTF-8 within size limit."""
    if artifact is None:
        return _skip(check, "No artifact to check")
    if not artifact.copied or not artifact.fixture_relative_path:
        return _skip(check, "Artifact not copied — skipping content check")
    if not _is_text_type(artifact.content_type):
        return _skip(check, f"Not a text artifact: {artifact.content_type!r}")

    artifact_file = pack_dir / "artifacts" / artifact.fixture_relative_path
    if not artifact_file.exists():
        return _fail(check, f"Artifact file not found: {artifact.fixture_relative_path}")

    try:
        raw = artifact_file.read_bytes()
    except OSError as exc:
        return _error(check, "Cannot read artifact file", err=str(exc))

    if len(raw) > request.max_artifact_bytes:
        raw = raw[: request.max_artifact_bytes]

    try:
        raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return _fail(
            check,
            "Artifact is not valid UTF-8 text",
            detail=str(exc),
        )
    return _pass(check, f"Text artifact readable: {artifact.fixture_relative_path}")


def check_failure_limitation_present(
    check: SliceReplayCheck,
    pack: FixturePack,
    pack_dir: Path,
    artifact: FixtureArtifact | None,
    request: SliceReplayRequest,
) -> SliceReplayCheckResult:
    """Pass if the fixture pack or artifact carries failure/partial limitations."""
    if artifact is not None:
        failure_lims = {"partial_run", "missing_downstream_artifacts", "failed_run"}
        artifact_lims = set(artifact.limitations)
        if artifact_lims & failure_lims:
            return _pass(
                check,
                f"Artifact carries failure limitations: {sorted(artifact_lims & failure_lims)}",
            )
        if artifact.copy_error and artifact.status if hasattr(artifact, "status") else False:
            return _pass(check, f"Artifact has copy_error: {artifact.copy_error!r}")
        if not artifact.copied:
            return _pass(check, f"Artifact not copied (metadata-only): {artifact.copy_error!r}")

    # Check pack-level limitations
    pack_failure_lims = {"partial_run", "missing_downstream_artifacts", "failed_run"}
    pack_lims = set(pack.limitations)
    if pack_lims & pack_failure_lims:
        return _pass(
            check,
            f"Fixture pack carries failure limitations: {sorted(pack_lims & pack_failure_lims)}",
        )

    return _fail(
        check,
        "No failure or partial limitations found in fixture pack or artifact",
        detail="Expected partial_run, failed_run, or missing_downstream_artifacts",
    )


def check_checksum_matches_if_available(
    check: SliceReplayCheck,
    pack: FixturePack,
    pack_dir: Path,
    artifact: FixtureArtifact | None,
    request: SliceReplayRequest,
) -> SliceReplayCheckResult:
    """Pass if artifact checksum matches the recorded value (skipped if none recorded)."""
    if artifact is None:
        return _skip(check, "No artifact to check")
    if not artifact.copied or not artifact.fixture_relative_path:
        return _skip(check, "Artifact not copied — skipping checksum check")
    if not artifact.checksum:
        return _skip(check, "No checksum recorded — skipping verification")

    artifact_file = pack_dir / "artifacts" / artifact.fixture_relative_path
    if not artifact_file.exists():
        return _fail(check, f"Artifact file not found: {artifact.fixture_relative_path}")

    try:
        h = hashlib.sha256()
        with artifact_file.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        actual = f"sha256:{h.hexdigest()}"
    except OSError as exc:
        return _error(check, "Cannot read artifact file for checksum", err=str(exc))

    if actual == artifact.checksum:
        return _pass(check, f"Checksum matches: {artifact.checksum}")
    return _fail(
        check,
        "Checksum mismatch",
        detail=f"expected={artifact.checksum!r} actual={actual!r}",
    )


def check_artifact_kind_matches(
    check: SliceReplayCheck,
    pack: FixturePack,
    pack_dir: Path,
    artifact: FixtureArtifact | None,
    request: SliceReplayRequest,
) -> SliceReplayCheckResult:
    """Pass if artifact kind matches the filter in the request (if set)."""
    if artifact is None:
        return _skip(check, "No artifact to check")
    if not request.artifact_kind:
        return _skip(check, "No artifact_kind filter in request")
    if artifact.artifact_kind == request.artifact_kind:
        return _pass(check, f"Artifact kind matches: {artifact.artifact_kind!r}")
    return _fail(
        check,
        f"Artifact kind mismatch: expected {request.artifact_kind!r}, got {artifact.artifact_kind!r}",
    )


def check_source_stage_matches(
    check: SliceReplayCheck,
    pack: FixturePack,
    pack_dir: Path,
    artifact: FixtureArtifact | None,
    request: SliceReplayRequest,
) -> SliceReplayCheckResult:
    """Pass if artifact source_stage matches the filter in the request (if set)."""
    if artifact is None:
        return _skip(check, "No artifact to check")
    if not request.source_stage:
        return _skip(check, "No source_stage filter in request")
    if artifact.source_stage == request.source_stage:
        return _pass(check, f"source_stage matches: {artifact.source_stage!r}")
    return _fail(
        check,
        f"source_stage mismatch: expected {request.source_stage!r}, got {artifact.source_stage!r}",
    )


# ---------------------------------------------------------------------------
# Check type registry
# ---------------------------------------------------------------------------

CheckFn = Any  # Callable[[SliceReplayCheck, FixturePack, Path, FixtureArtifact | None, SliceReplayRequest], SliceReplayCheckResult]

CHECK_REGISTRY: dict[str, CheckFn] = {
    "fixture_pack_loads": check_fixture_pack_loads,
    "copied_file_exists": check_copied_file_exists,
    "metadata_only_reason_present": check_metadata_only_reason_present,
    "source_manifest_loads": check_source_manifest_loads,
    "source_index_summary_loads": check_source_index_summary_loads,
    "json_artifact_reads": check_json_artifact_reads,
    "text_artifact_reads": check_text_artifact_reads,
    "failure_limitation_present": check_failure_limitation_present,
    "checksum_matches_if_available": check_checksum_matches_if_available,
    "artifact_kind_matches": check_artifact_kind_matches,
    "source_stage_matches": check_source_stage_matches,
}

__all__ = [
    "CHECK_REGISTRY",
    "check_artifact_kind_matches",
    "check_checksum_matches_if_available",
    "check_copied_file_exists",
    "check_failure_limitation_present",
    "check_fixture_pack_loads",
    "check_json_artifact_reads",
    "check_metadata_only_reason_present",
    "check_source_index_summary_loads",
    "check_source_manifest_loads",
    "check_source_stage_matches",
    "check_text_artifact_reads",
]
