# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
"""Phase 10 — Slice Replay Testing from Fixture Packs.

Turns harvested fixture packs into executable, focused replay tests.
Replay checks validate narrow behavior slices against previously harvested
real-run artifact data, without rerunning full managed audits.

This module:
  - loads fixture packs via Phase 9 loader
  - builds profile-driven check lists
  - executes deterministic local checks against copied fixture data
  - writes replay reports to OpsCenter-owned paths

This module does NOT:
  - run full audits (Phase 6)
  - harvest new fixtures (Phase 9)
  - implement regression suite orchestration
  - mutate fixture packs or source artifacts
  - apply recommendations
  - import managed repo code
"""

from .checks import (
    CHECK_REGISTRY,
    check_artifact_kind_matches,
    check_checksum_matches_if_available,
    check_copied_file_exists,
    check_failure_limitation_present,
    check_fixture_pack_loads,
    check_json_artifact_reads,
    check_metadata_only_reason_present,
    check_source_index_summary_loads,
    check_source_manifest_loads,
    check_source_stage_matches,
    check_text_artifact_reads,
)
from .errors import (
    ReplayInputError,
    ReplayReportLoadError,
    ReplayReportWriteError,
    SliceReplayError,
)
from .models import (
    SliceReplayCheck,
    SliceReplayCheckResult,
    SliceReplayProfile,
    SliceReplayReport,
    SliceReplayRequest,
)
from .profiles import PROFILE_CHECKS, get_check_specs
from .reports import load_replay_report, write_replay_report
from .runner import run_slice_replay

__all__ = [
    # runner
    "run_slice_replay",
    # reports
    "load_replay_report",
    "write_replay_report",
    # models
    "SliceReplayCheck",
    "SliceReplayCheckResult",
    "SliceReplayProfile",
    "SliceReplayReport",
    "SliceReplayRequest",
    # profiles
    "PROFILE_CHECKS",
    "get_check_specs",
    # checks
    "CHECK_REGISTRY",
    "check_artifact_kind_matches",
    "check_checksum_matches_if_available",
    "check_copied_file_exists",
    "check_failure_limitation_present",
    "check_fixture_pack_loads",
    "check_json_artifact_reads",
    "check_metadata_only_reason_present",
    "check_source_index_summary_loads",
    "check_source_manifest_loads",
    "check_source_stage_matches",
    "check_text_artifact_reads",
    # errors
    "ReplayInputError",
    "ReplayReportLoadError",
    "ReplayReportWriteError",
    "SliceReplayError",
]
