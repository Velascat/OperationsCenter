# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Multi-run artifact index — Phase 7 historical query layer.

Walks a directory of past managed-audit runs, loads every
``artifact_manifest.json`` it finds, and exposes a queryable structure on
top of the existing single-manifest API. Failed loads are recorded as
``IndexedRun`` entries with ``load_error`` populated rather than raised, so
the index is robust against partial / corrupt / racing-delete buckets.

Discovery is filesystem-driven only — the dispatch lock store
(``audit_dispatch.lock_store``) describes *active* runs and is not consulted
here. This module serves *historical* queries.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from operations_center.audit_contracts.vocabulary import ManifestStatus, RunStatus

from .errors import (
    ArtifactNotFoundError,
    ArtifactPathUnresolvableError,
    ManifestInvalidError,
    ManifestNotFoundError,
)
from .index import build_artifact_index
from .loader import load_artifact_manifest
from .models import ArtifactQuery, IndexedArtifact, ManagedArtifactIndex
from .query import query_artifacts
from .retrieval import resolve_artifact_path

_MANIFEST_FILENAME = "artifact_manifest.json"
_SKIP_DIRS = frozenset({"__pycache__", "node_modules", ".git", ".venv"})


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def discover_manifest_files(
    search_root: Path | str,
    *,
    max_depth: int = 6,
    follow_symlinks: bool = False,
) -> list[Path]:
    """Walk ``search_root`` and return every directory's ``artifact_manifest.json``.

    Hidden dirs (starting with ``.``) and noise dirs (``__pycache__`` etc.) are
    skipped. Depth is bounded relative to ``search_root`` so a typo doesn't
    walk the whole filesystem.
    """
    root = Path(search_root)
    if not root.is_dir():
        return []

    out: list[Path] = []
    root_parts_len = len(root.resolve().parts)

    for dirpath, dirnames, filenames in os.walk(root, followlinks=follow_symlinks):
        # Depth-prune.
        cur_depth = len(Path(dirpath).resolve().parts) - root_parts_len
        if cur_depth >= max_depth:
            dirnames.clear()

        # Filter children in-place.
        dirnames[:] = [
            d for d in dirnames if not d.startswith(".") and d not in _SKIP_DIRS
        ]

        if _MANIFEST_FILENAME in filenames:
            out.append(Path(dirpath) / _MANIFEST_FILENAME)

    return sorted(out)


# ---------------------------------------------------------------------------
# Indexed run + multi-run index
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IndexedRun:
    """One past audit run discovered by the multi-run indexer.

    On successful load, ``index`` carries the full per-run ManagedArtifactIndex
    and ``load_error`` is None. On failure, ``index`` is None and ``load_error``
    contains the reason — the run is still listed so operators can see what
    failed.
    """

    manifest_path: Path
    run_id: str
    repo_id: str
    audit_type: str
    producer: str
    run_status: RunStatus | None
    manifest_status: ManifestStatus | None
    finalized_at: datetime | None
    artifact_count: int
    is_partial: bool
    load_error: str | None
    index: ManagedArtifactIndex | None

    @property
    def loaded(self) -> bool:
        return self.index is not None and self.load_error is None


@dataclass
class MultiRunArtifactIndex:
    """Federated index over many past audit runs.

    Built by ``build_multi_run_index``. Provides per-run lookup, cross-run
    querying, and per-artifact resolution with optional fresh existence
    revalidation.
    """

    search_root: Path
    runs: list[IndexedRun]
    skipped_paths: list[tuple[Path, str]] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Run lookup
    # ------------------------------------------------------------------

    def get_run(self, run_id: str) -> IndexedRun | None:
        """Return a run by exact ``run_id``."""
        return next((r for r in self.runs if r.run_id == run_id), None)

    def find_run_by_prefix(self, prefix: str) -> IndexedRun:
        """Resolve a run by exact id or unique prefix (git-style).

        Raises ``ArtifactNotFoundError`` if no match, or ``ValueError`` with
        the candidate list if the prefix is ambiguous.
        """
        if not prefix:
            raise ArtifactNotFoundError("empty run_id prefix")
        # Exact match wins.
        exact = self.get_run(prefix)
        if exact is not None:
            return exact
        candidates = [r for r in self.runs if r.run_id.startswith(prefix)]
        if not candidates:
            raise ArtifactNotFoundError(
                f"no run with run_id prefix {prefix!r} (have {len(self.runs)} runs)"
            )
        if len(candidates) > 1:
            raise ValueError(
                f"run_id prefix {prefix!r} is ambiguous; matches "
                f"{len(candidates)} runs: {[r.run_id for r in candidates]}"
            )
        return candidates[0]

    def by_repo(self, repo_id: str) -> list[IndexedRun]:
        return [r for r in self.runs if r.repo_id == repo_id]

    def by_audit_type(self, audit_type: str) -> list[IndexedRun]:
        return [r for r in self.runs if r.audit_type == audit_type]

    @property
    def loaded_runs(self) -> list[IndexedRun]:
        return [r for r in self.runs if r.loaded]

    @property
    def failed_runs(self) -> list[IndexedRun]:
        return [r for r in self.runs if not r.loaded]

    # ------------------------------------------------------------------
    # Cross-run query
    # ------------------------------------------------------------------

    def query(self, query: ArtifactQuery | None = None) -> list[IndexedArtifact]:
        """Federate ``query_artifacts`` across every successfully-loaded run."""
        out: list[IndexedArtifact] = []
        for run in self.loaded_runs:
            assert run.index is not None
            out.extend(query_artifacts(run.index, query))
        return out

    def iter_artifacts(self) -> Iterator[IndexedArtifact]:
        for run in self.loaded_runs:
            assert run.index is not None
            yield from run.index.artifacts

    # ------------------------------------------------------------------
    # Resolve
    # ------------------------------------------------------------------

    def resolve(
        self,
        run_id: str,
        artifact_id: str,
        *,
        recheck_exists: bool = True,
    ) -> Path:
        """Resolve ``(run_id, artifact_id)`` to an absolute path on disk.

        Set ``recheck_exists=False`` if the caller is about to do their own
        existence check and wants to avoid the extra stat. Default is True so
        callers about to ``read_text()`` get a clean error if the file is gone.
        """
        run = self.get_run(run_id)
        if run is None:
            raise ArtifactNotFoundError(
                f"no run with run_id {run_id!r} in index (search_root={self.search_root})"
            )
        if run.index is None:
            raise ArtifactPathUnresolvableError(
                f"run {run_id!r} failed to load: {run.load_error}"
            )
        path = resolve_artifact_path(run.index, artifact_id)
        if recheck_exists and not path.exists():
            raise ArtifactPathUnresolvableError(
                f"artifact {artifact_id!r} for run {run_id!r} resolved to "
                f"{path} but file no longer exists on disk"
            )
        return path


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


