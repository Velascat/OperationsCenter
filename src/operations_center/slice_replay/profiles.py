# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
"""Replay profile → check-type mapping.

Each profile selects a set of check types and determines whether they apply
per-artifact or once per pack. Check types are resolved via CHECK_REGISTRY.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import SliceReplayProfile


@dataclass
class CheckSpec:
    """Describes one check to run.

    check_type: key in CHECK_REGISTRY
    per_artifact: if True, run once per selected artifact; if False, run once for the pack
    required: failure causes report status 'failed' (not just 'partial')
    filter_fn: optional extra filter — returns True if this check applies to an artifact
    """

    check_type: str
    per_artifact: bool = True
    required: bool = True
    # filter function name (string key) or None = always apply
    apply_only_when: str | None = None


def _copied_only(artifact) -> bool:
    return artifact is not None and artifact.copied


def _metadata_only(artifact) -> bool:
    return artifact is not None and not artifact.copied


def _json_artifact(artifact) -> bool:
    if artifact is None or not artifact.copied:
        return False
    ct = artifact.content_type.split(";")[0].strip().lower()
    return ct in ("application/json", "application/x-ndjson")


def _text_artifact(artifact) -> bool:
    from operations_center.slice_replay.checks import _is_text_type
    return artifact is not None and artifact.copied and _is_text_type(artifact.content_type)


_FILTER_REGISTRY = {
    "copied_only": _copied_only,
    "metadata_only": _metadata_only,
    "json_artifact": _json_artifact,
    "text_artifact": _text_artifact,
}


# Profile definitions — ordered list of CheckSpec entries
PROFILE_CHECKS: dict[SliceReplayProfile, list[CheckSpec]] = {
    SliceReplayProfile.FIXTURE_INTEGRITY: [
        CheckSpec("fixture_pack_loads", per_artifact=False, required=True),
        CheckSpec("source_manifest_loads", per_artifact=False, required=True),
        CheckSpec("source_index_summary_loads", per_artifact=False, required=True),
        CheckSpec("copied_file_exists", per_artifact=True, required=True, apply_only_when="copied_only"),
        CheckSpec("metadata_only_reason_present", per_artifact=True, required=True, apply_only_when="metadata_only"),
        CheckSpec("checksum_matches_if_available", per_artifact=True, required=False, apply_only_when="copied_only"),
    ],
    SliceReplayProfile.MANIFEST_CONTRACT: [
        CheckSpec("fixture_pack_loads", per_artifact=False, required=True),
        CheckSpec("source_manifest_loads", per_artifact=False, required=True),
        CheckSpec("source_index_summary_loads", per_artifact=False, required=True),
    ],
    SliceReplayProfile.ARTIFACT_READABILITY: [
        CheckSpec("fixture_pack_loads", per_artifact=False, required=True),
        CheckSpec("copied_file_exists", per_artifact=True, required=True, apply_only_when="copied_only"),
        CheckSpec("json_artifact_reads", per_artifact=True, required=True, apply_only_when="json_artifact"),
        CheckSpec("text_artifact_reads", per_artifact=True, required=False, apply_only_when="text_artifact"),
        CheckSpec("checksum_matches_if_available", per_artifact=True, required=False, apply_only_when="copied_only"),
    ],
    SliceReplayProfile.FAILURE_SLICE: [
        CheckSpec("fixture_pack_loads", per_artifact=False, required=True),
        CheckSpec("source_manifest_loads", per_artifact=False, required=True),
        CheckSpec("failure_limitation_present", per_artifact=False, required=True),
        CheckSpec("copied_file_exists", per_artifact=True, required=False, apply_only_when="copied_only"),
        CheckSpec("metadata_only_reason_present", per_artifact=True, required=True, apply_only_when="metadata_only"),
        CheckSpec("json_artifact_reads", per_artifact=True, required=False, apply_only_when="json_artifact"),
    ],
    SliceReplayProfile.STAGE_SLICE: [
        CheckSpec("fixture_pack_loads", per_artifact=False, required=True),
        CheckSpec("source_manifest_loads", per_artifact=False, required=False),
        CheckSpec("copied_file_exists", per_artifact=True, required=True, apply_only_when="copied_only"),
        CheckSpec("source_stage_matches", per_artifact=True, required=True),
        CheckSpec("json_artifact_reads", per_artifact=True, required=False, apply_only_when="json_artifact"),
        CheckSpec("text_artifact_reads", per_artifact=True, required=False, apply_only_when="text_artifact"),
    ],
    SliceReplayProfile.METADATA_ONLY_SLICE: [
        CheckSpec("fixture_pack_loads", per_artifact=False, required=True),
        CheckSpec("metadata_only_reason_present", per_artifact=True, required=True, apply_only_when="metadata_only"),
    ],
}


def get_check_specs(profile: SliceReplayProfile) -> list[CheckSpec]:
    """Return the ordered list of CheckSpecs for a given profile."""
    specs = PROFILE_CHECKS.get(profile)
    if specs is None:
        from .errors import ReplayInputError
        raise ReplayInputError(f"Unknown replay profile: {profile!r}")
    return specs


def get_artifact_filter(apply_only_when: str | None):
    """Return the filter callable for the given filter key, or None."""
    if apply_only_when is None:
        return None
    return _FILTER_REGISTRY.get(apply_only_when)


__all__ = [
    "CheckSpec",
    "PROFILE_CHECKS",
    "get_artifact_filter",
    "get_check_specs",
]
