"""build_artifact_index() — builds a ManagedArtifactIndex from a validated manifest."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from operations_center.audit_contracts.artifact_manifest import (
    ManagedArtifactEntry,
    ManagedArtifactManifest,
)
from operations_center.audit_contracts.vocabulary import Limitation, Location

from .models import (
    ArtifactIndexSource,
    IndexedArtifact,
    ManagedArtifactIndex,
)

_MACHINE_READABLE_PREFIXES = ("application/json", "application/x-ndjson")


def _is_machine_readable(content_type: str) -> bool:
    return any(content_type.startswith(p) for p in _MACHINE_READABLE_PREFIXES)


def _derive_repo_root(manifest: ManagedArtifactManifest, manifest_dir: Path) -> Path | None:
    """Try to derive the repo root from manifest_dir and manifest.run_root.

    The manifest lives at:  {repo_root}/{run_root}/artifact_manifest.json
    So:  repo_root = manifest_dir.parents[len(run_root.parts) - 1]

    Returns None if run_root is absent or resolution would go above the filesystem root.
    """
    if not manifest.run_root:
        return None

    run_root_parts = len(Path(manifest.run_root).parts)
    if run_root_parts == 0:
        return None

    candidate = manifest_dir
    # The manifest directory IS the run_root dir, so step up run_root_parts levels.
    for _ in range(run_root_parts):
        parent = candidate.parent
        if parent == candidate:
            return None  # hit filesystem root
        candidate = parent

    return candidate


def _resolve_entry_path(
    entry: ManagedArtifactEntry,
    manifest: ManagedArtifactManifest,
    manifest_dir: Path,
    repo_root: Path | None,
) -> Path | None:
    """Resolve an artifact entry's path to an absolute Path, or return None.

    Resolution rules (Phase 2 semantics):
      - Absolute paths are returned unchanged.
      - EXTERNAL_OR_UNKNOWN paths are not resolved (return None — mark as unresolved).
      - All other relative paths resolve against repo_root (artifact_root).
        repo_root is either the caller-supplied override or derived from the
        manifest's run_root depth relative to the manifest directory.
    """
    raw = entry.path
    if not raw:
        return None

    p = Path(raw)
    if p.is_absolute():
        return p

    if entry.location == Location.EXTERNAL_OR_UNKNOWN:
        return None

    effective_root = repo_root or _derive_repo_root(manifest, manifest_dir)
    if effective_root is None:
        return None

    return (effective_root / p).resolve()


def _index_entry(
    entry: ManagedArtifactEntry,
    manifest: ManagedArtifactManifest,
    manifest_path: Path,
    repo_root: Path | None,
) -> IndexedArtifact:
    manifest_dir = manifest_path.parent
    resolved = _resolve_entry_path(entry, manifest, manifest_dir, repo_root)

    exists: bool | None = None
    if resolved is not None:
        exists = resolved.exists()

    is_partial = Limitation.PARTIAL_RUN in entry.limitations

    return IndexedArtifact(
        artifact_id=entry.artifact_id,
        repo_id=manifest.repo_id,
        producer=manifest.producer,
        run_id=manifest.run_id,
        audit_type=manifest.audit_type,
        manifest_path=str(manifest_path),
        artifact_kind=entry.artifact_kind,
        location=entry.location,
        path_role=entry.path_role,
        source_stage=entry.source_stage,
        status=entry.status,
        path=entry.path,
        relative_path=entry.relative_path,
        content_type=entry.content_type,
        size_bytes=entry.size_bytes,
        checksum=entry.checksum,
        consumer_types=list(entry.consumer_types),
        valid_for=list(entry.valid_for),
        limitations=list(entry.limitations),
        description=entry.description,
        metadata=dict(entry.metadata),
        resolved_path=resolved,
        exists_on_disk=exists,
        is_repo_singleton=entry.is_repo_singleton,
        is_partial=is_partial,
        is_machine_readable=_is_machine_readable(entry.content_type),
    )


def build_artifact_index(
    manifest: ManagedArtifactManifest,
    manifest_path: Path | str,
    *,
    repo_root: Path | str | None = None,
) -> ManagedArtifactIndex:
    """Build an in-memory ManagedArtifactIndex from a validated manifest.

    Parameters
    ----------
    manifest:
        A validated ManagedArtifactManifest (from load_artifact_manifest()).
    manifest_path:
        Absolute path to the manifest file. Used to anchor path resolution.
    repo_root:
        Optional override for the managed repo root directory. When provided,
        all relative artifact paths are resolved against this directory.
        When omitted, the builder attempts to derive the root from the manifest
        file location and the manifest's run_root field.

    Returns
    -------
    ManagedArtifactIndex
        Contains all artifact entries (including repo_singleton) as IndexedArtifact
        instances. excluded_paths are preserved at index level, not as artifacts.
    """
    abs_manifest = Path(manifest_path).resolve()
    effective_repo_root = Path(repo_root).resolve() if repo_root is not None else None

    source = ArtifactIndexSource(
        manifest_path=str(abs_manifest),
        repo_id=manifest.repo_id,
        run_id=manifest.run_id,
        audit_type=manifest.audit_type,
        producer=manifest.producer,
    )

    indexed: list[IndexedArtifact] = [
        _index_entry(entry, manifest, abs_manifest, effective_repo_root)
        for entry in manifest.artifacts
    ]

    return ManagedArtifactIndex(
        source=source,
        manifest_status=manifest.manifest_status,
        run_status=manifest.run_status,
        artifact_root=manifest.artifact_root,
        run_root=manifest.run_root,
        artifacts=indexed,
        excluded_paths=list(manifest.excluded_paths),
        warnings=list(manifest.warnings),
        errors=list(manifest.errors),
        limitations=list(manifest.limitations),
        metadata=dict(manifest.metadata),
    )


__all__ = ["build_artifact_index"]
