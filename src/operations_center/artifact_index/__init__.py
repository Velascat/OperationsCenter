# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
"""Phase 7 — Artifact Index and Retrieval.

Provides manifest loading, artifact indexing, query, and retrieval APIs
for managed repo audit artifacts.

The manifest is the source of truth. No directory scanning is performed.
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
    "ManagedArtifactIndex",
    # functions
    "build_artifact_index",
    "get_artifact_by_id",
    "index_dispatch_result",
    "load_artifact_manifest",
    "query_artifacts",
    "read_json_artifact",
    "read_text_artifact",
    "resolve_artifact_path",
]
