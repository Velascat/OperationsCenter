from __future__ import annotations

import shutil
import tempfile
from pathlib import Path


class WorkspaceManager:
    def __init__(self, prefix: str = "cp-task-") -> None:
        self.prefix = prefix

    def create(self) -> Path:
        return Path(tempfile.mkdtemp(prefix=self.prefix))

    def cleanup(self, path: Path) -> None:
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
