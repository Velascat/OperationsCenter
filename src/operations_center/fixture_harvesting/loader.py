# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Fixture pack loader.

load_fixture_pack() deserializes a fixture_pack.json and validates that all
copied artifacts are present on disk. Does not execute replay tests.
"""

from __future__ import annotations

import json
from pathlib import Path

from .errors import FixturePackLoadError
from .models import FixturePack


def load_fixture_pack(path: Path | str) -> FixturePack:
    """Load and validate a fixture pack from a fixture_pack.json file.

    Validates:
    - fixture_pack.json exists and is valid JSON / valid FixturePack schema
    - Every FixtureArtifact with copied=True has its file present in artifacts/

    Does NOT execute replay tests. Does NOT modify any files.

    Parameters
    ----------
    path:
        Path to fixture_pack.json (or the fixture pack directory — will look
        for fixture_pack.json inside it).

    Returns
    -------
    FixturePack

    Raises
    ------
    FileNotFoundError
        If fixture_pack.json does not exist.
    FixturePackLoadError
        If the JSON is invalid, schema validation fails, or a copied artifact
        file is missing from disk.
    """
    pack_path = Path(path)

    # Accept either the directory or the .json file
    if pack_path.is_dir():
        pack_path = pack_path / "fixture_pack.json"

    if not pack_path.exists():
        raise FileNotFoundError(f"fixture_pack.json not found: {pack_path}")

    try:
        raw = pack_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise FixturePackLoadError(f"Cannot read fixture_pack.json: {exc}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise FixturePackLoadError(f"fixture_pack.json is not valid JSON: {exc}") from exc

    try:
        pack = FixturePack.model_validate(data)
    except Exception as exc:
        raise FixturePackLoadError(f"fixture_pack.json schema validation failed: {exc}") from exc

    # Validate that copied artifact files exist
    artifacts_dir = pack_path.parent / "artifacts"
    missing_files: list[str] = []
    for artifact in pack.artifacts:
        if artifact.copied and artifact.fixture_relative_path:
            artifact_file = artifacts_dir / artifact.fixture_relative_path
            if not artifact_file.exists():
                missing_files.append(
                    f"{artifact.source_artifact_id} -> {artifact.fixture_relative_path}"
                )

    if missing_files:
        raise FixturePackLoadError(
            f"fixture pack {pack.fixture_pack_id!r} has {len(missing_files)} missing copied "
            f"artifact file(s):\n" + "\n".join(f"  {m}" for m in missing_files)
        )

    return pack


__all__ = ["load_fixture_pack"]
