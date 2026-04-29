# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
"""Main fixture harvesting entry point.

harvest_fixtures() is the single public function in this module.
It wires together selector, writer, and returns the completed FixturePack.
"""

from __future__ import annotations

from pathlib import Path

from .errors import HarvestInputError
from .models import FixturePack, HarvestRequest
from .selector import select_fixture_artifacts
from .writer import write_fixture_pack


def harvest_fixtures(
    request: HarvestRequest,
    output_dir: Path,
) -> tuple[FixturePack, Path]:
    """Harvest a fixture pack from a managed artifact index.

    Runs artifact selection, copies safe artifacts into an OpsCenter-owned
    directory, and writes a fixture_pack.json with full provenance.

    Parameters
    ----------
    request:
        The HarvestRequest specifying which index to harvest, what profile
        to use, and copy policy constraints.
    output_dir:
        Directory where the fixture pack will be written. The pack is placed
        at output_dir/<fixture_pack_id>/.

    Returns
    -------
    (FixturePack, pack_dir)
        The fixture pack metadata and the path to the pack directory.

    Raises
    ------
    HarvestInputError
        If the request is invalid (e.g. missing required parameters for the
        selected profile, or artifact_ids not found in the index).
    FixturePackWriteError
        On filesystem failures during write.
    UnsafePathError
        If any artifact path escapes the fixture pack directory.
    """
    if request.index is None:
        raise HarvestInputError("HarvestRequest.index is required")

    selection = select_fixture_artifacts(request.index, request)

    return write_fixture_pack(
        index=request.index,
        selection=selection,
        request=request,
        output_dir=output_dir,
    )


__all__ = ["harvest_fixtures"]
