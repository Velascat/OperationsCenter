# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
from operations_center.application.scope_policy import ChangedFilePolicyChecker


def test_allowed_paths_accepts_in_scope_changes() -> None:
    checker = ChangedFilePolicyChecker()
    violations = checker.find_violations(
        changed_files=["src/workflow/task.py", "tools/audit/report.py"],
        allowed_paths=["src/workflow/", "tools/audit/"],
    )
    assert violations == []


def test_allowed_paths_rejects_out_of_scope_changes() -> None:
    checker = ChangedFilePolicyChecker()
    violations = checker.find_violations(
        changed_files=["src/workflow/task.py", "deployment/docker-compose.yml"],
        allowed_paths=["src/workflow/"],
    )
    assert violations == ["deployment/docker-compose.yml"]
