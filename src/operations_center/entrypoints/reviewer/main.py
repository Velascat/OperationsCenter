# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Reviewer entrypoint — delegates to pr_review_watcher.main.

This shim exists so operations-center.sh can continue to address the review
role as `reviewer.main` while the actual implementation lives in the
pr_review_watcher package, which can be tested independently.
"""
from __future__ import annotations

import sys

from operations_center.entrypoints.pr_review_watcher.main import main

if __name__ == "__main__":
    sys.exit(main())
