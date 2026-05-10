# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Fixture pack writer.

Writes selected artifacts and metadata into an OperationsCenter-owned
fixture pack directory. Original artifacts are never modified.
"""

from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .errors import FixturePackWriteError, UnsafePathError
from .models import (
    CopyPolicy,
    FixtureArtifact,
    FixtureFindingReference,
    FixturePack,
    FixtureSelection,
    HarvestRequest,
    make_fixture_pack_id,
)
from operations_center.behavior_calibration.analyzer import _build_index_summary

if TYPE_CHECKING:
    from operations_center.artifact_index.models import IndexedArtifact, ManagedArtifactIndex

_SAFE_FILENAME_RE = re.compile(r"[^a-zA-Z0-9_\-\.]")
_DEFAULT_TEXT_TYPES = frozenset({
    "application/json",
    "application/x-ndjson",
    "text/plain",
    "text/csv",
    "text/yaml",
    "application/yaml",
})


def _safe_filename(name: str) -> str:
    """Convert a string to a safe filename."""
    safe = _SAFE_FILENAME_RE.sub("_", name)
    return safe[:200] or "artifact"


def _is_text_content_type(content_type: str) -> bool:
    ct = content_type.split(";")[0].strip().lower()
    return ct in _DEFAULT_TEXT_TYPES or ct.startswith("text/")


def _compute_checksum(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return f"sha256:{h.hexdigest()}"


def _assert_safe_destination(dest: Path, artifacts_dir: Path) -> None:
    """Raise UnsafePathError if dest is outside artifacts_dir."""
    try:
        dest.resolve().relative_to(artifacts_dir.resolve())
    except ValueError as exc:
        raise UnsafePathError(
            f"Destination path {dest} escapes fixture pack directory {artifacts_dir}"
        ) from exc


def _write_fixture_artifact(
    artifact: "IndexedArtifact",
    artifacts_dir: Path,
    policy: CopyPolicy,
    total_bytes_written: int,
) -> tuple[FixtureArtifact, int]:
    """Attempt to copy one artifact into the fixture pack.

    Returns (FixtureArtifact, bytes_written).
    """
    # Derive a path-safe filename
    stem = _safe_filename(artifact.artifact_id.replace(":", "__"))
    suffix = Path(artifact.path).suffix or ""
    filename = f"{stem}{suffix}"
    dest = artifacts_dir / filename

    _assert_safe_destination(dest, artifacts_dir)

    base_kwargs: dict[str, Any] = dict(
        source_artifact_id=artifact.artifact_id,
        artifact_kind=artifact.artifact_kind,
        source_stage=artifact.source_stage,
        location=artifact.location.value,
        path_role=artifact.path_role.value,
        source_path=artifact.path,
        content_type=artifact.content_type,
        # checksum and size_bytes excluded here; set per-path below
        limitations=[lim.value for lim in artifact.limitations],
    )

    def _meta_only(reason: str) -> tuple[FixtureArtifact, int]:
        return FixtureArtifact(
            **base_kwargs,
            checksum=artifact.checksum,
            size_bytes=artifact.size_bytes,
            fixture_relative_path=None,
            copied=False,
            copy_error=reason,
        ), 0

    # --- Guard: missing/unresolved ---
    if artifact.resolved_path is None:
        return _meta_only("path unresolvable")

    if artifact.exists_on_disk is False or artifact.status.value == "missing":
        if not policy.include_missing_files:
            return _meta_only("missing file excluded by copy policy")
        return _meta_only("source file does not exist on disk")

    src = artifact.resolved_path

    if not src.exists():
        return _meta_only(f"source file not found: {src}")

    # --- Guard: binary content type ---
    if not policy.include_binary_artifacts and not _is_text_content_type(artifact.content_type):
        return _meta_only(f"binary content type {artifact.content_type!r} excluded by copy policy")

    # --- Guard: allowed content types ---
    if policy.allowed_content_types is not None:
        ct = artifact.content_type.split(";")[0].strip().lower()
        if ct not in {x.lower() for x in policy.allowed_content_types}:
            return _meta_only(f"content type {artifact.content_type!r} not in allowed list")

    # --- Guard: file size ---
    try:
        file_size = src.stat().st_size
    except OSError as exc:
        return _meta_only(f"cannot stat source file: {exc}")

    if file_size > policy.max_artifact_bytes:
        return _meta_only(f"oversized: {file_size} bytes > max {policy.max_artifact_bytes}")

    if total_bytes_written + file_size > policy.max_total_bytes:
        return _meta_only("max_total_bytes budget exceeded")

    # --- Copy ---
    try:
        shutil.copy2(src, dest)
    except OSError as exc:
        return _meta_only(f"copy failed: {exc}")

    checksum = _compute_checksum(dest)
    rel_path = dest.name  # relative to artifacts_dir

    return FixtureArtifact(
        **base_kwargs,
        fixture_relative_path=rel_path,
        checksum=checksum,
        size_bytes=file_size,
        copied=True,
        copy_error="",
    ), file_size


def _build_finding_references(
    request: HarvestRequest,
) -> list[FixtureFindingReference]:
    """Convert CalibrationFinding objects into FixtureFindingReferences."""
    if not request.findings:
        return []
    refs: list[FixtureFindingReference] = []
    target_ids = set(request.finding_ids) if request.finding_ids else None
    for f in request.findings:
        fid = getattr(f, "finding_id", None)
        if target_ids is not None and fid not in target_ids:
            continue
        refs.append(FixtureFindingReference(
            source_finding_id=str(fid) if fid else "",
            severity=getattr(getattr(f, "severity", None), "value", None) or str(getattr(f, "severity", "")),
            category=getattr(getattr(f, "category", None), "value", None) or str(getattr(f, "category", "")),
            artifact_ids=list(getattr(f, "artifact_ids", [])),
            summary=str(getattr(f, "summary", "")),
        ))
    return refs


def write_fixture_pack(
    index: "ManagedArtifactIndex",
    selection: FixtureSelection,
    request: HarvestRequest,
    output_dir: Path,
) -> tuple[FixturePack, Path]:
    """Write selected artifacts and metadata into a fixture pack directory.

    Parameters
    ----------
    index:
        The source artifact index (read-only).
    selection:
        The FixtureSelection from select_fixture_artifacts().
    request:
        The original HarvestRequest for metadata and policy.
    output_dir:
        Root directory where fixture packs are stored. The pack will be
        created at output_dir/<fixture_pack_id>/.

    Returns
    -------
    (FixturePack, pack_dir)
        The pack metadata model and the path to the pack directory.

    Raises
    ------
    FixturePackWriteError
        On failure creating directories or writing metadata files.
    UnsafePathError
        If any artifact destination path escapes the pack directory.
    """
    pack_id = make_fixture_pack_id(
        index.source.repo_id,
        index.source.run_id,
        request.harvest_profile,
    )
    pack_dir = output_dir / pack_id
    artifacts_dir = pack_dir / "artifacts"

    try:
        artifacts_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise FixturePackWriteError(f"Cannot create fixture pack directory: {exc}") from exc

    # Build index summary
    summary = _build_index_summary(index)

    # Copy selected artifacts
    policy = request.copy_policy
    fixture_artifacts: list[FixtureArtifact] = []
    total_bytes = 0

    for sel in selection.selected:
        fa, bytes_written = _write_fixture_artifact(
            sel.artifact, artifacts_dir, policy, total_bytes
        )
        fixture_artifacts.append(fa)
        total_bytes += bytes_written

    # Copy source manifest for provenance
    src_manifest = Path(index.source.manifest_path)
    if src_manifest.exists():
        try:
            shutil.copy2(src_manifest, pack_dir / "source_manifest.json")
        except OSError:
            pass  # non-fatal: provenance path is still recorded

    # Write index summary
    try:
        (pack_dir / "source_index_summary.json").write_text(
            summary.model_dump_json(indent=2), encoding="utf-8"
        )
    except OSError as exc:
        raise FixturePackWriteError(f"Cannot write source_index_summary.json: {exc}") from exc

    # Build finding references
    finding_refs = _build_finding_references(request)

    # Assemble limitations
    limitations = [lim.value for lim in index.limitations]

    pack = FixturePack(
        fixture_pack_id=pack_id,
        created_by=request.created_by,
        source_repo_id=index.source.repo_id,
        source_run_id=index.source.run_id,
        source_audit_type=index.source.audit_type,
        source_manifest_path=str(src_manifest),
        source_index_summary=summary,
        harvest_profile=request.harvest_profile,
        selection_rationale=request.selection_rationale,
        artifacts=fixture_artifacts,
        findings=finding_refs,
        limitations=limitations,
        metadata=dict(request.metadata),
    )

    # Write fixture_pack.json
    try:
        (pack_dir / "fixture_pack.json").write_text(
            pack.model_dump_json(indent=2), encoding="utf-8"
        )
    except OSError as exc:
        raise FixturePackWriteError(f"Cannot write fixture_pack.json: {exc}") from exc

    return pack, pack_dir


__all__ = ["write_fixture_pack"]
