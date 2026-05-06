# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Run Memory file-backed index — writer + query service + rebuild.

Storage layout:

    <index_dir>/
        records.jsonl        ← append-only log, one record per line

Single write site contract:
    ``record_execution_result`` is the only function that appends. Callers
    invoke it post-finalize. ``rebuild_index_from_artifacts`` clears the
    file and re-creates it deterministically.

Deterministic IDs:
    ``record_id`` = ``"rmr-" + sha256(result_id)[:16]`` so the same
    ``ExecutionResult`` always produces the same record. Rebuilds are
    idempotent.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

from .models import RunMemoryQuery, RunMemoryRecord, SourceType

_RECORDS_FILE = "records.jsonl"


def deterministic_record_id(result_id: str) -> str:
    """Stable identifier derived from ``result_id`` only. Rebuild-safe."""
    digest = hashlib.sha256(result_id.encode("utf-8")).hexdigest()[:16]
    return f"rmr-{digest}"


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------


@dataclass
class RunMemoryIndexWriter:
    """Append-only writer. The single allowed write path."""

    index_dir: Path

    def __post_init__(self) -> None:
        self.index_dir.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        return self.index_dir / _RECORDS_FILE

    def append(self, record: RunMemoryRecord) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record.to_jsonl(), sort_keys=True) + "\n")

    def truncate(self) -> None:
        """Clear the index. Used by rebuild only."""
        self.path.write_text("", encoding="utf-8")


# ---------------------------------------------------------------------------
# Query service
# ---------------------------------------------------------------------------


_TEXT_FIELDS = ("summary", "tags", "artifact_paths", "repo_id", "run_id")


@dataclass
class RunMemoryQueryService:
    """Read-only query layer. Loads records lazily on each query."""

    index_dir: Path

    @property
    def path(self) -> Path:
        return self.index_dir / _RECORDS_FILE

    def _iter_records(self) -> Iterator[RunMemoryRecord]:
        if not self.path.exists():
            return
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            yield RunMemoryRecord.from_jsonl(json.loads(line))

    def query(self, q: RunMemoryQuery) -> list[RunMemoryRecord]:
        results: list[RunMemoryRecord] = []
        for rec in self._iter_records():
            if not _matches(rec, q):
                continue
            results.append(rec)
        # Stable order: created_at then record_id (ties).
        results.sort(key=lambda r: (r.created_at, r.record_id))
        return results

    def all(self) -> list[RunMemoryRecord]:
        return self.query(RunMemoryQuery())


def _matches(rec: RunMemoryRecord, q: RunMemoryQuery) -> bool:
    if q.repo_id is not None and rec.repo_id != q.repo_id:
        return False
    if q.run_id is not None and rec.run_id != q.run_id:
        return False
    if q.request_id is not None and rec.request_id != q.request_id:
        return False
    if q.result_id is not None and rec.result_id != q.result_id:
        return False
    if q.status is not None and rec.status != q.status:
        return False
    if q.contract_kind is not None and q.contract_kind not in rec.contract_kinds:
        return False
    if q.tag is not None and q.tag not in rec.tags:
        return False
    if q.time_range is not None:
        start, end = q.time_range
        try:
            ts = datetime.fromisoformat(rec.created_at.replace("Z", "+00:00"))
        except ValueError:
            return False
        if ts < start or ts > end:
            return False
    if q.text is not None:
        needle = q.text.lower()
        haystack_parts: list[str] = [
            rec.summary or "",
            rec.repo_id or "",
            rec.run_id or "",
        ]
        haystack_parts.extend(rec.tags)
        haystack_parts.extend(rec.artifact_paths)
        haystack = " | ".join(haystack_parts).lower()
        if needle not in haystack:
            return False
    return True


# ---------------------------------------------------------------------------
# Single write site (callers in OperationsCenter use this only)
# ---------------------------------------------------------------------------


