# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Phase 11 mini regression suite models.

Output types (suite definitions, reports, entry results) are Pydantic so they
are serializable to JSON. Runtime inputs are plain dataclasses.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from operations_center.slice_replay.models import SliceReplayProfile


# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

EntryStatus = Literal["passed", "failed", "error", "skipped"]
SuiteStatus = Literal["passed", "failed", "error", "partial"]

_SAFE_ID_RE = re.compile(r"[^a-zA-Z0-9_\-]")


def _safe_id(raw: str) -> str:
    """Replace unsafe characters with underscores."""
    return _SAFE_ID_RE.sub("_", raw)


# ---------------------------------------------------------------------------
# Suite definition (serializable — stored in .json files)
# ---------------------------------------------------------------------------

class MiniRegressionSuiteEntry(BaseModel, frozen=True):
    """One replay target inside a suite definition.

    fixture_pack_path may be an absolute path or a relative path resolved at
    run time against the suite file's directory.
    """

    entry_id: str = Field(description="Unique within the suite. Must be path-safe.")
    fixture_pack_path: str = Field(description="Path to fixture_pack.json or pack directory.")
    replay_profile: SliceReplayProfile
    required: bool = Field(
        default=True,
        description="If True, failure causes the suite to report as failed/error.",
    )
    selected_fixture_artifact_ids: list[str] | None = None
    source_stage: str | None = None
    artifact_kind: str | None = None
    max_artifact_bytes: int = 10 * 1024 * 1024
    fail_fast: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class MiniRegressionSuiteDefinition(BaseModel):
    """A durable mini regression suite definition.

    Serialized to / loaded from a JSON file. Entries are ordered and explicit.
    """

    schema_version: str = "1.1"
    suite_id: str = Field(description="Stable, path-safe identifier.")
    name: str
    description: str = ""
    repo_id: str | None = Field(
        default=None,
        description="Managed repo this suite targets. None for multi-repo suites.",
    )
    audit_type: str | None = Field(
        default=None,
        description="Audit type this suite targets. None for multi-type suites.",
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    entries: list[MiniRegressionSuiteEntry] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_unique_entry_ids(self) -> "MiniRegressionSuiteDefinition":
        seen: set[str] = set()
        for entry in self.entries:
            if entry.entry_id in seen:
                from .errors import SuiteDefinitionError
                raise SuiteDefinitionError(
                    f"Duplicate entry_id {entry.entry_id!r} in suite {self.suite_id!r}"
                )
            seen.add(entry.entry_id)
        return self

    @property
    def required_entries(self) -> list[MiniRegressionSuiteEntry]:
        return [e for e in self.entries if e.required]

    @property
    def optional_entries(self) -> list[MiniRegressionSuiteEntry]:
        return [e for e in self.entries if not e.required]


# ---------------------------------------------------------------------------
# Entry and suite results (serializable)
# ---------------------------------------------------------------------------

class MiniRegressionEntryResult(BaseModel, frozen=True):
    """The result of executing one suite entry."""

    entry_id: str
    fixture_pack_id: str
    fixture_pack_path: str
    replay_profile: SliceReplayProfile
    required: bool
    status: EntryStatus
    slice_replay_report_path: str = Field(
        default="",
        description="Path where the slice replay report was written. Empty if run failed.",
    )
    summary: str
    error: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class MiniRegressionSuiteSummary(BaseModel, frozen=True):
    """Aggregated counts from a suite run."""

    total_entries: int
    required_entries: int
    optional_entries: int
    passed_entries: int
    failed_entries: int
    error_entries: int
    skipped_entries: int
    required_failures: int
    optional_failures: int

    @property
    def text(self) -> str:
        return (
            f"{self.total_entries} entries: "
            f"{self.passed_entries} passed, "
            f"{self.failed_entries} failed, "
            f"{self.error_entries} error, "
            f"{self.skipped_entries} skipped "
            f"({self.required_failures} required failures)"
        )


class MiniRegressionSuiteReport(BaseModel):
    """Durable report from a mini regression suite run.

    Written to {output_dir}/{suite_id}/{suite_run_id}/suite_report.json.
    """

    schema_version: str = "1.1"
    suite_run_id: str
    suite_id: str
    suite_name: str
    repo_id: str | None = Field(
        default=None,
        description="Managed repo this suite targets. Propagated from suite definition.",
    )
    audit_type: str | None = Field(
        default=None,
        description="Audit type this suite targets. Propagated from suite definition.",
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime
    ended_at: datetime
    status: SuiteStatus
    entry_results: list[MiniRegressionEntryResult] = Field(default_factory=list)
    summary: MiniRegressionSuiteSummary
    report_paths: list[str] = Field(
        default_factory=list,
        description="Paths to individual slice replay report files.",
    )
    limitations: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Runtime input (dataclass — holds non-serializable state)
# ---------------------------------------------------------------------------

@dataclass
class MiniRegressionRunRequest:
    """Runtime request for executing a mini regression suite."""

    suite_definition: MiniRegressionSuiteDefinition
    output_dir: Path
    fail_fast: bool = False
    include_optional_entries: bool = True
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_suite_run_id(suite_id: str) -> str:
    """Generate a stable, path-safe suite run id with a random suffix to avoid second-resolution collisions."""
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    suffix = uuid.uuid4().hex[:8]
    raw = f"{suite_id}__{ts}__{suffix}"
    return _SAFE_ID_RE.sub("_", raw)


__all__ = [
    "EntryStatus",
    "MiniRegressionEntryResult",
    "MiniRegressionRunRequest",
    "MiniRegressionSuiteDefinition",
    "MiniRegressionSuiteEntry",
    "MiniRegressionSuiteReport",
    "MiniRegressionSuiteSummary",
    "SuiteStatus",
    "make_suite_run_id",
]
