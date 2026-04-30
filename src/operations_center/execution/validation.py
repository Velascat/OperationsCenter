# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Pre-execution validation helpers.

Cited by `docs/design/autonomy_gaps.md` Wave 2 entries (S3-2, S7-7, etc.).
Each function is a small read-only check; together they form a
pre-flight stage that the coordinator (or a dedicated validator) can
run before invoking kodo.

None of these wire themselves into the pipeline automatically — wiring
is a per-feature decision. They're available as building blocks.

Invariant notes:
  • Read-only — no settings mutation, no contract mutation
  • No imports of behavior_calibration (runtime layer)
  • No routing decisions
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


# ── _check_execution_environment ─────────────────────────────────────────────

@dataclass(frozen=True)
class EnvironmentCheck:
    """Result of a pre-execution environment probe."""
    ok: bool
    missing: tuple[str, ...]
    notes: tuple[str, ...]


def _check_execution_environment(workspace_path: Path, *, required_files: tuple[str, ...] = ()) -> EnvironmentCheck:
    """Verify the workspace looks ready for kodo to run.

    Cheap check — confirms the workspace is a populated git clone, optionally
    that key files exist. Returns a structured result rather than raising
    so the caller decides whether to abort, retry, or push through.
    """
    missing: list[str] = []
    notes: list[str] = []
    ws = Path(workspace_path)
    if not ws.exists():
        return EnvironmentCheck(ok=False, missing=("workspace_path",), notes=())
    if not (ws / ".git").exists():
        missing.append(".git")
    for rf in required_files:
        if not (ws / rf).exists():
            missing.append(rf)
    if not any(ws.iterdir()):
        notes.append("workspace_is_empty")
    return EnvironmentCheck(ok=not missing, missing=tuple(missing), notes=tuple(notes))


# ── _collect_open_pr_files ───────────────────────────────────────────────────

def _collect_open_pr_files(gh_client, owner: str, repo: str, *, exclude_pr: int | None = None) -> dict[int, list[str]]:
    """Return {pr_number: [file_paths]} for every open PR in the repo.

    Caller can use this to surface "another open PR is touching this file"
    context to kodo, so it doesn't unknowingly create conflicting changes.
    Defensive: any per-PR fetch failure is silently dropped (best-effort).
    """
    out: dict[int, list[str]] = {}
    try:
        prs = gh_client.list_open_prs(owner, repo)
    except Exception as exc:
        logger.debug("_collect_open_pr_files: list_open_prs failed — %s", exc)
        return out
    for pr in prs:
        n = pr.get("number")
        if n is None or (exclude_pr is not None and n == exclude_pr):
            continue
        try:
            files = gh_client.list_pr_files(owner, repo, n)
        except Exception:
            files = []
        if files:
            out[int(n)] = list(files)
    return out


# ── _has_conflict_with_active_task ───────────────────────────────────────────

def _has_conflict_with_active_task(
    candidate_paths: list[str],
    open_pr_files: dict[int, list[str]],
    *,
    in_review_pr: int | None = None,
) -> tuple[bool, list[int]]:
    """Three-tier conflict detection — does the candidate's file set overlap an active PR?

    Returns (has_conflict, conflicting_pr_numbers).

    "Active" here means any open PR; the in_review_pr argument lets the
    caller distinguish their own PR from others when self-overlap is
    expected (e.g. a revision pass on the same PR).

    Pure function — no GitHub calls, no settings reads. Caller passes
    the data in.
    """
    cand = {Path(p).as_posix() for p in candidate_paths if p}
    if not cand:
        return False, []
    conflicts: list[int] = []
    for pr_n, files in open_pr_files.items():
        if in_review_pr is not None and pr_n == in_review_pr:
            continue
        pr_set = {Path(f).as_posix() for f in files if f}
        if cand & pr_set:
            conflicts.append(pr_n)
    return bool(conflicts), sorted(conflicts)


# ── build_improve_triage_result ──────────────────────────────────────────────

@dataclass(frozen=True)
class ImproveTriageResult:
    """Structured outcome of an improve-mode kodo run.

    Replaces the loose dict shape that downstream consumers were guessing
    at. Carries the kodo verdict, any structured suggestions, and the
    bookkeeping needed for follow-up task creation.
    """
    success: bool
    summary: str
    suggestions: tuple[dict, ...]
    workspace_path: str
    kodo_exit_code: int


def build_improve_triage_result(
    *,
    success: bool,
    summary: str,
    suggestions: list[dict] | None,
    workspace_path: Path | str,
    kodo_exit_code: int,
) -> ImproveTriageResult:
    """Construct an ImproveTriageResult from the pieces.

    Convenience builder so callers don't have to know the dataclass
    shape; defensive against None / wrong types in the suggestions list.
    """
    valid: list[dict] = []
    for s in suggestions or []:
        if isinstance(s, dict) and s.get("title"):
            valid.append(s)
    return ImproveTriageResult(
        success=bool(success),
        summary=str(summary or "")[:500],
        suggestions=tuple(valid),
        workspace_path=str(workspace_path),
        kodo_exit_code=int(kodo_exit_code),
    )
