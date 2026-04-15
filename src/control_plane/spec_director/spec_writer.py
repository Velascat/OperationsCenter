# src/control_plane/spec_director/spec_writer.py
from __future__ import annotations

import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path

from control_plane.spec_director.models import SpecFrontMatter

_DEFAULT_SPECS_DIR = Path("docs/specs")
logger = logging.getLogger(__name__)


class SpecWriter:
    def __init__(self, specs_dir: Path | None = None) -> None:
        self.specs_dir = specs_dir or _DEFAULT_SPECS_DIR

    def write(
        self,
        slug: str,
        spec_text: str,
        workspace_path: Path | None = None,
    ) -> Path:
        """Write spec to canonical location and optionally copy to workspace."""
        self.specs_dir.mkdir(parents=True, exist_ok=True)
        dest = self.specs_dir / f"{slug}.md"
        dest.write_text(spec_text, encoding="utf-8")
        logger.info('{"event": "spec_written", "path": "%s"}', str(dest))

        if workspace_path is not None:
            ws_dest = workspace_path / "docs" / "specs" / f"{slug}.md"
            ws_dest.parent.mkdir(parents=True, exist_ok=True)
            ws_dest.write_text(spec_text, encoding="utf-8")
            logger.info('{"event": "spec_workspace_copy", "path": "%s"}', str(ws_dest))

        return dest

    def archive_expired(self, retention_days: int = 90) -> list[Path]:
        """Move completed/cancelled specs older than retention_days to archive/."""
        archive_dir = self.specs_dir / "archive"
        archived: list[Path] = []
        cutoff = datetime.now(UTC).timestamp() - (retention_days * 86400)

        for spec_file in self.specs_dir.glob("*.md"):
            try:
                fm = SpecFrontMatter.from_spec_text(spec_file.read_text())
            except Exception:
                continue
            if fm.status not in {"complete", "cancelled"}:
                continue
            if fm.created_at:
                try:
                    created = datetime.fromisoformat(fm.created_at).timestamp()
                    if created > cutoff:
                        continue
                except Exception:
                    pass
            archive_dir.mkdir(parents=True, exist_ok=True)
            dest = archive_dir / spec_file.name
            shutil.move(str(spec_file), str(dest))
            archived.append(dest)
            logger.info('{"event": "spec_archived", "slug": "%s"}', fm.slug)

        return archived

    def update_front_matter_status(self, slug: str, status: str) -> None:
        """Update the status field in a spec file's front matter."""
        spec_path = self.specs_dir / f"{slug}.md"
        if not spec_path.exists():
            return
        text = spec_path.read_text()
        updated = text.replace(f"status: active", f"status: {status}", 1)
        spec_path.write_text(updated)
