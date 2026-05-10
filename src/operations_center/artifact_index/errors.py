# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Errors raised by the artifact index and retrieval API."""

from __future__ import annotations


class ArtifactIndexError(Exception):
    """Base class for all artifact index errors."""


class ManifestNotFoundError(ArtifactIndexError):
    """The artifact_manifest.json file does not exist at the given path."""


class ManifestInvalidError(ArtifactIndexError):
    """The manifest file exists but is not valid JSON or fails contract validation."""


class ArtifactNotFoundError(ArtifactIndexError):
    """No indexed artifact matches the requested artifact_id."""


class ArtifactPathUnresolvableError(ArtifactIndexError):
    """The artifact's path cannot be resolved to a usable filesystem path.

    Raised when the path is relative and no base directory is available,
    or when the location is EXTERNAL_OR_UNKNOWN.
    """


class NoManifestPathError(ArtifactIndexError):
    """A dispatch result was passed to index_dispatch_result() but has no manifest path.

    The dispatch result must have artifact_manifest_path set before indexing.
    """


__all__ = [
    "ArtifactIndexError",
    "ManifestNotFoundError",
    "ManifestInvalidError",
    "ArtifactNotFoundError",
    "ArtifactPathUnresolvableError",
    "NoManifestPathError",
]
