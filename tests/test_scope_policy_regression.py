# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Regression tests for scope policy edge cases (kodo test)."""
from operations_center.application.scope_policy import ChangedFilePolicyChecker


def test_path_traversal_detected():
    """F1: ../etc/passwd should not pass scope check for src/."""
    checker = ChangedFilePolicyChecker()
    violations = checker.find_violations(["src/../etc/passwd"], ["src/"])
    assert violations, "Path traversal should be detected as a violation"


def test_directory_without_trailing_slash():
    """F3: 'src' without trailing slash should match 'src/foo.py'."""
    checker = ChangedFilePolicyChecker()
    violations = checker.find_violations(["src/foo.py"], ["src"])
    assert violations == [], "'src' should match files under src/"
