"""Detect cross-repo impact when a goal touches a shared-interface path.

Cited by `docs/design/autonomy_gaps.md` S7-6 (Cross-Repo Impact Analysis).

Each repo's settings carry an `impact_report_paths` list (already exists
on `RepoSettings` as a deferred field). When a goal task in repo A
touches a path under that prefix, neighbour repos that *consume* that
interface should be warned — there's a cross-repo coordination need.

This module provides ``_check_cross_repo_impact`` — a pure function over
(changed_files, all_repo_settings) that returns the set of repos whose
declared interfaces intersect the changed files.

Invariants: pure function. No Plane calls, no notifications. Caller
decides what to do with the result (post a comment, file a ticket,
publish an alert).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CrossRepoImpact:
    """A repo whose declared shared paths intersect the changed file set."""
    repo_key: str
    matched_paths: tuple[str, ...]   # the path-prefix(es) that matched
    changed_files: tuple[str, ...]   # files that fell under those prefixes


def _normalize(path: str) -> str:
    """Posix-style normalisation for cross-OS path comparison."""
    return Path(path).as_posix().lstrip("./")


def _check_cross_repo_impact(
    changed_files: list[str],
    *,
    repos: dict[str, Any],
    source_repo_key: str | None = None,
) -> list[CrossRepoImpact]:
    """Return repos other than *source_repo_key* whose impact_report_paths match.

    *changed_files* is the file list the goal task produced (relative to
    *source_repo_key*'s root). *repos* is a {repo_key: RepoSettings}-style
    mapping. *source_repo_key* is excluded from the result (a repo doesn't
    impact itself).

    Empty list when nothing crosses a declared interface.
    """
    if not changed_files or not repos:
        return []
    cand = [_normalize(f) for f in changed_files if f]
    out: list[CrossRepoImpact] = []
    for rk, rcfg in repos.items():
        if rk == source_repo_key:
            continue
        prefixes = list(getattr(rcfg, "impact_report_paths", []) or [])
        if not prefixes:
            continue
        matched_prefixes: list[str] = []
        matched_files: list[str] = []
        for prefix in prefixes:
            norm_prefix = _normalize(str(prefix)).rstrip("/")
            if not norm_prefix:
                continue
            files_in = [c for c in cand if c == norm_prefix or c.startswith(norm_prefix + "/")]
            if files_in:
                matched_prefixes.append(prefix)
                matched_files.extend(files_in)
        if matched_prefixes:
            out.append(CrossRepoImpact(
                repo_key=rk,
                matched_paths=tuple(sorted(set(matched_prefixes))),
                changed_files=tuple(sorted(set(matched_files))),
            ))
    return out