def record_execution_result(
    result,  # operations_center.contracts.execution.ExecutionResult
    index_dir: Path,
    *,
    repo_id: str | None = None,
    artifact_paths: Iterable[str] = (),
    contract_kinds: Iterable[str] = (),
    tags: Iterable[str] = (),
    summary: str | None = None,
) -> RunMemoryRecord:
    """Index a finalized ``ExecutionResult``. Idempotent: re-calling with
    the same result emits a new line but the ``record_id`` matches, so a
    subsequent rebuild deduplicates by id.

    The single allowed call site lives in OperationsCenter post-finalize.
    """
    rec = _record_from_result(
        result,
        repo_id=repo_id,
        artifact_paths=tuple(artifact_paths),
        contract_kinds=tuple(contract_kinds),
        tags=tuple(tags),
        summary=summary,
    )
    RunMemoryIndexWriter(index_dir).append(rec)
    return rec


def _record_from_result(
    result,
    *,
    repo_id: str | None,
    artifact_paths: tuple[str, ...],
    contract_kinds: tuple[str, ...],
    tags: tuple[str, ...],
    summary: str | None,
) -> RunMemoryRecord:
    # Tolerant accessor: works with Pydantic models or dicts.
    def _get(field: str, default=None):
        if hasattr(result, field):
            return getattr(result, field)
        if isinstance(result, dict):
            return result.get(field, default)
        return default

    run_id = str(_get("run_id"))
    request_id = str(_get("proposal_id") or _get("request_id") or "")
    decision_id = str(_get("decision_id") or _get("lane_decision_id") or "")
    # ``result_id`` isn't a field on OC's Pydantic ExecutionResult — derive
    # a stable composite if absent.
    result_id = str(_get("result_id") or f"{run_id}::{decision_id or 'no-decision'}")
    status = _get("status")
    if hasattr(status, "value"):
        status = status.value
    status = str(status or "unknown")

    auto_summary = summary
    if auto_summary is None:
        reason = _get("failure_reason")
        auto_summary = reason if reason else f"{status}: run {run_id}"

    return RunMemoryRecord(
        record_id=deterministic_record_id(result_id),
        run_id=run_id,
        request_id=request_id,
        result_id=result_id,
        repo_id=repo_id,
        artifact_paths=tuple(artifact_paths),
        contract_kinds=tuple(contract_kinds),
        status=status,
        summary=auto_summary,
        tags=tuple(tags),
        created_at=datetime.now(tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        source_type=SourceType.EXECUTION_RESULT,
    )


# ---------------------------------------------------------------------------
# Rebuild from on-disk ExecutionResult artifacts
# ---------------------------------------------------------------------------


def rebuild_index_from_artifacts(
    artifacts_dir: Path,
    index_dir: Path,
) -> int:
    """Scan ``artifacts_dir`` for ``execution_result*.json`` files and
    regenerate the JSONL index. Returns the number of records written.

    The only v1 source: persisted ``ExecutionResult`` JSON artifacts. Files
    not matching the naming pattern are skipped.
    """
    writer = RunMemoryIndexWriter(index_dir)
    writer.truncate()
    seen: set[str] = set()
    count = 0
    for path in _iter_result_artifacts(artifacts_dir):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        record = _record_from_result(
            data,
            repo_id=data.get("repo_id"),
            artifact_paths=(str(path),),
            contract_kinds=(),
            tags=(),
            summary=None,
        )
        if record.record_id in seen:
            continue  # idempotent dedupe within one rebuild
        seen.add(record.record_id)
        writer.append(record)
        count += 1
    return count


def _iter_result_artifacts(artifacts_dir: Path) -> Iterator[Path]:
    if not artifacts_dir.exists():
        return
    stack = [artifacts_dir]
    while stack:
        cur = stack.pop()
        for entry in sorted(cur.iterdir()):
            if entry.is_dir():
                if entry.name in {"__pycache__", ".git"}:
                    continue
                stack.append(entry)
            elif entry.suffix == ".json" and entry.name.startswith("execution_result"):
                yield entry
