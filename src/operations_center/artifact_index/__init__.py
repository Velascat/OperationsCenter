# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Phase 7 — Artifact Index and Retrieval.

Provides manifest loading, single-run indexing, multi-run historical indexing,
querying, and retrieval APIs for managed repo audit artifacts.

The on-disk manifest is the source of truth. The single-run layer never scans
directories; the multi-run layer walks a search root looking for manifests
(but never inside them).
"""

from .errors import (
    ArtifactIndexError,
    ArtifactNotFoundError,
    ArtifactPathUnresolvableError,
    ManifestInvalidError,
    ManifestNotFoundError,
    NoManifestPathError,
)
from .index import build_artifact_index
from .loader import load_artifact_manifest
from .models import (
    ArtifactIndexSource,
    ArtifactQuery,
    IndexedArtifact,
    ManagedArtifactIndex,
)
from .multi_run import (
    IndexedRun,
    MultiRunArtifactIndex,
    build_multi_run_index,
    discover_manifest_files,
)
from .query import query_artifacts
from .retrieval import (
    get_artifact_by_id,
    index_dispatch_result,
    read_json_artifact,
    read_text_artifact,
    resolve_artifact_path,
)

__all__ = [
    # errors
    "ArtifactIndexError",
    "ArtifactNotFoundError",
    "ArtifactPathUnresolvableError",
    "ManifestInvalidError",
    "ManifestNotFoundError",
    "NoManifestPathError",
    # models
    "ArtifactIndexSource",
    "ArtifactQuery",
    "IndexedArtifact",
    "IndexedRun",
    "ManagedArtifactIndex",
    "MultiRunArtifactIndex",
    # single-run functions
    "build_artifact_index",
    "get_artifact_by_id",
    "index_dispatch_result",
    "load_artifact_manifest",
    "query_artifacts",
    "read_json_artifact",
    "read_text_artifact",
    "resolve_artifact_path",
    # multi-run functions
    "build_multi_run_index",
    "discover_manifest_files",
]
