# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from operations_center.observer.models import BacklogItem, BacklogSignal

if TYPE_CHECKING:
    from operations_center.observer.service import ObserverContext

# Types that are never auto-promoted — require deliberate operator action
_BLOCKED_TYPES: frozenset[str] = frozenset({"arch", "redesign"})

_TYPE_RE = re.compile(r"^\*\*Type\*\*:\s*(\S+)", re.IGNORECASE)


def collect_backlog(repo_path: Path, backlog_rel: str = "docs/backlog.md") -> BacklogSignal:
    """Parse the ## Next section of the repo's backlog doc and return promotable items."""
    backlog_path = repo_path / backlog_rel
    if not backlog_path.exists():
        return BacklogSignal()

    text = backlog_path.read_text(encoding="utf-8", errors="replace")
    return _parse_backlog(text)


def _parse_backlog(text: str) -> BacklogSignal:
    # Find the ## Next section — stop at the next ## heading
    next_match = re.search(r"^## Next\s*$", text, re.MULTILINE)
    if not next_match:
        return BacklogSignal()

    section_start = next_match.end()
    next_section = re.search(r"^## ", text[section_start:], re.MULTILINE)
    section_text = text[section_start: section_start + next_section.start()] if next_section else text[section_start:]

    items: list[BacklogItem] = []
    # Split on ### headings
    chunks = re.split(r"^### ", section_text, flags=re.MULTILINE)
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue
        lines = chunk.splitlines()
        title = lines[0].strip()
        if not title or title.startswith("#"):
            continue

        item_type = "feature"  # default if not tagged
        description_lines: list[str] = []
        for line in lines[1:]:
            type_match = _TYPE_RE.match(line.strip())
            if type_match:
                item_type = type_match.group(1).lower()
            elif line.strip() and not line.strip().startswith("**"):
                description_lines.append(line.strip())

        items.append(BacklogItem(
            title=title,
            item_type=item_type,
            description=" ".join(description_lines)[:300],
        ))

    return BacklogSignal(items=items)


def promotable_items(signal: BacklogSignal) -> list[BacklogItem]:
    """Return only items eligible for auto-promotion (not arch/redesign)."""
    return [item for item in signal.items if item.item_type not in _BLOCKED_TYPES]


class BacklogCollector:
    """ObserverContext-compatible wrapper around collect_backlog."""

    def collect(self, context: "ObserverContext") -> BacklogSignal:
        return collect_backlog(context.repo_path)