def _peek_manifest_metadata(manifest_path: Path) -> dict[str, Any] | None:
    """Read minimal manifest metadata without full pydantic validation.

    Used as a fallback so we can populate IndexedRun fields even when the
    full manifest fails strict validation.
    """
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _coerce_status(value: Any, enum_cls):
    if value is None:
        return None
    try:
        return enum_cls(value)
    except (ValueError, TypeError):
        return None


def _build_one(
    manifest_path: Path,
    *,
    repo_root: Path | None,
) -> IndexedRun:
    abs_path = manifest_path.resolve()
    try:
        manifest = load_artifact_manifest(abs_path)
    except ManifestNotFoundError as exc:
        # File raced away between discovery and load — record but don't crash.
        return IndexedRun(
            manifest_path=abs_path,
            run_id="",
            repo_id="",
            audit_type="",
            producer="",
            run_status=None,
            manifest_status=None,
            finalized_at=None,
            artifact_count=0,
            is_partial=False,
            load_error=str(exc),
            index=None,
        )
    except ManifestInvalidError as exc:
        meta = _peek_manifest_metadata(abs_path) or {}
        return IndexedRun(
            manifest_path=abs_path,
            run_id=str(meta.get("run_id", "")),
            repo_id=str(meta.get("repo_id", "")),
            audit_type=str(meta.get("audit_type", "")),
            producer=str(meta.get("producer", "")),
            run_status=_coerce_status(meta.get("run_status"), RunStatus),
            manifest_status=_coerce_status(meta.get("manifest_status"), ManifestStatus),
            finalized_at=_parse_iso(meta.get("finalized_at")),
            artifact_count=len(meta.get("artifacts") or []),
            is_partial=meta.get("manifest_status") == "partial",
            load_error=str(exc),
            index=None,
        )

    index = build_artifact_index(manifest, abs_path, repo_root=repo_root)
    return IndexedRun(
        manifest_path=abs_path,
        run_id=manifest.run_id,
        repo_id=manifest.repo_id,
        audit_type=manifest.audit_type,
        producer=manifest.producer,
        run_status=manifest.run_status,
        manifest_status=manifest.manifest_status,
        finalized_at=manifest.finalized_at,
        artifact_count=len(manifest.artifacts),
        is_partial=manifest.manifest_status == ManifestStatus.PARTIAL,
        load_error=None,
        index=index,
    )


def build_multi_run_index(
    search_root: Path | str,
    *,
    repo_root: Path | str | None = None,
    repo_filter: str | None = None,
    audit_type_filter: str | None = None,
    max_depth: int = 6,
) -> MultiRunArtifactIndex:
    """Discover and index every ``artifact_manifest.json`` under ``search_root``.

    Parameters
    ----------
    search_root:
        Directory to walk. Each directory containing an ``artifact_manifest.json``
        is treated as one bucket.
    repo_root:
        Optional shared repo root forwarded to ``build_artifact_index`` for all
        runs. If omitted, each run derives its own.
    repo_filter / audit_type_filter:
        Post-load filters on manifest metadata. Runs that don't match are
        omitted from the index entirely (not recorded in skipped_paths).
    max_depth:
        Maximum directory depth (relative to ``search_root``) to walk. Default 4
        is enough for the typical ``tools/audit/report/<audit_type>/<bucket>/``
        layout.
    """
    root = Path(search_root)
    manifest_paths = discover_manifest_files(root, max_depth=max_depth)

    runs: list[IndexedRun] = []
    skipped: list[tuple[Path, str]] = []
    repo_root_p = Path(repo_root) if repo_root is not None else None

    for mp in manifest_paths:
        if not mp.is_file():
            skipped.append((mp, "manifest disappeared between discovery and load"))
            continue

        run = _build_one(mp, repo_root=repo_root_p)

        if repo_filter is not None and run.repo_id and run.repo_id != repo_filter:
            continue
        if audit_type_filter is not None and run.audit_type and run.audit_type != audit_type_filter:
            continue

        runs.append(run)

    # Sort: most recently finalized first, then by run_id for determinism.
    runs.sort(
        key=lambda r: (
            -(r.finalized_at.timestamp() if r.finalized_at else 0),
            r.run_id,
        )
    )

    return MultiRunArtifactIndex(
        search_root=root.resolve(),
        runs=runs,
        skipped_paths=skipped,
    )


__all__ = [
    "IndexedRun",
    "MultiRunArtifactIndex",
    "build_multi_run_index",
    "discover_manifest_files",
]
