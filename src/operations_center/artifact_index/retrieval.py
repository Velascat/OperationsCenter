# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Artifact retrieval API.

Provides safe reference and content retrieval for indexed artifacts.
All operations use the index exclusively — no directory scanning.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .errors import (
    ArtifactNotFoundError,
    ArtifactPathUnresolvableError,
    ManifestInvalidError,
    NoManifestPathError,
)
from .index import build_artifact_index
from .loader import load_artifact_manifest
from .models import IndexedArtifact, ManagedArtifactIndex

_DEFAULT_MAX_BYTES = 10 * 1024 * 1024  # 10 MiB safety ceiling


def get_artifact_by_id(index: ManagedArtifactIndex, artifact_id: str) -> IndexedArtifact:
    """Return the IndexedArtifact with the given artifact_id.

    Raises
    ------
    ArtifactNotFoundError
        No artifact with that id exists in the index.
    """
    artifact = index.get_by_id(artifact_id)
    if artifact is None:
        raise ArtifactNotFoundError(
            f"artifact '{artifact_id}' not found in index for "
            f"{index.source.repo_id}/{index.source.audit_type} run {index.source.run_id}"
        )
    return artifact


def resolve_artifact_path(index: ManagedArtifactIndex, artifact_id: str) -> Path:
    """Return the resolved absolute Path for the artifact.

    The path is taken from the index (derived from the manifest).
    No directory scanning is performed.

    Raises
    ------
    ArtifactNotFoundError
        No artifact with that id exists in the index.
    ArtifactPathUnresolvableError
        The artifact's path could not be resolved to an absolute path
        (e.g. relative path with no derivable repo root, or EXTERNAL_OR_UNKNOWN location).
    """
    artifact = get_artifact_by_id(index, artifact_id)

    if artifact.resolved_path is None:
        raise ArtifactPathUnresolvableError(
            f"artifact '{artifact_id}' has an unresolvable path: {artifact.path!r} "
            f"(location={artifact.location.value})"
        )

    return artifact.resolved_path


def read_text_artifact(
    index: ManagedArtifactIndex,
    artifact_id: str,
    *,
    max_bytes: int | None = _DEFAULT_MAX_BYTES,
) -> str:
    """Read and return the text content of an artifact.

    Parameters
    ----------
    index:
        The index containing the artifact.
    artifact_id:
        ID of the artifact to read.
    max_bytes:
        Maximum bytes to read. Defaults to 10 MiB. Pass None to remove the limit
        (use with caution for large artifacts).

    Raises
    ------
    ArtifactNotFoundError
        No artifact with that id exists.
    ArtifactPathUnresolvableError
        The artifact's path cannot be resolved.
    OSError
        The file cannot be read.
    UnicodeDecodeError
        The file content is not valid UTF-8. Callers should handle this if
        encoding is uncertain.
    """
    path = resolve_artifact_path(index, artifact_id)

    if max_bytes is not None:
        raw_bytes = path.read_bytes()
        if len(raw_bytes) > max_bytes:
            raw_bytes = raw_bytes[:max_bytes]
        return raw_bytes.decode("utf-8", errors="replace")

    return path.read_text(encoding="utf-8")


def read_json_artifact(
    index: ManagedArtifactIndex,
    artifact_id: str,
    *,
    max_bytes: int | None = _DEFAULT_MAX_BYTES,
) -> Any:
    """Read and parse a JSON artifact, returning the parsed value.

    Parameters
    ----------
    index:
        The index containing the artifact.
    artifact_id:
        ID of the artifact to read.
    max_bytes:
        Maximum bytes to read before parsing. Defaults to 10 MiB.

    Raises
    ------
    ArtifactNotFoundError
        No artifact with that id exists.
    ArtifactPathUnresolvableError
        The artifact's path cannot be resolved.
    ManifestInvalidError
        The file content is not valid JSON.
    OSError
        The file cannot be read.
    """
    import json

    text = read_text_artifact(index, artifact_id, max_bytes=max_bytes)

    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        artifact = get_artifact_by_id(index, artifact_id)
        raise ManifestInvalidError(
            f"artifact '{artifact_id}' at {artifact.path!r} is not valid JSON: {exc}"
        ) from exc


def index_dispatch_result(
    result: Any,
    *,
    repo_root: Path | str | None = None,
) -> ManagedArtifactIndex:
    """Build an artifact index from a Phase 6 ManagedAuditDispatchResult.

    Loads the manifest from result.artifact_manifest_path and builds the index.
    Does not rerun dispatch, scan directories, or inspect stdout/stderr.

    Parameters
    ----------
    result:
        A ManagedAuditDispatchResult (or any object with an
        artifact_manifest_path attribute).
    repo_root:
        Optional override for the managed repo root, forwarded to
        build_artifact_index() for path resolution.

    Raises
    ------
    NoManifestPathError
        result.artifact_manifest_path is None or absent.
    ManifestNotFoundError, ManifestInvalidError
        Propagated from load_artifact_manifest().
    """
    manifest_path = getattr(result, "artifact_manifest_path", None)
    if manifest_path is None:
        raise NoManifestPathError(
            "dispatch result has no artifact_manifest_path — "
            "the dispatch must have succeeded and the producer must have written "
            "a compliant artifact_manifest.json"
        )

    manifest = load_artifact_manifest(manifest_path)
    return build_artifact_index(manifest, manifest_path, repo_root=repo_root)


__all__ = [
    "get_artifact_by_id",
    "index_dispatch_result",
    "read_json_artifact",
    "read_text_artifact",
    "resolve_artifact_path",
]
