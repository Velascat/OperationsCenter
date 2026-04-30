# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Tests for run_status and artifact manifest discovery."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from operations_center.audit_contracts.vocabulary import RunStatus
from operations_center.audit_toolset.discovery import (
    load_run_status_entrypoint,
    resolve_artifact_manifest_path,
)
from operations_center.audit_toolset.errors import (
    ArtifactManifestPathMissingError,
    ArtifactManifestPathResolutionError,
    RunStatusContractError,
    RunStatusNotFoundError,
)

_EXAMPLES = Path(__file__).parent.parent.parent.parent / "examples" / "audit_contracts"

_MINIMAL_RUN_STATUS = {
    "producer": "videofoundry",
    "repo_id": "videofoundry",
    "run_id": "3dead998d4c44e1cb296bef061de50f3",
    "audit_type": "representative",
    "status": "completed",
    "artifact_manifest_path": "tools/audit/report/representative/bucket/artifact_manifest.json",
}


class TestLoadRunStatusEntrypoint:
    def test_loads_completed_example(self) -> None:
        rs = load_run_status_entrypoint(_EXAMPLES / "completed_run_status.json")
        assert rs.status == RunStatus.COMPLETED
        assert rs.is_terminal

    def test_loads_failed_example(self) -> None:
        rs = load_run_status_entrypoint(_EXAMPLES / "failed_run_status.json")
        assert rs.status == RunStatus.INTERRUPTED
        assert rs.is_terminal

    def test_raises_for_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(RunStatusNotFoundError):
            load_run_status_entrypoint(tmp_path / "nonexistent.json")

    def test_raises_for_invalid_json(self, tmp_path: Path) -> None:
        bad = tmp_path / "run_status.json"
        bad.write_text("this is not json", encoding="utf-8")
        with pytest.raises(RunStatusContractError):
            load_run_status_entrypoint(bad)

    def test_raises_for_invalid_contract(self, tmp_path: Path) -> None:
        bad = tmp_path / "run_status.json"
        bad.write_text(json.dumps({"status": "completed"}), encoding="utf-8")
        with pytest.raises(RunStatusContractError):
            load_run_status_entrypoint(bad)

    def test_legacy_in_progress_loads_as_non_compliant(self, tmp_path: Path) -> None:
        data = {**_MINIMAL_RUN_STATUS, "status": "in_progress"}
        f = tmp_path / "run_status.json"
        f.write_text(json.dumps(data), encoding="utf-8")
        rs = load_run_status_entrypoint(f)
        assert rs.status == RunStatus.IN_PROGRESS_LEGACY
        assert not rs.is_compliant

    def test_example_note_key_stripped(self, tmp_path: Path) -> None:
        data = {**_MINIMAL_RUN_STATUS, "_example_note": "synthetic"}
        f = tmp_path / "run_status.json"
        f.write_text(json.dumps(data), encoding="utf-8")
        rs = load_run_status_entrypoint(f)
        assert rs is not None

    def test_is_compliant_true_for_completed_example(self) -> None:
        rs = load_run_status_entrypoint(_EXAMPLES / "completed_run_status.json")
        assert rs.is_compliant

    def test_string_path_accepted(self) -> None:
        rs = load_run_status_entrypoint(str(_EXAMPLES / "completed_run_status.json"))
        assert rs is not None

    def test_does_not_scan_directory(self, tmp_path: Path) -> None:
        (tmp_path / "other_file.json").write_text("{}", encoding="utf-8")
        with pytest.raises(RunStatusNotFoundError):
            load_run_status_entrypoint(tmp_path / "run_status.json")


class TestResolveArtifactManifestPath:
    def test_resolves_relative_path_with_base_dir(self, tmp_path: Path) -> None:
        data = {**_MINIMAL_RUN_STATUS}
        f = tmp_path / "run_status.json"
        f.write_text(json.dumps(data), encoding="utf-8")
        rs = load_run_status_entrypoint(f)
        resolved = resolve_artifact_manifest_path(rs, base_dir=tmp_path)
        expected = (tmp_path / _MINIMAL_RUN_STATUS["artifact_manifest_path"]).resolve()
        assert resolved == expected

    def test_resolves_from_completed_example(self) -> None:
        rs = load_run_status_entrypoint(_EXAMPLES / "completed_run_status.json")
        resolved = resolve_artifact_manifest_path(rs, base_dir="/some/repo/root")
        assert resolved.name == "artifact_manifest.json"
        assert resolved.is_absolute()

    def test_raises_when_path_is_missing(self) -> None:
        data = {**_MINIMAL_RUN_STATUS, "artifact_manifest_path": None}
        from operations_center.audit_contracts.run_status import ManagedRunStatus
        rs = ManagedRunStatus.model_validate(data)
        with pytest.raises(ArtifactManifestPathMissingError):
            resolve_artifact_manifest_path(rs)

    def test_raises_for_relative_path_without_base_dir(self) -> None:
        from operations_center.audit_contracts.run_status import ManagedRunStatus
        rs = ManagedRunStatus.model_validate(_MINIMAL_RUN_STATUS)
        with pytest.raises(ArtifactManifestPathResolutionError):
            resolve_artifact_manifest_path(rs)

    def test_absolute_path_returned_as_is(self) -> None:
        from operations_center.audit_contracts.run_status import ManagedRunStatus
        data = {**_MINIMAL_RUN_STATUS, "artifact_manifest_path": "/abs/path/artifact_manifest.json"}
        rs = ManagedRunStatus.model_validate(data)
        resolved = resolve_artifact_manifest_path(rs)
        assert resolved == Path("/abs/path/artifact_manifest.json")

    def test_base_dir_string_accepted(self, tmp_path: Path) -> None:
        from operations_center.audit_contracts.run_status import ManagedRunStatus
        rs = ManagedRunStatus.model_validate(_MINIMAL_RUN_STATUS)
        resolved = resolve_artifact_manifest_path(rs, base_dir=str(tmp_path))
        assert resolved.is_absolute()

    def test_manifest_path_missing_error_mentions_run_id(self) -> None:
        from operations_center.audit_contracts.run_status import ManagedRunStatus
        rs = ManagedRunStatus.model_validate({**_MINIMAL_RUN_STATUS, "artifact_manifest_path": None})
        with pytest.raises(ArtifactManifestPathMissingError, match="3dead998"):
            resolve_artifact_manifest_path(rs)

    def test_no_directory_scanning(self, tmp_path: Path) -> None:
        # Even if artifact_manifest.json exists nearby, it is not found by scanning.
        (tmp_path / "artifact_manifest.json").write_text("{}", encoding="utf-8")
        from operations_center.audit_contracts.run_status import ManagedRunStatus
        rs = ManagedRunStatus.model_validate({**_MINIMAL_RUN_STATUS, "artifact_manifest_path": None})
        with pytest.raises(ArtifactManifestPathMissingError):
            resolve_artifact_manifest_path(rs, base_dir=tmp_path)
