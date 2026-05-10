# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""load_artifact_manifest() — validated manifest loader."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from operations_center.audit_contracts.artifact_manifest import ManagedArtifactManifest

from .errors import ManifestInvalidError, ManifestNotFoundError


def load_artifact_manifest(path: Path | str) -> ManagedArtifactManifest:
    """Load and validate an artifact_manifest.json from a known path.

    The caller must supply the path directly — this function does not search
    for or infer manifest locations.

    Raises
    ------
    ManifestNotFoundError
        The file does not exist at the given path.
    ManifestInvalidError
        The file exists but is not parseable JSON, or fails Phase 2 contract
        validation.
    """
    p = Path(path)

    if not p.exists():
        raise ManifestNotFoundError(f"artifact_manifest.json not found: {p}")

    try:
        raw = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise ManifestInvalidError(f"could not read manifest file: {exc}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ManifestInvalidError(
            f"artifact_manifest.json is not valid JSON at {p}: {exc}"
        ) from exc

    try:
        return ManagedArtifactManifest.model_validate(data)
    except ValidationError as exc:
        raise ManifestInvalidError(
            f"artifact_manifest.json fails contract validation at {p}: {exc}"
        ) from exc


__all__ = ["load_artifact_manifest"]
