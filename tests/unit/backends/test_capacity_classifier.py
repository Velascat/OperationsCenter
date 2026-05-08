# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""G-V04 / G-005 — capacity-exhaustion classifier tests.

Confirms the shared classifier matches observed-in-the-wild phrases and
that adapters flip exit-0 runs to FAILED when capacity-exhaustion text
appears in stdout/stderr.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from rxp.contracts import RuntimeInvocation, RuntimeResult

from operations_center.backends._capacity_classifier import classify_capacity_exhaustion
from operations_center.backends.direct_local.adapter import DirectLocalBackendAdapter
from operations_center.backends.kodo.models import KodoRunCapture
from operations_center.backends.kodo.normalize import normalize as kodo_normalize
from operations_center.config.settings import AiderSettings
from operations_center.contracts.enums import ExecutionStatus, FailureReasonCategory
from operations_center.contracts.execution import ExecutionRequest


# ---------------------------------------------------------------------------
# Pure classifier
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "phrase",
    [
        "You're out of extra usage · resets 4:20am",
        "you have hit your usage limit",
        "Quota exhausted for this organization",
        "ERROR: insufficient quota",
        "you've run out of credits",
        "Payment Required",
    ],
)
def test_classifier_matches_known_phrases(phrase: str) -> None:
    excerpt = classify_capacity_exhaustion(phrase)
    assert excerpt is not None
    assert excerpt.startswith("capacity exhaustion detected:")


@pytest.mark.parametrize(
    "phrase",
    [
        "",
        "everything is fine",
        "Done: 1/1 stage completed",
        "no changes",
    ],
)
def test_classifier_ignores_unrelated_text(phrase: str) -> None:
    assert classify_capacity_exhaustion(phrase) is None


def test_classifier_returns_excerpt_for_matched_line() -> None:
    excerpt = classify_capacity_exhaustion(
        "preamble line\nYou're out of extra usage · resets 4:20am\ntrailing line"
    )
    assert excerpt is not None
    assert "out of extra usage" in excerpt.lower()
    assert "preamble" not in excerpt
    assert "trailing" not in excerpt


# ---------------------------------------------------------------------------
# direct_local: capacity excerpt in stdout flips exit-0 to FAILED
# ---------------------------------------------------------------------------


class _CapacityFakeRuntime:
    """Writes a capacity-exhaustion notice to stdout and reports succeeded."""

    def __init__(self, *, stdout: str) -> None:
        self._stdout = stdout

    def run(self, invocation: RuntimeInvocation) -> RuntimeResult:
        ar = Path(invocation.artifact_directory) if invocation.artifact_directory else Path("/tmp")
        ar.mkdir(parents=True, exist_ok=True)
        sout = ar / "stdout.txt"
        serr = ar / "stderr.txt"
        sout.write_text(self._stdout, encoding="utf-8")
        serr.write_text("", encoding="utf-8")
        now = datetime.now(timezone.utc).isoformat()
        return RuntimeResult(
            invocation_id=invocation.invocation_id,
            runtime_name=invocation.runtime_name,
            runtime_kind=invocation.runtime_kind,
            status="succeeded",
            exit_code=0,
            started_at=now,
            finished_at=now,
            stdout_path=str(sout),
            stderr_path=str(serr),
        )


def _request(tmp_path: Path) -> ExecutionRequest:
    (tmp_path / "repo").mkdir(exist_ok=True)
    return ExecutionRequest(
        proposal_id="prop", decision_id="dec", goal_text="x",
        repo_key="r", clone_url="https://example.invalid/r.git",
        base_branch="main", task_branch="auto/r",
        workspace_path=tmp_path / "repo",
    )


def test_direct_local_flips_exit_zero_capacity_run_to_failed(tmp_path: Path) -> None:
    runtime = _CapacityFakeRuntime(
        stdout="working...\nYou're out of extra usage · resets 4:20am\n"
    )
    adapter = DirectLocalBackendAdapter(
        AiderSettings(binary="/bin/true", timeout_seconds=10), runtime=runtime,
    )
    result = adapter.execute(_request(tmp_path))

    assert result.success is False
    assert result.status == ExecutionStatus.FAILED
    assert result.failure_category == FailureReasonCategory.BACKEND_ERROR
    assert "capacity exhaustion" in (result.failure_reason or "").lower()


def test_direct_local_clean_success_unchanged(tmp_path: Path) -> None:
    runtime = _CapacityFakeRuntime(stdout="all good")
    adapter = DirectLocalBackendAdapter(
        AiderSettings(binary="/bin/true", timeout_seconds=10), runtime=runtime,
    )
    result = adapter.execute(_request(tmp_path))

    assert result.success is True
    assert result.status == ExecutionStatus.SUCCEEDED
    assert result.failure_category is None


# ---------------------------------------------------------------------------
# kodo normalize: capacity excerpt in combined_output flips success
# ---------------------------------------------------------------------------


def _kodo_capture(*, stdout: str = "", stderr: str = "", exit_code: int = 0) -> KodoRunCapture:
    now = datetime.now(timezone.utc)
    return KodoRunCapture(
        run_id="run-1",
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        command=["kodo"],
        started_at=now,
        finished_at=now,
        duration_ms=0,
    )


def test_kodo_normalize_flips_capacity_exhaustion_to_failed() -> None:
    capture = _kodo_capture(
        stdout="Done: 1/1 stage completed\nYou're out of extra usage · resets 4:20am\n"
    )
    result = kodo_normalize(capture, proposal_id="p", decision_id="d")
    assert result.success is False
    assert result.status == ExecutionStatus.FAILED
    assert result.failure_category == FailureReasonCategory.BACKEND_ERROR
    assert "capacity exhaustion" in (result.failure_reason or "").lower()


def test_kodo_normalize_clean_success_unchanged() -> None:
    capture = _kodo_capture(stdout="Done: 1/1 stage completed\n")
    result = kodo_normalize(capture, proposal_id="p", decision_id="d")
    assert result.success is True
    assert result.status == ExecutionStatus.SUCCEEDED
