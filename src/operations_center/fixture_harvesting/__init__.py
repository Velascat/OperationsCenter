# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Phase 9 — Fixture Harvesting from Managed Repo Audit Artifacts.

Turns real managed audit runs into durable, reusable fixture packs.
Fixture packs capture selected artifacts, metadata, findings, and provenance
so later phases can perform fast slice replay and regression testing without
rerunning the full managed audit.

This module:
  - reads manifests and artifact indexes (Phase 7)
  - optionally integrates calibration findings (Phase 8) as selection evidence
  - copies safe declared artifacts into OpsCenter-owned fixture pack directories
  - writes fixture_pack.json with full provenance

This module does NOT:
  - execute replay tests (Phase 10)
  - create regression suites
  - mutate original producer artifacts
  - apply recommendations
  - import managed repo code
"""

from .errors import (
    FixtureHarvestingError,
    FixturePackLoadError,
    FixturePackWriteError,
    HarvestInputError,
    UnsafePathError,
)
from .harvester import harvest_fixtures
from .loader import load_fixture_pack
from .models import (
    CopyPolicy,
    FixtureArtifact,
    FixtureFindingReference,
    FixturePack,
    FixtureSelection,
    HarvestProfile,
    HarvestRequest,
    SelectedArtifact,
    make_fixture_pack_id,
)
from .selector import select_fixture_artifacts
from .writer import write_fixture_pack

__all__ = [
    # errors
    "FixtureHarvestingError",
    "FixturePackLoadError",
    "FixturePackWriteError",
    "HarvestInputError",
    "UnsafePathError",
    # models
    "CopyPolicy",
    "FixtureArtifact",
    "FixtureFindingReference",
    "FixturePack",
    "FixtureSelection",
    "HarvestProfile",
    "HarvestRequest",
    "SelectedArtifact",
    "make_fixture_pack_id",
    # functions
    "harvest_fixtures",
    "load_fixture_pack",
    "select_fixture_artifacts",
    "write_fixture_pack",
]
