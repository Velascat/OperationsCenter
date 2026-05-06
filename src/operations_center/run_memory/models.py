# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Run Memory models — RunMemoryRecord, RunMemoryQuery, SourceType."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class SourceType(str, Enum):
    """v1: only execution_result. Other source types added when their
    artifacts exist on disk in OperationsCenter today."""

    EXECUTION_RESULT = "execution_result"


@dataclass(frozen=True)
class RunMemoryRecord:
    """A single advisory entry.

    ``record_id`` is deterministic from ``result_id`` so rebuilds are
    idempotent. See ``deterministic_record_id`` in :mod:`index`.
    """

    record_id: str
    run_id: str
    request_id: str
    result_id: str
    repo_id: str | None
    artifact_paths: tuple[str, ...]
    contract_kinds: tuple[str, ...]  # free-form strings (intentionally not enum in v1)
    status: str
    summary: str
    tags: tuple[str, ...]
    created_at: str  # ISO-8601, stored verbatim
    source_type: SourceType

    def to_jsonl(self) -> dict:
        return {
            "record_id": self.record_id,
            "run_id": self.run_id,
            "request_id": self.request_id,
            "result_id": self.result_id,
            "repo_id": self.repo_id,
            "artifact_paths": list(self.artifact_paths),
            "contract_kinds": list(self.contract_kinds),
            "status": self.status,
            "summary": self.summary,
            "tags": list(self.tags),
            "created_at": self.created_at,
            "source_type": self.source_type.value,
        }

    @classmethod
    def from_jsonl(cls, data: dict) -> "RunMemoryRecord":
        return cls(
            record_id=data["record_id"],
            run_id=data["run_id"],
            request_id=data["request_id"],
            result_id=data["result_id"],
            repo_id=data.get("repo_id"),
            artifact_paths=tuple(data.get("artifact_paths") or ()),
            contract_kinds=tuple(data.get("contract_kinds") or ()),
            status=data["status"],
            summary=data.get("summary", ""),
            tags=tuple(data.get("tags") or ()),
            created_at=data["created_at"],
            source_type=SourceType(data.get("source_type", "execution_result")),
        )


@dataclass(frozen=True)
class RunMemoryQuery:
    """All filters applied with AND. ``text`` is strict substring across
    summary, tags, artifact_paths, repo_id, run_id (case-insensitive)."""

    repo_id: str | None = None
    run_id: str | None = None
    request_id: str | None = None
    result_id: str | None = None
    status: str | None = None
    contract_kind: str | None = None
    tag: str | None = None
    text: str | None = None
    time_range: tuple[datetime, datetime] | None = None  # inclusive [start, end]
