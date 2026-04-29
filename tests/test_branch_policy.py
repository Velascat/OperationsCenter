# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
from operations_center.adapters.git import branch_allowed


def test_branch_allowed_with_glob() -> None:
    assert branch_allowed("feature/my-work", ["main", "feature/*"])
    assert not branch_allowed("hotfix/x", ["main", "feature/*"])
