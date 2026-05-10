# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Post-dispatch coverage analysis bridge.

After a managed audit dispatch finishes successfully, the consuming repo
(e.g. ExampleManagedRepo's representative pipeline) writes ``coverage.json`` into
its bucket directory. This module locates that file via the Phase 7 artifact
index and invokes Custodian's ``custodian audit --enable-coverage`` against
the consuming repo's working tree to produce CV1/CV2/CV3 findings.

OperationsCenter never imports Custodian as a Python module — Custodian is a
sibling tool with its own venv and dependencies. The bridge is subprocess-only
(matches how dispatch already shells out to VF).
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from collections.abc import Iterable
from pathlib import Path

from operations_center.artifact_index import (
    ManifestInvalidError,
    ManifestNotFoundError,
    build_artifact_index,
    load_artifact_manifest,
)

from .models import CoverageAuditSummary

logger = logging.getLogger(__name__)

_COVERAGE_RULES = ("CV1_MODULE_UNEXECUTED", "CV2_FUNCTION_UNEXECUTED", "CV3_MODULE_BELOW_MIN_COVERAGE")
_DEFAULT_TIMEOUT_S = 120
_MAX_SAMPLES = 6


def _find_coverage_json(artifact_manifest_path: str | Path) -> Path | None:
    """Locate ``coverage.json`` within the dispatch result's artifact set.

    Uses the Phase 7 single-manifest index. Returns the resolved absolute
    path, or None if no entry whose path ends in ``coverage.json`` is present.
    """
    mp = Path(artifact_manifest_path)
    try:
        manifest = load_artifact_manifest(mp)
    except (ManifestNotFoundError, ManifestInvalidError) as exc:
        logger.warning("coverage_analysis_manifest_unreadable", extra={"path": str(mp), "error": str(exc)})
        return None

    index = build_artifact_index(manifest, mp)
    for entry in index.artifacts:
        if entry.path.endswith("coverage.json") and entry.resolved_path is not None:
            return entry.resolved_path
    return None


def _summarize(stdout: str, exit_code: int, coverage_json_path: Path | None) -> CoverageAuditSummary:
    """Parse Custodian's --json output and produce a compact summary."""
    cv_counts = {rule: 0 for rule in _COVERAGE_RULES}
    samples: list[str] = []
    total = 0

    try:
        payload = json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        return CoverageAuditSummary(
            coverage_json_path=str(coverage_json_path) if coverage_json_path else None,
            custodian_exit_code=exit_code,
            error="custodian stdout was not valid JSON",
        )

    patterns = (payload.get("patterns") or {})
    cov_block = patterns.get("COVERAGE")
    if cov_block:
        # Custodian wraps adapter findings under one COVERAGE pattern entry.
        # cov_block["count"] is the total; cov_block["samples"] include the rule-tagged messages.
        total = int(cov_block.get("count") or 0)
        for sample in (cov_block.get("samples") or []):
            for rule in _COVERAGE_RULES:
                if rule in sample:
                    cv_counts[rule] += 1
                    break
            if len(samples) < _MAX_SAMPLES:
                samples.append(sample)

    return CoverageAuditSummary(
        coverage_json_path=str(coverage_json_path) if coverage_json_path else None,
        custodian_exit_code=exit_code,
        findings_total=total,
        cv1_count=cv_counts["CV1_MODULE_UNEXECUTED"],
        cv2_count=cv_counts["CV2_FUNCTION_UNEXECUTED"],
        cv3_count=cv_counts["CV3_MODULE_BELOW_MIN_COVERAGE"],
        sample_findings=samples,
    )


def run_post_dispatch_coverage_audit(
    *,
    artifact_manifest_path: str | Path,
    consuming_repo_root: str | Path,
    custodian_executable: str | None = None,
    timeout_seconds: int = _DEFAULT_TIMEOUT_S,
    extra_argv: Iterable[str] = (),
) -> CoverageAuditSummary:
    """Invoke ``custodian audit --enable-coverage`` against the consuming repo.

    Parameters
    ----------
    artifact_manifest_path:
        Path to the dispatch's ``artifact_manifest.json`` (returned by
        ``dispatch_managed_audit``). Used to locate ``coverage.json`` via
        the Phase 7 artifact index.
    consuming_repo_root:
        Working tree of the repo Custodian should audit (e.g. the VF repo).
        Custodian reads its own ``.custodian.yaml`` from here; the
        ``--enable-coverage`` flag overlays the coverage adapter onto that
        config.
    custodian_executable:
        Override path to the Custodian binary. Defaults to ``custodian`` on
        PATH.
    timeout_seconds:
        Subprocess wall-clock timeout.
    extra_argv:
        Additional flags to forward (e.g. ``--no-color``).

    Returns
    -------
    CoverageAuditSummary
        Always returned. ``error`` is populated for unhealthy outcomes (no
        coverage.json, custodian unavailable, timeout, malformed JSON).
        Never raises.
    """
    repo_root = Path(consuming_repo_root)
    coverage_json = _find_coverage_json(artifact_manifest_path)
    if coverage_json is None:
        return CoverageAuditSummary(
            coverage_json_path=None,
            error="coverage.json not found in dispatch artifact manifest",
        )

    binary = custodian_executable or shutil.which("custodian")
    if binary is None:
        return CoverageAuditSummary(
            coverage_json_path=str(coverage_json),
            error="custodian executable not found on PATH",
        )

    argv = [
        binary,
        "audit",
        "--repo", str(repo_root),
        "--enable-coverage",
        "--coverage-json", str(coverage_json),
        "--json",
        "--no-color",
        *extra_argv,
    ]
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return CoverageAuditSummary(
            coverage_json_path=str(coverage_json),
            error=f"custodian audit timed out after {timeout_seconds}s",
        )
    except FileNotFoundError as exc:
        return CoverageAuditSummary(
            coverage_json_path=str(coverage_json),
            error=f"custodian executable not invokable: {exc}",
        )

    return _summarize(proc.stdout, proc.returncode, coverage_json)


__all__ = [
    "run_post_dispatch_coverage_audit",
]
