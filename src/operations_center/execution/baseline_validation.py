# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Baseline validation step — run repo's validation_commands before kodo.

Wires the previously-dead RepoSettings fields:

  • validation_commands       (list of shell commands to run)
  • validation_timeout_seconds (per-repo timeout, default 300)
  • skip_baseline_validation  (per-repo opt-out)

What this *is*: a callable function the WorkspaceManager can run after
the clone but before kodo. It executes each configured command in
sequence; first non-zero exit aborts the chain. Returns a
ValidationSummary the caller embeds in the eventual ExecutionResult.

What this is NOT: post-execution validation (run after kodo). That's a
separate concern — the existing kodo capture already records exit codes;
adding a post-validation gate is its own design decision (which test
failures attribute to kodo's changes vs. pre-existing? — same conundrum
as F10 intent verification).

Invariants:
  • Read-only of settings; no mutation
  • Returns a ValidationSummary; doesn't decide to abort the run
  • Caller (coordinator) decides whether to proceed based on the summary
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any

from operations_center.contracts.common import ValidationSummary
from operations_center.contracts.enums import ValidationStatus

logger = logging.getLogger(__name__)


def run_baseline_validation(
    workspace_path: Path,
    *,
    repo_cfg: Any,
) -> ValidationSummary:
    """Execute *repo_cfg*.validation_commands sequentially in *workspace_path*.

    Returns a ValidationSummary:
      • status=PASSED   when every command exits 0
      • status=FAILED   on first non-zero exit (subsequent commands skipped)
      • status=SKIPPED  when commands list is empty OR skip_baseline_validation=True
      • status=ERROR    when any command exceeds timeout (environmental)

    Honors:
      • RepoSettings.validation_commands       (list[str])
      • RepoSettings.validation_timeout_seconds (int, default 300)
      • RepoSettings.skip_baseline_validation   (bool, default False)
    """
    if repo_cfg is None or getattr(repo_cfg, "skip_baseline_validation", False):
        return ValidationSummary(status=ValidationStatus.SKIPPED)

    commands = list(getattr(repo_cfg, "validation_commands", []) or [])
    if not commands:
        return ValidationSummary(status=ValidationStatus.SKIPPED)

    timeout = int(getattr(repo_cfg, "validation_timeout_seconds", 300) or 300)
    import time
    start = time.monotonic()
    passed = 0

    for cmd in commands:
        if not isinstance(cmd, str) or not cmd.strip():
            continue
        try:
            proc = subprocess.run(
                cmd, shell=True, cwd=workspace_path,
                capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            logger.warning(
                "baseline_validation: %r timed out after %ds in %s",
                cmd, timeout, workspace_path,
            )
            return ValidationSummary(
                status=ValidationStatus.ERROR,
                commands_run=passed + 1,
                commands_passed=passed,
                commands_failed=1,
                failure_excerpt=f"timeout after {timeout}s: {cmd}",
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        if proc.returncode != 0:
            excerpt = (proc.stderr or proc.stdout or "")[:1000]
            logger.warning(
                "baseline_validation: %r failed (exit=%d) in %s",
                cmd, proc.returncode, workspace_path,
            )
            return ValidationSummary(
                status=ValidationStatus.FAILED,
                commands_run=passed + 1,
                commands_passed=passed,
                commands_failed=1,
                failure_excerpt=f"exit={proc.returncode}: {excerpt}"[:1000],
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        passed += 1

    return ValidationSummary(
        status=ValidationStatus.PASSED,
        commands_run=passed,
        commands_passed=passed,
        commands_failed=0,
        duration_ms=int((time.monotonic() - start) * 1000),
    )
