# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path


class WorkspaceManager:
    def __init__(self, prefix: str = "oc-task-") -> None:
        self.prefix = prefix

    def create(self) -> Path:
        return Path(tempfile.mkdtemp(prefix=self.prefix))

    def cleanup(self, path: Path) -> None:
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
