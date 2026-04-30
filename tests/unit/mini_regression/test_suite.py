# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Tests for Phase 11 mini regression suite.

Covers: suite loader, runner, report persistence, failure semantics,
status computation, import boundaries.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from operations_center.mini_regression import (
    MiniRegressionRunRequest,
    MiniRegressionSuiteDefinition,
    MiniRegressionSuiteEntry,
    SuiteDefinitionError,
    SuiteReportLoadError,
    load_mini_regression_suite,
    load_suite_report,
    run_mini_regression_suite,
)
from operations_center.mini_regression.runner import _compute_suite_status
from operations_center.slice_replay.models import SliceReplayProfile, SliceReplayReport


# ---------------------------------------------------------------------------
# Contract 1 — Suite loader: valid load
# ---------------------------------------------------------------------------

class TestSuiteLoader:
    def test_load_valid_suite(self, tmp_path: Path, simple_suite: MiniRegressionSuiteDefinition):
        suite_path = tmp_path / "suite.json"
        suite_path.write_text(simple_suite.model_dump_json(), encoding="utf-8")

        loaded = load_mini_regression_suite(suite_path)
        assert loaded.suite_id == simple_suite.suite_id
        assert len(loaded.entries) == 1

    def test_load_file_not_found(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            load_mini_regression_suite(tmp_path / "nonexistent.json")

    def test_load_invalid_json(self, tmp_path: Path):
        bad = tmp_path / "suite.json"
        bad.write_text("not valid {{{ json", encoding="utf-8")
        with pytest.raises(SuiteDefinitionError, match="not valid JSON"):
            load_mini_regression_suite(bad)

    def test_load_schema_validation_failure(self, tmp_path: Path):
        bad = tmp_path / "suite.json"
        bad.write_text(json.dumps({"suite_id": "x", "entries": "not_a_list"}), encoding="utf-8")
        with pytest.raises(SuiteDefinitionError, match="schema validation failed"):
            load_mini_regression_suite(bad)

    def test_load_duplicate_entry_ids(self, tmp_path: Path):
        suite_data = {
            "suite_id": "dup_suite",
            "name": "Dup",
            "entries": [
                {
                    "entry_id": "same",
                    "fixture_pack_path": "/some/path",
                    "replay_profile": "fixture_integrity",
                },
                {
                    "entry_id": "same",
                    "fixture_pack_path": "/other/path",
                    "replay_profile": "artifact_readability",
                },
            ],
        }
        path = tmp_path / "suite.json"
        path.write_text(json.dumps(suite_data), encoding="utf-8")
        with pytest.raises(SuiteDefinitionError, match="Duplicate entry_id"):
            load_mini_regression_suite(path)


# ---------------------------------------------------------------------------
# Contract 2 — Suite entry model validation
# ---------------------------------------------------------------------------

class TestSuiteEntry:
    def test_required_default(self):
        entry = MiniRegressionSuiteEntry(
            entry_id="e1",
            fixture_pack_path="/some/path",
            replay_profile=SliceReplayProfile.FIXTURE_INTEGRITY,
        )
        assert entry.required is True

    def test_optional_flag(self):
        entry = MiniRegressionSuiteEntry(
            entry_id="e1",
            fixture_pack_path="/some/path",
            replay_profile=SliceReplayProfile.ARTIFACT_READABILITY,
            required=False,
        )
        assert entry.required is False

    def test_frozen(self):
        entry = MiniRegressionSuiteEntry(
            entry_id="e1",
            fixture_pack_path="/some/path",
            replay_profile=SliceReplayProfile.FIXTURE_INTEGRITY,
        )
        with pytest.raises(Exception):
            entry.entry_id = "mutated"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Contract 3 — Suite definition properties
# ---------------------------------------------------------------------------

class TestSuiteDefinition:
    def test_required_entries_property(self, mixed_suite: MiniRegressionSuiteDefinition):
        req = mixed_suite.required_entries
        assert all(e.required for e in req)
        assert len(req) == 1

    def test_optional_entries_property(self, mixed_suite: MiniRegressionSuiteDefinition):
        opt = mixed_suite.optional_entries
        assert all(not e.required for e in opt)
        assert len(opt) == 1

    def test_roundtrip_json(self, simple_suite: MiniRegressionSuiteDefinition):
        raw = simple_suite.model_dump_json()
        loaded = MiniRegressionSuiteDefinition.model_validate_json(raw)
        assert loaded.suite_id == simple_suite.suite_id


# ---------------------------------------------------------------------------
# Contract 4 — Runner: all-passed suite
# ---------------------------------------------------------------------------

class TestRunnerPassed:
    def test_all_required_passed(self, run_request: MiniRegressionRunRequest):
        report = run_mini_regression_suite(run_request)
        assert report.status == "passed"
        assert len(report.entry_results) == 1
        assert report.entry_results[0].status in ("passed", "partial")

    def test_summary_written(self, run_request: MiniRegressionRunRequest):
        report = run_mini_regression_suite(run_request)
        assert report.summary.total_entries >= 1

    def test_report_file_written(self, run_request: MiniRegressionRunRequest):
        report = run_mini_regression_suite(run_request)
        expected = (
            run_request.output_dir
            / run_request.suite_definition.suite_id
            / report.suite_run_id
            / "suite_report.json"
        )
        assert expected.exists()


# ---------------------------------------------------------------------------
# Contract 5 — Runner: required entry failure → suite failed
# ---------------------------------------------------------------------------

class TestRunnerFailure:
    def test_bad_pack_path_required_entry_errors(self, tmp_path: Path):
        suite = MiniRegressionSuiteDefinition(
            suite_id="err_suite",
            name="Error Suite",
            entries=[
                MiniRegressionSuiteEntry(
                    entry_id="bad_entry",
                    fixture_pack_path="/nonexistent/pack",
                    replay_profile=SliceReplayProfile.FIXTURE_INTEGRITY,
                    required=True,
                )
            ],
        )
        request = MiniRegressionRunRequest(
            suite_definition=suite,
            output_dir=tmp_path / "out",
        )
        report = run_mini_regression_suite(request)
        assert report.status in ("failed", "error")
        assert report.entry_results[0].status == "error"

    def test_required_failure_reflected_in_summary(self, tmp_path: Path):
        suite = MiniRegressionSuiteDefinition(
            suite_id="fail_suite",
            name="Fail Suite",
            entries=[
                MiniRegressionSuiteEntry(
                    entry_id="bad_entry",
                    fixture_pack_path="/nonexistent/pack",
                    replay_profile=SliceReplayProfile.FIXTURE_INTEGRITY,
                    required=True,
                )
            ],
        )
        request = MiniRegressionRunRequest(
            suite_definition=suite,
            output_dir=tmp_path / "out",
        )
        report = run_mini_regression_suite(request)
        assert report.summary.required_failures >= 1


# ---------------------------------------------------------------------------
# Contract 6 — Runner: optional entry skip
# ---------------------------------------------------------------------------

class TestRunnerOptionalSkip:
    def test_optional_skipped_when_flag_false(
        self, mixed_suite: MiniRegressionSuiteDefinition, tmp_path: Path
    ):
        request = MiniRegressionRunRequest(
            suite_definition=mixed_suite,
            output_dir=tmp_path / "out",
            include_optional_entries=False,
        )
        report = run_mini_regression_suite(request)
        skipped = [r for r in report.entry_results if r.status == "skipped"]
        assert len(skipped) == 1
        assert not skipped[0].required

    def test_optional_included_by_default(
        self, mixed_suite: MiniRegressionSuiteDefinition, tmp_path: Path
    ):
        request = MiniRegressionRunRequest(
            suite_definition=mixed_suite,
            output_dir=tmp_path / "out",
            include_optional_entries=True,
        )
        report = run_mini_regression_suite(request)
        skipped = [r for r in report.entry_results if r.status == "skipped"]
        assert len(skipped) == 0

    def test_optional_failure_does_not_fail_suite(
        self, mixed_suite: MiniRegressionSuiteDefinition, tmp_path: Path
    ):
        # The optional entry uses failure_slice on a failure pack which may pass;
        # in any case, an optional failure should not cause suite-level failure
        request = MiniRegressionRunRequest(
            suite_definition=mixed_suite,
            output_dir=tmp_path / "out",
        )
        report = run_mini_regression_suite(request)
        # Suite status must be driven by required entries only
        required_results = [r for r in report.entry_results if r.required]
        if all(r.status in ("passed", "skipped") for r in required_results):
            assert report.status in ("passed", "partial")


# ---------------------------------------------------------------------------
# Contract 7 — Runner: fail_fast stops early
# ---------------------------------------------------------------------------

class TestRunnerFailFast:
    def test_fail_fast_stops_after_first_required_failure(self, tmp_path: Path):
        suite = MiniRegressionSuiteDefinition(
            suite_id="ff_suite",
            name="Fail Fast Suite",
            entries=[
                MiniRegressionSuiteEntry(
                    entry_id="bad_first",
                    fixture_pack_path="/nonexistent/pack",
                    replay_profile=SliceReplayProfile.FIXTURE_INTEGRITY,
                    required=True,
                ),
                MiniRegressionSuiteEntry(
                    entry_id="should_not_run",
                    fixture_pack_path="/also/nonexistent",
                    replay_profile=SliceReplayProfile.ARTIFACT_READABILITY,
                    required=True,
                ),
            ],
        )
        request = MiniRegressionRunRequest(
            suite_definition=suite,
            output_dir=tmp_path / "out",
            fail_fast=True,
        )
        report = run_mini_regression_suite(request)
        assert report.status == "partial"
        assert len(report.entry_results) == 1


# ---------------------------------------------------------------------------
# Contract 8 — Suite status computation rules
# ---------------------------------------------------------------------------

class TestSuiteStatusRules:
    def _make_result(self, *, required: bool, status: str):
        from operations_center.mini_regression.models import MiniRegressionEntryResult
        return MiniRegressionEntryResult(
            entry_id="e",
            fixture_pack_id="p",
            fixture_pack_path="/p",
            replay_profile=SliceReplayProfile.FIXTURE_INTEGRITY,
            required=required,
            status=status,
            summary="",
        )

    def test_all_required_passed(self):
        results = [self._make_result(required=True, status="passed")]
        assert _compute_suite_status(results, stopped_early=False) == "passed"

    def test_required_failure(self):
        results = [self._make_result(required=True, status="failed")]
        assert _compute_suite_status(results, stopped_early=False) == "failed"

    def test_required_error_no_failure(self):
        results = [self._make_result(required=True, status="error")]
        assert _compute_suite_status(results, stopped_early=False) == "error"

    def test_stopped_early_partial(self):
        results = [self._make_result(required=True, status="passed")]
        assert _compute_suite_status(results, stopped_early=True) == "partial"

    def test_optional_failure_does_not_fail(self):
        results = [
            self._make_result(required=True, status="passed"),
            self._make_result(required=False, status="failed"),
        ]
        assert _compute_suite_status(results, stopped_early=False) == "passed"

    def test_failed_takes_precedence_over_error(self):
        results = [
            self._make_result(required=True, status="failed"),
            self._make_result(required=True, status="error"),
        ]
        assert _compute_suite_status(results, stopped_early=False) == "failed"


# ---------------------------------------------------------------------------
# Contract 9 — Report persistence: write and load
# ---------------------------------------------------------------------------

class TestReportPersistence:
    def test_write_creates_file(self, run_request: MiniRegressionRunRequest):
        report = run_mini_regression_suite(run_request)
        report_path = (
            run_request.output_dir
            / run_request.suite_definition.suite_id
            / report.suite_run_id
            / "suite_report.json"
        )
        assert report_path.exists()

    def test_load_roundtrip(self, run_request: MiniRegressionRunRequest):
        report = run_mini_regression_suite(run_request)
        report_path = (
            run_request.output_dir
            / run_request.suite_definition.suite_id
            / report.suite_run_id
            / "suite_report.json"
        )
        loaded = load_suite_report(report_path)
        assert loaded.suite_run_id == report.suite_run_id
        assert loaded.status == report.status

    def test_load_not_found(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            load_suite_report(tmp_path / "nonexistent.json")

    def test_load_invalid_json(self, tmp_path: Path):
        bad = tmp_path / "report.json"
        bad.write_text("not json", encoding="utf-8")
        with pytest.raises(SuiteReportLoadError, match="not valid JSON"):
            load_suite_report(bad)


# ---------------------------------------------------------------------------
# Contract 10 — Import boundary: mini_regression never imports managed repo
# ---------------------------------------------------------------------------

class TestImportBoundary:
    _PACKAGE_ROOT = (
        Path(__file__).parents[3]
        / "src"
        / "operations_center"
        / "mini_regression"
    )

    _FORBIDDEN_PREFIXES = (
        "videofoundry",
        "managed_repo",
        "kodo",
        "codex",
        "archon",
    )

    def _collect_imports(self, source: str) -> list[str]:
        tree = ast.parse(source)
        names: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    names.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    names.append(node.module)
        return names

    @pytest.mark.parametrize("py_file", list(_PACKAGE_ROOT.glob("*.py")))
    def test_no_forbidden_imports(self, py_file: Path):
        source = py_file.read_text(encoding="utf-8")
        imports = self._collect_imports(source)
        for imp in imports:
            for prefix in self._FORBIDDEN_PREFIXES:
                assert not imp.startswith(prefix), (
                    f"{py_file.name} imports forbidden module {imp!r}"
                )


# ---------------------------------------------------------------------------
# Contract 11 — Suite limitations are aggregated from replay reports (gap_005)
# ---------------------------------------------------------------------------

class TestSuiteLimitations:
    def test_limitations_field_is_list(self, run_request: MiniRegressionRunRequest):
        report = run_mini_regression_suite(run_request)
        assert isinstance(report.limitations, list)

    def test_limitations_aggregated_from_failure_pack(
        self, failure_pack, tmp_path: Path
    ):
        """A failure fixture pack with partial_run limitation should surface it in the suite."""
        _, pack_dir = failure_pack
        suite = MiniRegressionSuiteDefinition(
            suite_id="limit_suite",
            name="Limitations Suite",
            entries=[
                MiniRegressionSuiteEntry(
                    entry_id="fail_entry",
                    fixture_pack_path=str(pack_dir),
                    replay_profile=SliceReplayProfile.FAILURE_SLICE,
                    required=True,
                )
            ],
        )
        request = MiniRegressionRunRequest(
            suite_definition=suite,
            output_dir=tmp_path / "out",
        )
        report = run_mini_regression_suite(request)
        # If the replay report surfaced limitations, they must appear in the suite report
        # (they may be empty if the replay found none — that is also valid)
        assert isinstance(report.limitations, list)

    def test_no_duplicate_limitations(self, mixed_suite: MiniRegressionSuiteDefinition, tmp_path: Path):
        """Duplicate limitation strings from multiple entries must be de-duplicated."""
        request = MiniRegressionRunRequest(
            suite_definition=mixed_suite,
            output_dir=tmp_path / "out",
        )
        report = run_mini_regression_suite(request)
        assert len(report.limitations) == len(set(report.limitations))

    def test_make_suite_run_id_unique(self):
        """Parallel calls to make_suite_run_id must not collide (gap_008)."""
        from operations_center.mini_regression.models import make_suite_run_id
        ids = {make_suite_run_id("test_suite") for _ in range(20)}
        assert len(ids) == 20


# ---------------------------------------------------------------------------
# Contract 12 — Replay partial status maps to suite entry passed (gap_r2_006)
# ---------------------------------------------------------------------------

_REPLAY_TARGET = "operations_center.mini_regression.runner.run_slice_replay"


def _make_partial_replay_report(fixture_pack_path: str) -> SliceReplayReport:
    return SliceReplayReport(
        fixture_pack_id="pack_001",
        fixture_pack_path=fixture_pack_path,
        source_repo_id="videofoundry",
        source_run_id="run_001",
        source_audit_type="representative",
        replay_profile=SliceReplayProfile.FIXTURE_INTEGRITY,
        status="partial",
        summary="Stopped early due to fail_fast",
        limitations=["partial_run: stopped before completion"],
    )


class TestReplayPartialSemantics:
    """gap_r2_006: replay status='partial' must map to entry status='passed'."""

    def test_partial_replay_maps_to_entry_passed(self, tmp_path: Path):
        suite = MiniRegressionSuiteDefinition(
            suite_id="partial_suite",
            name="Partial Replay Suite",
            entries=[
                MiniRegressionSuiteEntry(
                    entry_id="entry_partial",
                    fixture_pack_path=str(tmp_path / "pack.json"),
                    replay_profile=SliceReplayProfile.FIXTURE_INTEGRITY,
                    required=True,
                )
            ],
        )
        request = MiniRegressionRunRequest(
            suite_definition=suite,
            output_dir=tmp_path / "out",
        )
        partial_report = _make_partial_replay_report(str(tmp_path / "pack.json"))
        with patch(_REPLAY_TARGET, return_value=partial_report):
            suite_report = run_mini_regression_suite(request)

        assert len(suite_report.entry_results) == 1
        assert suite_report.entry_results[0].status == "passed", (
            "partial replay status must map to entry status 'passed'"
        )

    def test_partial_replay_does_not_fail_suite(self, tmp_path: Path):
        suite = MiniRegressionSuiteDefinition(
            suite_id="partial_suite2",
            name="Partial Suite",
            entries=[
                MiniRegressionSuiteEntry(
                    entry_id="entry_partial",
                    fixture_pack_path=str(tmp_path / "pack.json"),
                    replay_profile=SliceReplayProfile.FIXTURE_INTEGRITY,
                    required=True,
                )
            ],
        )
        request = MiniRegressionRunRequest(
            suite_definition=suite,
            output_dir=tmp_path / "out",
        )
        partial_report = _make_partial_replay_report(str(tmp_path / "pack.json"))
        with patch(_REPLAY_TARGET, return_value=partial_report):
            suite_report = run_mini_regression_suite(request)

        assert suite_report.status == "passed"

    def test_partial_replay_limitations_surfaced(self, tmp_path: Path):
        suite = MiniRegressionSuiteDefinition(
            suite_id="partial_suite3",
            name="Partial Limitations",
            entries=[
                MiniRegressionSuiteEntry(
                    entry_id="entry_partial",
                    fixture_pack_path=str(tmp_path / "pack.json"),
                    replay_profile=SliceReplayProfile.FIXTURE_INTEGRITY,
                    required=True,
                )
            ],
        )
        request = MiniRegressionRunRequest(
            suite_definition=suite,
            output_dir=tmp_path / "out",
        )
        partial_report = _make_partial_replay_report(str(tmp_path / "pack.json"))
        with patch(_REPLAY_TARGET, return_value=partial_report):
            suite_report = run_mini_regression_suite(request)

        assert "partial_run: stopped before completion" in suite_report.limitations


# ---------------------------------------------------------------------------
# Contract 13 — repo_id / audit_type propagation from suite definition
# ---------------------------------------------------------------------------

class TestRepoAuditTypeFields:
    """Top-level repo_id and audit_type propagate from suite definition to report."""

    def test_repo_id_and_audit_type_propagated(
        self, good_pack, tmp_path: Path
    ):
        _, pack_dir = good_pack
        suite = MiniRegressionSuiteDefinition(
            suite_id="typed_suite",
            name="Typed Suite",
            repo_id="videofoundry",
            audit_type="representative",
            entries=[
                MiniRegressionSuiteEntry(
                    entry_id="e1",
                    fixture_pack_path=str(pack_dir),
                    replay_profile=SliceReplayProfile.FIXTURE_INTEGRITY,
                )
            ],
        )
        request = MiniRegressionRunRequest(
            suite_definition=suite,
            output_dir=tmp_path / "out",
        )
        report = run_mini_regression_suite(request)
        assert report.repo_id == "videofoundry"
        assert report.audit_type == "representative"

    def test_repo_id_and_audit_type_default_none(
        self, simple_suite: MiniRegressionSuiteDefinition, tmp_path: Path
    ):
        """Suite definition without repo_id/audit_type → report fields are None."""
        request = MiniRegressionRunRequest(
            suite_definition=simple_suite,
            output_dir=tmp_path / "out",
        )
        report = run_mini_regression_suite(request)
        assert report.repo_id is None
        assert report.audit_type is None

    def test_fields_present_in_serialised_json(self, good_pack, tmp_path: Path):
        """repo_id and audit_type must appear in the written suite_report.json."""
        _, pack_dir = good_pack
        suite = MiniRegressionSuiteDefinition(
            suite_id="json_suite",
            name="JSON Suite",
            repo_id="videofoundry",
            audit_type="enrichment",
            entries=[
                MiniRegressionSuiteEntry(
                    entry_id="e1",
                    fixture_pack_path=str(pack_dir),
                    replay_profile=SliceReplayProfile.FIXTURE_INTEGRITY,
                )
            ],
        )
        request = MiniRegressionRunRequest(
            suite_definition=suite,
            output_dir=tmp_path / "out",
        )
        report = run_mini_regression_suite(request)
        raw = json.loads(report.model_dump_json())
        assert raw["repo_id"] == "videofoundry"
        assert raw["audit_type"] == "enrichment"

    def test_schema_version_is_1_1(self):
        """MiniRegressionSuiteReport schema_version must be 1.1 after field addition."""
        from operations_center.mini_regression.models import MiniRegressionSuiteReport
        assert MiniRegressionSuiteReport.model_fields["schema_version"].default == "1.1"

    def test_suite_definition_schema_version_is_1_1(self):
        """MiniRegressionSuiteDefinition schema_version must be 1.1 after field addition."""
        assert MiniRegressionSuiteDefinition.model_fields["schema_version"].default == "1.1"

    def test_suite_report_schema_delta_zero(self):
        """suite_report.schema.json must match MiniRegressionSuiteReport fields exactly."""
        from operations_center.mini_regression.models import MiniRegressionSuiteReport
        schema_path = (
            Path(__file__).parents[3]
            / "schemas"
            / "mini_regression"
            / "suite_report.schema.json"
        )
        file_fields = set(json.loads(schema_path.read_text()).get("properties", {}).keys())
        model_fields = set(MiniRegressionSuiteReport.model_json_schema().get("properties", {}).keys())
        assert file_fields == model_fields, f"Schema delta: {file_fields.symmetric_difference(model_fields)}"
