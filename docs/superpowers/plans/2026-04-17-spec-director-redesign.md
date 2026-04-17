# Spec Director Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the spec director so campaigns actually complete by adding phase orchestration, removing the backwards revise_spec logic, and wiring queue-drain as the primary autonomous trigger.

**Architecture:** The core new piece is `phase_orchestrator.py` which reads campaign task states from the board and advances implement→test→improve phases automatically. Supporting changes trim dead code (revise_spec, Plane label trigger, insight_snapshot) and simplify the state model. The main loop is reordered to run phase orchestration before trigger detection.

**Tech Stack:** Python 3.11+, Pydantic v2, PlaneClient (internal adapter), `call_claude()` subprocess wrapper (`src/control_plane/spec_director/_claude_cli.py`), pytest.

---

## File Map

| File | Action |
|---|---|
| `src/control_plane/entrypoints/worker/main.py` | Modify — add `"spec-campaign"` to `_AUTO_SOURCES` |
| `src/control_plane/spec_director/phase_orchestrator.py` | **Create** — new module |
| `src/control_plane/spec_director/trigger.py` | Modify — remove Plane label trigger, fix queue drain condition |
| `src/control_plane/spec_director/models.py` | Modify — slim down `CampaignRecord` |
| `src/control_plane/spec_director/state.py` | Modify — remove `update_progress`, `increment_revision_count` |
| `src/control_plane/spec_director/suppressor.py` | Modify — read `area_keywords` from spec front matter |
| `src/control_plane/spec_director/recovery.py` | Modify — remove `revise_spec`, `revision_budget_ok`, `is_stalled` |
| `src/control_plane/spec_director/context_bundle.py` | Modify — remove `insight_snapshot`, add board signals |
| `src/control_plane/spec_director/brainstorm.py` | Modify — update prompt to include available repos |
| `src/control_plane/entrypoints/spec_director/main.py` | Modify — add phase orchestration, new trigger interface |
| `tests/test_spec_campaign_source.py` | **Create** |
| `tests/test_phase_orchestrator.py` | **Create** |
| `tests/test_trigger_detector.py` | **Create** |

---

## Task 1: Fix `_AUTO_SOURCES` so spec-campaign tasks are promoted from Backlog

**Files:**
- Modify: `src/control_plane/entrypoints/worker/main.py:1084`
- Create: `tests/test_spec_campaign_source.py`

- [ ] **Step 1: Write the failing test**

`tests/test_spec_campaign_source.py`:

```python
"""Verify that spec-campaign-sourced Backlog tasks are promoted by promote_backlog_tasks."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from control_plane.entrypoints.worker.main import promote_backlog_tasks


def _make_issue(*, task_id: str, state: str, labels: list[str]) -> dict:
    return {
        "id": task_id,
        "name": f"Task {task_id}",
        "state": {"name": state},
        "labels": [{"name": lbl} for lbl in labels],
        "description": f"## Execution\nrepo: repo_a\nbase_branch: main\nmode: goal\n",
    }


def test_spec_campaign_source_task_is_promoted():
    issue = _make_issue(
        task_id="task-1",
        state="Backlog",
        labels=["task-kind: goal", "source: spec-campaign", "campaign-id: abc-123", "repo: repo_a"],
    )
    client = MagicMock()
    client.transition_issue.return_value = None
    client.comment_issue.return_value = None

    promoted = promote_backlog_tasks(client, [issue], max_promotions=5)

    assert "task-1" in promoted
    client.transition_issue.assert_called_once_with("task-1", "Ready for AI")


def test_non_spec_campaign_task_without_known_source_is_not_promoted():
    issue = _make_issue(
        task_id="task-2",
        state="Backlog",
        labels=["task-kind: goal"],  # no source label, no repo label
    )
    client = MagicMock()
    promoted = promote_backlog_tasks(client, [issue], max_promotions=5)
    assert promoted == []
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd /home/dev/Documents/GitHub/ControlPlane
python -m pytest tests/test_spec_campaign_source.py -v 2>&1 | head -30
```

Expected: `FAILED tests/test_spec_campaign_source.py::test_spec_campaign_source_task_is_promoted`

- [ ] **Step 3: Add `"spec-campaign"` to `_AUTO_SOURCES`**

In `src/control_plane/entrypoints/worker/main.py` at line 1084, change:

```python
        _AUTO_SOURCES = {"proposer", "autonomy", "improve-worker", "reviewer-dep-conflict", "post-merge-ci", "multi-step-plan"}
```

to:

```python
        _AUTO_SOURCES = {"proposer", "autonomy", "improve-worker", "reviewer-dep-conflict", "post-merge-ci", "multi-step-plan", "spec-campaign"}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_spec_campaign_source.py -v
```

Expected: `2 passed`

- [ ] **Step 5: Run the full test suite to check for regressions**

```bash
python -m pytest tests/ -x -q 2>&1 | tail -20
```

Expected: all pass (or existing failures only — baseline before this PR)

- [ ] **Step 6: Commit**

```bash
git add src/control_plane/entrypoints/worker/main.py tests/test_spec_campaign_source.py
git commit -m "fix: add spec-campaign to _AUTO_SOURCES so implement tasks promote from Backlog"
```

---

## Task 2: Slim down `CampaignRecord` and update suppressor

The spec says: board is ground truth for phase state; local JSON is a thin index. `area_keywords` moves from the state file to being read on demand from the spec front matter. `last_progress_at`, `spec_revision_count`, and `trigger_source` are only used by code we're removing in Task 6.

**Files:**
- Modify: `src/control_plane/spec_director/models.py`
- Modify: `src/control_plane/spec_director/state.py`
- Modify: `src/control_plane/spec_director/suppressor.py`

- [ ] **Step 1: Write a failing test for suppressor reading from spec file**

Add to `tests/test_spec_campaign_source.py`:

```python
def test_suppressor_reads_area_keywords_from_spec_file(tmp_path):
    """Suppressor must work even when CampaignRecord has no area_keywords."""
    from control_plane.spec_director.suppressor import is_suppressed
    from control_plane.spec_director.models import CampaignRecord

    # Write a spec file with area_keywords
    specs_dir = tmp_path / "docs" / "specs"
    specs_dir.mkdir(parents=True)
    spec_path = specs_dir / "my-slug.md"
    spec_path.write_text("""\
---
campaign_id: test-uuid-1234
slug: my-slug
phases:
  - implement
repos:
  - repo_a
area_keywords:
  - src/auth/
  - authentication
status: active
created_at: 2026-01-01T00:00:00
---
## Overview
A test spec.
""")

    record = CampaignRecord(
        campaign_id="test-uuid-1234",
        slug="my-slug",
        spec_file=str(spec_path),
        status="active",
        created_at="2026-01-01T00:00:00",
    )

    suppressed = is_suppressed(
        proposal_title="Refactor authentication module",
        proposal_paths=["src/auth/login.py"],
        active_campaigns=[record],
        specs_dir=specs_dir,
    )
    assert suppressed is True


def test_suppressor_not_suppressed_when_no_keyword_match(tmp_path):
    from control_plane.spec_director.suppressor import is_suppressed
    from control_plane.spec_director.models import CampaignRecord

    specs_dir = tmp_path / "docs" / "specs"
    specs_dir.mkdir(parents=True)
    spec_path = specs_dir / "my-slug.md"
    spec_path.write_text("""\
---
campaign_id: test-uuid-1234
slug: my-slug
phases: [implement]
repos: [repo_a]
area_keywords:
  - src/auth/
status: active
created_at: 2026-01-01T00:00:00
---
""")

    record = CampaignRecord(
        campaign_id="test-uuid-1234",
        slug="my-slug",
        spec_file=str(spec_path),
        status="active",
        created_at="2026-01-01T00:00:00",
    )

    suppressed = is_suppressed(
        proposal_title="Add logging to database queries",
        proposal_paths=["src/db/queries.py"],
        active_campaigns=[record],
        specs_dir=specs_dir,
    )
    assert suppressed is False
```

- [ ] **Step 2: Run to verify it fails**

```bash
python -m pytest tests/test_spec_campaign_source.py::test_suppressor_reads_area_keywords_from_spec_file -v 2>&1 | head -30
```

Expected: fails because `CampaignRecord` still requires `area_keywords` and `is_suppressed` has wrong signature

- [ ] **Step 3: Update `CampaignRecord` in `models.py`**

Replace the `CampaignRecord` class:

```python
class CampaignRecord(BaseModel):
    campaign_id: str
    slug: str
    spec_file: str
    status: Literal["active", "complete", "cancelled", "partial"]
    created_at: str
```

Remove `area_keywords`, `last_progress_at`, `spec_revision_count`, `trigger_source`.

Remove `TriggerSource` enum (no longer stored in record; it is still used in `trigger.py` for the result type — keep it there, just remove from CampaignRecord).

- [ ] **Step 4: Update `state.py` — remove unused methods**

Remove the following methods from `CampaignStateManager`:
- `update_progress()` (referenced `last_progress_at`)
- `increment_revision_count()` (referenced `spec_revision_count`)

Also remove the `rebuild_from_specs()` method (it populated `area_keywords` which is now gone; rebuild logic moves to `load()` via corrupt-state fallback which already exists).

Update `rebuild_from_specs()` to not set `area_keywords`:

```python
    def rebuild_from_specs(self, specs_dir: Path) -> ActiveCampaigns:
        """Rebuild active campaigns list by scanning spec front matter."""
        from control_plane.spec_director.models import SpecFrontMatter
        campaigns = []
        for spec_file in sorted(specs_dir.glob("*.md")):
            try:
                fm = SpecFrontMatter.from_spec_text(spec_file.read_text())
                if fm.status == "active":
                    campaigns.append(CampaignRecord(
                        campaign_id=fm.campaign_id,
                        slug=fm.slug,
                        spec_file=str(spec_file),
                        status="active",
                        created_at=fm.created_at,
                    ))
            except Exception as exc:
                logger.warning(
                    '{"event": "spec_rebuild_skip", "file": "%s", "error": "%s"}',
                    str(spec_file), str(exc),
                )
                continue
        rebuilt = ActiveCampaigns(campaigns=campaigns)
        self.save(rebuilt)
        return rebuilt
```

- [ ] **Step 5: Update `suppressor.py` to read `area_keywords` from spec file**

Read the current `suppressor.py`:

```bash
cat src/control_plane/spec_director/suppressor.py
```

Replace the `is_suppressed` function signature and implementation:

```python
# src/control_plane/spec_director/suppressor.py
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from control_plane.spec_director.models import CampaignRecord

logger = logging.getLogger(__name__)


def is_suppressed(
    proposal_title: str,
    proposal_paths: list[str],
    active_campaigns: list["CampaignRecord"] | None = None,
    specs_dir: Path | None = None,
) -> bool:
    """Return True if an active campaign covers the given proposal's area.

    area_keywords are loaded from each campaign's spec front matter.
    Falls back gracefully if the spec file is missing or unparseable.
    """
    if not active_campaigns:
        return False
    for campaign in active_campaigns:
        keywords = _load_area_keywords(campaign, specs_dir)
        if _any_keyword_matches(keywords, proposal_title, proposal_paths):
            logger.info(
                '{"event": "spec_suppressed", "campaign_id": "%s", "reason": "active_spec_campaign"}',
                campaign.campaign_id,
            )
            return True
    return False


def _load_area_keywords(campaign: "CampaignRecord", specs_dir: Path | None) -> list[str]:
    """Load area_keywords from the campaign's spec front matter."""
    spec_path = Path(campaign.spec_file)
    if specs_dir is not None and not spec_path.is_absolute():
        spec_path = specs_dir / spec_path.name
    try:
        from control_plane.spec_director.models import SpecFrontMatter
        text = spec_path.read_text(encoding="utf-8")
        fm = SpecFrontMatter.from_spec_text(text)
        return fm.area_keywords
    except Exception:
        return []


def _any_keyword_matches(
    keywords: list[str],
    title: str,
    paths: list[str],
) -> bool:
    if not keywords:
        return False
    title_lower = title.lower()
    paths_lower = [p.lower() for p in paths]
    for kw in keywords:
        kw_lower = kw.lower()
        if kw_lower in title_lower:
            return True
        if any(kw_lower in p for p in paths_lower):
            return True
    return False
```

- [ ] **Step 6: Run the suppressor tests**

```bash
python -m pytest tests/test_spec_campaign_source.py -v
```

Expected: all tests pass including the two new suppressor tests.

- [ ] **Step 7: Run full suite**

```bash
python -m pytest tests/ -x -q 2>&1 | tail -20
```

Fix any breakage from removing `area_keywords` from `CampaignRecord` (callers in `main.py` that set it when creating the record).

- [ ] **Step 8: Update `main.py` campaign record creation**

In `src/control_plane/entrypoints/spec_director/main.py`, remove `area_keywords` from the `CampaignRecord(...)` constructor call:

```python
    campaign_record = CampaignRecord(
        campaign_id=result.campaign_id,
        slug=result.slug,
        spec_file=str(spec_path),
        status="active",
        created_at=datetime.now(UTC).isoformat(),
    )
```

Also remove `trigger_source=trigger.source` and `last_progress_at=...` from the constructor call.

- [ ] **Step 9: Run full suite again**

```bash
python -m pytest tests/ -x -q 2>&1 | tail -20
```

Expected: all pass.

- [ ] **Step 10: Commit**

```bash
git add src/control_plane/spec_director/models.py \
        src/control_plane/spec_director/state.py \
        src/control_plane/spec_director/suppressor.py \
        src/control_plane/entrypoints/spec_director/main.py \
        tests/test_spec_campaign_source.py
git commit -m "refactor: slim CampaignRecord to thin index, read area_keywords from spec front matter"
```

---

## Task 3: Remove `revise_spec` and dead recovery code

**Files:**
- Modify: `src/control_plane/spec_director/recovery.py`

No new tests needed — we are removing code that has no callers after this PR (confirm below).

- [ ] **Step 1: Check callers of `revise_spec`, `revision_budget_ok`, `is_stalled`**

```bash
grep -rn "revise_spec\|revision_budget_ok\|is_stalled\|increment_revision_count\|update_progress" \
    src/control_plane/ --include="*.py"
```

Expected: only defined in `recovery.py` and `state.py` (already removed from state.py in Task 2); `revise_spec` called nowhere externally.

- [ ] **Step 2: Remove dead methods from `recovery.py`**

Replace the contents of `src/control_plane/spec_director/recovery.py` with:

```python
# src/control_plane/spec_director/recovery.py
from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from control_plane.spec_director.models import CampaignRecord
from control_plane.spec_director.state import CampaignStateManager

logger = logging.getLogger(__name__)


class RecoveryService:
    def __init__(
        self,
        client: Any,
        state_manager: CampaignStateManager,
        abandon_hours: int = 72,
    ) -> None:
        self._client = client
        self._state = state_manager
        self._abandon_hours = abandon_hours

    def should_abandon(self, campaign: CampaignRecord) -> bool:
        """True if campaign has been active beyond abandon_hours."""
        try:
            created = datetime.fromisoformat(campaign.created_at)
        except Exception:
            return True
        elapsed = (datetime.now(UTC) - created).total_seconds() / 3600
        return elapsed > self._abandon_hours

    def self_cancel(
        self,
        campaign: CampaignRecord,
        reason: str,
        specs_dir: Path,
    ) -> None:
        """Perform orderly campaign self-cancellation."""
        logger.info(
            '{"event": "campaign_self_cancel", "campaign_id": "%s", "reason": "%s"}',
            campaign.campaign_id, reason,
        )
        # Cancel all open Plane tasks for this campaign
        try:
            issues = self._client.list_issues()
            for issue in issues:
                labels = [str(lbl.get("name", "")).lower() for lbl in (issue.get("labels") or [])]
                if f"campaign-id: {campaign.campaign_id}" in labels:
                    state_name = str((issue.get("state") or {}).get("name", "")).lower()
                    if state_name not in {"done", "cancelled"}:
                        self._client.transition_issue(str(issue["id"]), "Cancelled")
        except Exception as exc:
            logger.warning(
                '{"event": "campaign_cancel_issues_error", "error": "%s"}', str(exc)
            )

        # Update spec front matter
        spec_path = specs_dir / f"{campaign.slug}.md"
        if spec_path.exists():
            text = spec_path.read_text()
            spec_path.write_text(text.replace("status: active", "status: cancelled", 1))

        # Mark campaign cancelled in state
        self._state.mark_cancelled(campaign.campaign_id)
        logger.info(
            '{"event": "campaign_cancelled", "campaign_id": "%s"}',
            campaign.campaign_id,
        )
```

- [ ] **Step 3: Run full suite**

```bash
python -m pytest tests/ -x -q 2>&1 | tail -20
```

Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add src/control_plane/spec_director/recovery.py
git commit -m "refactor: remove revise_spec and stale recovery methods from RecoveryService"
```

---

## Task 4: Rewrite trigger — queue drain primary, drop file secondary, remove Plane label

**Files:**
- Modify: `src/control_plane/spec_director/trigger.py`
- Modify: `src/control_plane/spec_director/models.py` (remove `PLANE_LABEL` from `TriggerSource`)
- Create: `tests/test_trigger_detector.py`

- [ ] **Step 1: Write failing tests**

`tests/test_trigger_detector.py`:

```python
"""Tests for the TriggerDetector: queue drain primary, drop file secondary."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from control_plane.spec_director.trigger import TriggerDetector
from control_plane.spec_director.models import TriggerSource


def _make_detector(tmp_path: Path, queue_empty: bool = True) -> TriggerDetector:
    drop_file = tmp_path / "spec_direction.md"
    return TriggerDetector(
        drop_file_path=drop_file,
        queue_threshold=0,  # unused in new design but kept for compat
    )


def test_no_trigger_when_active_campaign(tmp_path):
    detector = _make_detector(tmp_path)
    result = detector.detect(ready_count=0, running_count=0, has_active_campaign=True)
    assert result is None


def test_queue_drain_triggers_when_board_empty(tmp_path):
    detector = _make_detector(tmp_path)
    result = detector.detect(ready_count=0, running_count=0, has_active_campaign=False)
    assert result is not None
    assert result.source == TriggerSource.QUEUE_DRAIN


def test_queue_drain_does_not_trigger_if_running_tasks_exist(tmp_path):
    detector = _make_detector(tmp_path)
    result = detector.detect(ready_count=0, running_count=1, has_active_campaign=False)
    assert result is None


def test_queue_drain_does_not_trigger_if_ready_tasks_exist(tmp_path):
    detector = _make_detector(tmp_path)
    result = detector.detect(ready_count=2, running_count=0, has_active_campaign=False)
    assert result is None


def test_drop_file_takes_priority_over_queue_drain(tmp_path):
    detector = _make_detector(tmp_path)
    drop_file = tmp_path / "spec_direction.md"
    drop_file.write_text("Focus on auth module refactor")
    result = detector.detect(ready_count=0, running_count=0, has_active_campaign=False)
    assert result is not None
    assert result.source == TriggerSource.DROP_FILE
    assert "auth module" in result.seed_text


def test_drop_file_triggers_even_when_board_has_tasks(tmp_path):
    """Drop file (operator intent) fires regardless of board state."""
    detector = _make_detector(tmp_path)
    drop_file = tmp_path / "spec_direction.md"
    drop_file.write_text("Operator wants this")
    result = detector.detect(ready_count=5, running_count=2, has_active_campaign=False)
    assert result is not None
    assert result.source == TriggerSource.DROP_FILE


def test_archive_drop_file_moves_file(tmp_path):
    detector = _make_detector(tmp_path)
    drop_file = tmp_path / "spec_direction.md"
    drop_file.write_text("seed")
    detector.archive_drop_file()
    assert not drop_file.exists()
    archive_dir = tmp_path / "spec_direction.archive"
    assert archive_dir.exists()
    archived = list(archive_dir.iterdir())
    assert len(archived) == 1


def test_no_trigger_when_board_not_empty_and_no_drop_file(tmp_path):
    detector = _make_detector(tmp_path)
    result = detector.detect(ready_count=3, running_count=0, has_active_campaign=False)
    assert result is None
```

- [ ] **Step 2: Run to verify they fail**

```bash
python -m pytest tests/test_trigger_detector.py -v 2>&1 | head -40
```

Expected: multiple failures (wrong signature, PLANE_LABEL references, etc.)

- [ ] **Step 3: Rewrite `trigger.py`**

Replace `src/control_plane/spec_director/trigger.py` with:

```python
# src/control_plane/spec_director/trigger.py
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from control_plane.spec_director.models import TriggerSource

logger = logging.getLogger(__name__)


@dataclass
class TriggerResult:
    source: TriggerSource
    seed_text: str


class TriggerDetector:
    def __init__(
        self,
        drop_file_path: Path,
        queue_threshold: int = 0,  # kept for config compat, not used in logic
    ) -> None:
        self._drop_file = drop_file_path

    def detect(
        self,
        ready_count: int,
        running_count: int,
        has_active_campaign: bool,
    ) -> TriggerResult | None:
        """Return a TriggerResult if a campaign should start, else None."""
        if has_active_campaign:
            return None

        # Priority 1: operator drop-file (fires regardless of board state)
        if self._drop_file.exists():
            seed = self._drop_file.read_text(encoding="utf-8").strip()
            logger.info('{"event": "spec_trigger_drop_file"}')
            return TriggerResult(source=TriggerSource.DROP_FILE, seed_text=seed)

        # Priority 2: queue drain — board must be completely idle
        if ready_count == 0 and running_count == 0:
            logger.info('{"event": "spec_trigger_queue_drain"}')
            return TriggerResult(source=TriggerSource.QUEUE_DRAIN, seed_text="")

        return None

    def archive_drop_file(self) -> None:
        """Move drop-file to archive after successful campaign creation."""
        if not self._drop_file.exists():
            return
        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        archive_dir = self._drop_file.parent / "spec_direction.archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        self._drop_file.rename(archive_dir / f"{ts}.md")
        logger.info('{"event": "spec_drop_file_archived"}')
```

- [ ] **Step 4: Remove `PLANE_LABEL` from `TriggerSource` in `models.py`**

Change:

```python
class TriggerSource(str, Enum):
    DROP_FILE = "drop_file"
    PLANE_LABEL = "plane_label"
    QUEUE_DRAIN = "queue_drain"
```

to:

```python
class TriggerSource(str, Enum):
    DROP_FILE = "drop_file"
    QUEUE_DRAIN = "queue_drain"
```

- [ ] **Step 5: Run trigger tests**

```bash
python -m pytest tests/test_trigger_detector.py -v
```

Expected: all 8 tests pass.

- [ ] **Step 6: Run full suite**

```bash
python -m pytest tests/ -x -q 2>&1 | tail -20
```

Fix any breakage from `PLANE_LABEL` removal (grep for it first: `grep -rn PLANE_LABEL src/ tests/`).

- [ ] **Step 7: Commit**

```bash
git add src/control_plane/spec_director/trigger.py \
        src/control_plane/spec_director/models.py \
        tests/test_trigger_detector.py
git commit -m "refactor: rewrite trigger — queue drain primary, drop file secondary, remove Plane label"
```

---

## Task 5: Refactor context bundle — remove insight_snapshot, add board signals

**Files:**
- Modify: `src/control_plane/spec_director/context_bundle.py`
- Modify: `src/control_plane/spec_director/brainstorm.py`

- [ ] **Step 1: Write a failing test**

Add to `tests/test_spec_campaign_source.py`:

```python
def test_context_bundle_has_no_insight_snapshot():
    from control_plane.spec_director.context_bundle import ContextBundle
    bundle = ContextBundle(
        git_logs={},
        specs_index=[],
        recent_done_tasks=[],
        recent_cancelled_tasks=[],
        open_task_count=0,
        seed_text="",
        available_repos=[],
    )
    assert not hasattr(bundle, "insight_snapshot")


def test_context_bundle_build_includes_board_signals():
    from control_plane.spec_director.context_bundle import ContextBundleBuilder
    builder = ContextBundleBuilder()
    board_issues = [
        {"name": "Fix login bug", "state": {"name": "Done"}, "updated_at": "2026-04-10T00:00:00Z"},
        {"name": "Add tests", "state": {"name": "Cancelled"}, "updated_at": "2026-04-11T00:00:00Z"},
        {"name": "Refactor DB", "state": {"name": "Ready for AI"}, "updated_at": "2026-04-12T00:00:00Z"},
    ]
    bundle = builder.build(
        seed_text="",
        board_issues=board_issues,
        specs_index=[],
        git_logs={},
        available_repos=["repo_a", "repo_b"],
    )
    assert any(t["name"] == "Fix login bug" for t in bundle.recent_done_tasks)
    assert any(t["name"] == "Add tests" for t in bundle.recent_cancelled_tasks)
    assert bundle.open_task_count == 1
    assert bundle.available_repos == ["repo_a", "repo_b"]
    assert not hasattr(bundle, "insight_snapshot")
```

- [ ] **Step 2: Run to verify they fail**

```bash
python -m pytest tests/test_spec_campaign_source.py::test_context_bundle_has_no_insight_snapshot \
    tests/test_spec_campaign_source.py::test_context_bundle_build_includes_board_signals -v 2>&1 | head -20
```

Expected: `FAILED` (old `ContextBundle` has `insight_snapshot`, new fields don't exist).

- [ ] **Step 3: Rewrite `context_bundle.py`**

Replace `src/control_plane/spec_director/context_bundle.py` with:

```python
# src/control_plane/spec_director/context_bundle.py
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ContextBundle:
    git_logs: dict[str, str]          # {repo_key: git_log_text}
    specs_index: list[dict]
    recent_done_tasks: list[dict]     # Done tasks from last 14 days
    recent_cancelled_tasks: list[dict]
    open_task_count: int
    seed_text: str
    available_repos: list[str]


class ContextBundleBuilder:
    _MAX_SPECS = 50
    _MAX_GIT_COMMITS = 30
    _MAX_BOARD_TASKS = 50
    _RECENT_DAYS = 14

    def build(
        self,
        seed_text: str,
        board_issues: list[dict],
        specs_index: list[dict],
        git_logs: dict[str, str],
        available_repos: list[str],
    ) -> ContextBundle:
        from datetime import UTC, datetime, timedelta
        cutoff = datetime.now(UTC) - timedelta(days=self._RECENT_DAYS)

        recent_done: list[dict] = []
        recent_cancelled: list[dict] = []
        open_count = 0

        for issue in board_issues[: self._MAX_BOARD_TASKS]:
            state = str((issue.get("state") or {}).get("name", "")).lower()
            updated_raw = issue.get("updated_at") or issue.get("created_at") or ""
            try:
                updated = datetime.fromisoformat(updated_raw.replace("Z", "+00:00"))
            except Exception:
                updated = datetime.min.replace(tzinfo=UTC)

            if state == "done" and updated >= cutoff:
                recent_done.append({"name": issue.get("name", "")})
            elif state == "cancelled" and updated >= cutoff:
                recent_cancelled.append({"name": issue.get("name", "")})
            elif state not in {"done", "cancelled"}:
                open_count += 1

        return ContextBundle(
            git_logs=git_logs,
            specs_index=specs_index[: self._MAX_SPECS],
            recent_done_tasks=recent_done,
            recent_cancelled_tasks=recent_cancelled,
            open_task_count=open_count,
            seed_text=seed_text,
            available_repos=available_repos,
        )

    @staticmethod
    def collect_git_log(repo_path: Path, n: int = 30) -> str:
        """Run git log --oneline on *repo_path* and return the output."""
        try:
            result = subprocess.run(
                ["git", "log", "--oneline", f"-{n}"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=15,
            )
            return result.stdout.strip()
        except Exception:
            return ""

    @staticmethod
    def collect_specs_index(specs_dir: Path) -> list[dict]:
        """Return [{slug, status}] for each spec in specs_dir."""
        from control_plane.spec_director.models import SpecFrontMatter
        index = []
        for p in sorted(specs_dir.glob("*.md")):
            if p.parent.name == "archive":
                continue
            try:
                fm = SpecFrontMatter.from_spec_text(p.read_text())
                index.append({"slug": fm.slug, "status": fm.status})
            except Exception:
                index.append({"slug": p.stem, "status": "unknown"})
        return index
```

- [ ] **Step 4: Update `brainstorm.py` prompt to include available repos and use new bundle fields**

In `src/control_plane/spec_director/brainstorm.py`:

Update `_SYSTEM_PROMPT` to reference available repos:

```python
_SYSTEM_PROMPT = """\
You are a software architect for a Python repository. Given signals about the codebase's \
current state, generate a focused spec document for the next improvement campaign.

The spec MUST begin with a YAML front matter block in this exact format:
---
campaign_id: <a UUID v4 you generate>
slug: <kebab-case-slug>
phases:
  - implement
  - test
  - improve
repos:
  - <one of the available repos listed in the context>
area_keywords:
  - <directory prefix or topic keyword>
status: active
created_at: <ISO 8601 timestamp>
---

After the front matter, write a markdown spec document with:
- ## Overview (2-3 sentences)
- ## Goals (numbered list of concrete, bounded tasks)
- ## Constraints (approach decisions, allowed paths, things to avoid)
- ## Success Criteria (how to know it is done)

Be specific and bounded. Prefer 2-4 goals over vague large ones. \
Each goal should be completable by one kodo run in under 1 hour. \
Set repos: to exactly one repo from the available repos list."""
```

Update `_build_user_prompt` to use new `ContextBundle` fields (replace the old method):

```python
    @staticmethod
    def _build_user_prompt(bundle: ContextBundle) -> str:
        parts = []
        if bundle.available_repos:
            parts.append(f"## Available Repos\n" + "\n".join(f"- {r}" for r in bundle.available_repos))
        if bundle.seed_text:
            parts.append(f"## Operator Direction\n{bundle.seed_text}")
        for repo_key, log_text in bundle.git_logs.items():
            if log_text:
                parts.append(f"## Recent Git Activity ({repo_key})\n```\n{log_text}\n```")
        if bundle.specs_index:
            lines = "\n".join(f"- {s.get('slug', '?')} ({s.get('status', '?')})" for s in bundle.specs_index)
            parts.append(f"## Existing Specs\n{lines}")
        if bundle.recent_done_tasks:
            lines = "\n".join(f"- {t.get('name', '?')} [Done]" for t in bundle.recent_done_tasks[:20])
            parts.append(f"## Recently Completed Tasks\n{lines}")
        if bundle.recent_cancelled_tasks:
            lines = "\n".join(f"- {t.get('name', '?')} [Cancelled]" for t in bundle.recent_cancelled_tasks[:10])
            parts.append(f"## Recently Cancelled Tasks (avoid re-proposing)\n{lines}")
        parts.append(f"## Board Summary\n{bundle.open_task_count} open task(s) currently active.")
        parts.append("Generate the spec document now.")
        return "\n\n".join(parts)
```

Also update `brainstorm()` to accept `ContextBundle` with the new type (the method already takes `bundle: ContextBundle` — just the fields changed).

- [ ] **Step 5: Run the context bundle and brainstorm tests**

```bash
python -m pytest tests/test_spec_campaign_source.py -v
```

Expected: all pass.

- [ ] **Step 6: Run full suite**

```bash
python -m pytest tests/ -x -q 2>&1 | tail -20
```

Fix any breakage from `ContextBundle` field changes (the old `insight_snapshot` field is gone; `git_log` string → `git_logs` dict).

- [ ] **Step 7: Commit**

```bash
git add src/control_plane/spec_director/context_bundle.py \
        src/control_plane/spec_director/brainstorm.py \
        tests/test_spec_campaign_source.py
git commit -m "refactor: remove insight_snapshot from context bundle, add board signals and multi-repo git logs"
```

---

## Task 6: Create `PhaseOrchestrator`

This is the core new module. It reads campaign task states from the board and:
1. Advances implement→test_campaign→improve_campaign phases
2. Rewrites Blocked task descriptions via Claude CLI (unblocking)
3. Detects campaign completion and closes the parent task

**Files:**
- Create: `src/control_plane/spec_director/phase_orchestrator.py`
- Create: `tests/test_phase_orchestrator.py`

- [ ] **Step 1: Write failing tests**

`tests/test_phase_orchestrator.py`:

```python
"""Tests for PhaseOrchestrator — phase advancement and blocked-task unblocking."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from control_plane.spec_director.phase_orchestrator import PhaseOrchestrator
from control_plane.spec_director.models import ActiveCampaigns, CampaignRecord
from control_plane.spec_director.state import CampaignStateManager


_CAMPAIGN_ID = "test-campaign-uuid"


def _make_issue(
    *,
    task_id: str,
    name: str,
    state: str,
    kind: str,
    campaign_id: str = _CAMPAIGN_ID,
) -> dict:
    labels = [
        {"name": f"task-kind: {kind}"},
        {"name": "source: spec-campaign"},
        {"name": f"campaign-id: {campaign_id}"},
    ]
    return {
        "id": task_id,
        "name": name,
        "state": {"name": state},
        "labels": labels,
        "description": (
            f"## Execution\nrepo: repo_a\nbase_branch: main\nmode: {kind}\n"
            f"spec_campaign_id: {campaign_id}\nspec_file: docs/specs/my-slug.md\n"
            f"task_phase: {'implement' if kind == 'goal' else kind}\n"
        ),
    }


def _make_parent(*, campaign_id: str = _CAMPAIGN_ID) -> dict:
    return {
        "id": "parent-1",
        "name": "[Campaign] my-slug",
        "state": {"name": "Running"},
        "labels": [
            {"name": "source: spec-campaign"},
            {"name": f"campaign-id: {campaign_id}"},
        ],
    }


def _make_orchestrator(tmp_path: Path) -> tuple[PhaseOrchestrator, MagicMock]:
    client = MagicMock()
    client.transition_issue.return_value = None
    client.comment_issue.return_value = None
    client.update_issue_description.return_value = None

    state_path = tmp_path / "active.json"
    state_mgr = CampaignStateManager(state_path=state_path)
    record = CampaignRecord(
        campaign_id=_CAMPAIGN_ID,
        slug="my-slug",
        spec_file="docs/specs/my-slug.md",
        status="active",
        created_at="2026-01-01T00:00:00",
    )
    state_mgr.add_campaign(record)

    orch = PhaseOrchestrator(
        client=client,
        state_manager=state_mgr,
        specs_dir=tmp_path / "docs" / "specs",
    )
    return orch, client


def test_advances_to_test_when_all_implement_done(tmp_path):
    orch, client = _make_orchestrator(tmp_path)
    issues = [
        _make_parent(),
        _make_issue(task_id="impl-1", name="[Impl] Goal 1", state="Done", kind="goal"),
        _make_issue(task_id="test-1", name="[Test] Goal 1", state="Backlog", kind="test_campaign"),
    ]
    result = orch.run(issues)
    assert result.phases_advanced == 1
    client.transition_issue.assert_any_call("test-1", "Ready for AI")


def test_does_not_advance_if_implement_blocked(tmp_path):
    orch, client = _make_orchestrator(tmp_path)
    issues = [
        _make_parent(),
        _make_issue(task_id="impl-1", name="[Impl] Goal 1", state="Blocked", kind="goal"),
        _make_issue(task_id="test-1", name="[Test] Goal 1", state="Backlog", kind="test_campaign"),
    ]
    result = orch.run(issues)
    # Blocked task should get a rewrite attempt, but phase does NOT advance
    transition_calls = [str(c) for c in client.transition_issue.call_args_list]
    assert not any("test-1" in c and "Ready for AI" in c for c in transition_calls)


def test_advances_to_improve_when_all_test_done(tmp_path):
    orch, client = _make_orchestrator(tmp_path)
    issues = [
        _make_parent(),
        _make_issue(task_id="impl-1", name="[Impl] Goal 1", state="Done", kind="goal"),
        _make_issue(task_id="test-1", name="[Test] Goal 1", state="Done", kind="test_campaign"),
        _make_issue(task_id="imp-1", name="[Improve] Goal 1", state="Backlog", kind="improve_campaign"),
    ]
    result = orch.run(issues)
    assert result.phases_advanced >= 1
    client.transition_issue.assert_any_call("imp-1", "Ready for AI")


def test_completes_campaign_when_all_phases_terminal(tmp_path):
    orch, client = _make_orchestrator(tmp_path)
    state_mgr = orch._state

    issues = [
        _make_parent(),
        _make_issue(task_id="impl-1", name="[Impl] Goal 1", state="Done", kind="goal"),
        _make_issue(task_id="test-1", name="[Test] Goal 1", state="Done", kind="test_campaign"),
        _make_issue(task_id="imp-1", name="[Improve] Goal 1", state="Cancelled", kind="improve_campaign"),
    ]
    result = orch.run(issues)

    assert result.campaigns_completed == 1
    client.transition_issue.assert_any_call("parent-1", "Done")
    active = state_mgr.load()
    assert not active.has_active()


def test_blocked_task_rewritten_and_requeued(tmp_path):
    orch, client = _make_orchestrator(tmp_path)
    blocked_issue = _make_issue(
        task_id="impl-1", name="[Impl] Goal 1", state="Blocked", kind="goal"
    )
    # fetch_issue returns full description
    client.fetch_issue.return_value = dict(blocked_issue)

    with patch("control_plane.spec_director.phase_orchestrator.call_claude") as mock_claude:
        mock_claude.return_value = (
            "## Execution\nrepo: repo_a\nbase_branch: main\nmode: goal\n"
            "spec_campaign_id: test-campaign-uuid\nspec_file: docs/specs/my-slug.md\n"
            "task_phase: implement\nblock_rewrite_count: 1\n\n## Goal\nRewritten goal text.\n"
        )
        result = orch.run([_make_parent(), blocked_issue])

    assert result.tasks_unblocked == 1
    client.update_issue_description.assert_called_once()
    client.transition_issue.assert_any_call("impl-1", "Ready for AI")


def test_blocked_task_cancelled_after_two_rewrites(tmp_path):
    orch, client = _make_orchestrator(tmp_path)
    # Description already has block_rewrite_count: 2
    blocked_issue = _make_issue(
        task_id="impl-1", name="[Impl] Goal 1", state="Blocked", kind="goal"
    )
    full_desc = blocked_issue["description"] + "block_rewrite_count: 2\n"
    blocked_issue["description"] = full_desc
    client.fetch_issue.return_value = dict(blocked_issue, description=full_desc)

    result = orch.run([_make_parent(), blocked_issue])

    assert result.tasks_cancelled == 1
    client.transition_issue.assert_any_call("impl-1", "Cancelled")


def test_no_action_when_no_active_campaigns(tmp_path):
    client = MagicMock()
    state_path = tmp_path / "active.json"
    state_mgr = CampaignStateManager(state_path=state_path)
    orch = PhaseOrchestrator(
        client=client,
        state_manager=state_mgr,
        specs_dir=tmp_path / "docs" / "specs",
    )
    result = orch.run([])
    assert result.phases_advanced == 0
    assert result.campaigns_completed == 0
    client.transition_issue.assert_not_called()
```

- [ ] **Step 2: Run to verify they fail**

```bash
python -m pytest tests/test_phase_orchestrator.py -v 2>&1 | head -30
```

Expected: `ImportError` or `ModuleNotFoundError` — `phase_orchestrator` doesn't exist yet.

- [ ] **Step 3: Create `phase_orchestrator.py`**

Create `src/control_plane/spec_director/phase_orchestrator.py`:

```python
# src/control_plane/spec_director/phase_orchestrator.py
"""Phase orchestrator — advances spec campaign phases and unblocks stuck tasks."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from control_plane.spec_director._claude_cli import call_claude
from control_plane.spec_director.state import CampaignStateManager

logger = logging.getLogger(__name__)

_TERMINAL_STATES = frozenset({"done", "cancelled"})


def _status(issue: dict) -> str:
    state = issue.get("state")
    if isinstance(state, dict):
        return str(state.get("name", "")).lower()
    return str(state or "").lower()


def _labels(issue: dict) -> list[str]:
    raw = issue.get("labels", [])
    result = []
    if isinstance(raw, list):
        for r in raw:
            if isinstance(r, dict):
                n = r.get("name")
                if n:
                    result.append(str(n))
            elif r:
                result.append(str(r))
    return result


def _campaign_id_from_issue(issue: dict) -> str | None:
    for lbl in _labels(issue):
        if lbl.lower().startswith("campaign-id:"):
            return lbl.split(":", 1)[1].strip()
    return None


def _task_kind(issue: dict) -> str:
    for lbl in _labels(issue):
        if lbl.strip().lower().startswith("task-kind:"):
            return lbl.split(":", 1)[1].strip()
    return "goal"


def _parse_rewrite_count(description: str) -> int:
    m = re.search(r"block_rewrite_count:\s*(\d+)", description)
    return int(m.group(1)) if m else 0


def _set_rewrite_count(description: str, count: int) -> str:
    if re.search(r"block_rewrite_count:\s*\d+", description):
        return re.sub(r"block_rewrite_count:\s*\d+", f"block_rewrite_count: {count}", description)
    # Inject after task_phase line, or at end of ## Execution block
    updated = re.sub(
        r"(task_phase:\s*\S+)",
        rf"\1\nblock_rewrite_count: {count}",
        description,
        count=1,
    )
    if updated == description:
        # Fallback: append before ## Goal
        updated = description.replace("## Goal\n", f"block_rewrite_count: {count}\n\n## Goal\n", 1)
    return updated


def _read_spec_text(description: str, specs_dir: Path) -> str:
    m = re.search(r"spec_file:\s*(\S+)", description)
    if not m:
        return ""
    rel = m.group(1)
    candidates = [specs_dir / Path(rel).name, Path(rel)]
    for p in candidates:
        try:
            return p.read_text(encoding="utf-8")
        except Exception:
            continue
    return ""


@dataclass
class PhaseOrchestrationResult:
    phases_advanced: int = 0
    tasks_unblocked: int = 0
    tasks_cancelled: int = 0
    campaigns_completed: int = 0
    errors: list[str] = field(default_factory=list)


class PhaseOrchestrator:
    def __init__(
        self,
        client: Any,
        state_manager: CampaignStateManager,
        specs_dir: Path,
        max_rewrite_attempts: int = 2,
    ) -> None:
        self._client = client
        self._state = state_manager
        self._specs_dir = specs_dir
        self._max_rewrites = max_rewrite_attempts

    def run(self, issues: list[dict]) -> PhaseOrchestrationResult:
        result = PhaseOrchestrationResult()
        active = self._state.load()
        for campaign in active.active_campaigns():
            try:
                self._orchestrate(campaign.campaign_id, issues, result)
            except Exception as exc:
                logger.error(
                    '{"event": "phase_orchestrator_error", "campaign_id": "%s", "error": "%s"}',
                    campaign.campaign_id, str(exc),
                )
                result.errors.append(f"{campaign.campaign_id}: {exc}")
        return result

    def _orchestrate(
        self,
        campaign_id: str,
        issues: list[dict],
        result: PhaseOrchestrationResult,
    ) -> None:
        by_phase: dict[str, list[dict]] = {
            "goal": [],
            "test_campaign": [],
            "improve_campaign": [],
            "parent": [],
        }
        for issue in issues:
            if _campaign_id_from_issue(issue) != campaign_id:
                continue
            if str(issue.get("name", "")).startswith("[Campaign]"):
                by_phase["parent"].append(issue)
            else:
                kind = _task_kind(issue)
                bucket = kind if kind in by_phase else "goal"
                by_phase[bucket].append(issue)

        # Handle blocked tasks before phase-advancement check
        for phase_key in ("goal", "test_campaign", "improve_campaign"):
            for issue in by_phase[phase_key]:
                if _status(issue) == "blocked":
                    self._handle_blocked(issue, result)

        # Phase advancement: implement → test_campaign
        if by_phase["goal"] and self._all_terminal(by_phase["goal"]):
            backlog_test = [i for i in by_phase["test_campaign"] if _status(i) == "backlog"]
            for issue in backlog_test:
                self._client.transition_issue(str(issue["id"]), "Ready for AI")
                result.phases_advanced += 1
            if backlog_test:
                self._comment_parent(
                    by_phase["parent"],
                    f"Advancing to test phase: {len(backlog_test)} task(s) promoted.",
                )
                logger.info(
                    '{"event": "phase_advanced", "campaign_id": "%s", "to": "test_campaign", "count": %d}',
                    campaign_id, len(backlog_test),
                )

        # Phase advancement: test_campaign → improve_campaign
        if by_phase["test_campaign"] and self._all_terminal(by_phase["test_campaign"]):
            backlog_improve = [i for i in by_phase["improve_campaign"] if _status(i) == "backlog"]
            for issue in backlog_improve:
                self._client.transition_issue(str(issue["id"]), "Ready for AI")
                result.phases_advanced += 1
            if backlog_improve:
                self._comment_parent(
                    by_phase["parent"],
                    f"Advancing to improve phase: {len(backlog_improve)} task(s) promoted.",
                )
                logger.info(
                    '{"event": "phase_advanced", "campaign_id": "%s", "to": "improve_campaign", "count": %d}',
                    campaign_id, len(backlog_improve),
                )

        # Campaign completion: all child tasks terminal
        all_tasks = by_phase["goal"] + by_phase["test_campaign"] + by_phase["improve_campaign"]
        if all_tasks and self._all_terminal(all_tasks):
            done_n = sum(1 for i in all_tasks if _status(i) == "done")
            cancelled_n = sum(1 for i in all_tasks if _status(i) == "cancelled")
            for parent in by_phase["parent"]:
                self._client.transition_issue(str(parent["id"]), "Done")
                self._client.comment_issue(
                    str(parent["id"]),
                    f"Campaign complete. {done_n} task(s) done, {cancelled_n} cancelled.",
                )
            self._state.mark_complete(campaign_id)
            result.campaigns_completed += 1
            logger.info(
                '{"event": "campaign_complete", "campaign_id": "%s", "done": %d, "cancelled": %d}',
                campaign_id, done_n, cancelled_n,
            )

    def _all_terminal(self, issues: list[dict]) -> bool:
        return bool(issues) and all(_status(i) in _TERMINAL_STATES for i in issues)

    def _handle_blocked(self, issue: dict, result: PhaseOrchestrationResult) -> None:
        task_id = str(issue["id"])
        try:
            full = self._client.fetch_issue(task_id)
            description = str(
                full.get("description") or full.get("description_stripped") or ""
            )
        except Exception:
            description = str(
                issue.get("description") or issue.get("description_stripped") or ""
            )

        rewrite_count = _parse_rewrite_count(description)
        if rewrite_count >= self._max_rewrites:
            self._client.transition_issue(task_id, "Cancelled")
            self._client.comment_issue(
                task_id,
                f"Task cancelled after {self._max_rewrites} rewrite attempts.",
            )
            result.tasks_cancelled += 1
            logger.info(
                '{"event": "blocked_task_cancelled", "task_id": "%s", "rewrites": %d}',
                task_id, rewrite_count,
            )
            return

        spec_text = _read_spec_text(description, self._specs_dir)
        title = str(issue.get("name", ""))
        prompt = (
            "Rewrite this Plane task description to be clearer and more actionable.\n"
            "Keep all ## section headers. Do NOT change repo:, base_branch:, mode:, "
            "spec_campaign_id:, spec_file:, task_phase: fields in ## Execution.\n"
            "Output ONLY the rewritten task description with no preamble.\n\n"
            f"## Task title\n{title}\n\n"
            f"## Current description\n{description}\n"
        )
        if spec_text:
            prompt += f"\n## Spec context (do not change the spec)\n{spec_text[:3000]}\n"

        try:
            rewritten = call_claude(prompt)
        except Exception as exc:
            logger.warning(
                '{"event": "blocked_rewrite_failed", "task_id": "%s", "error": "%s"}',
                task_id, str(exc),
            )
            return

        new_count = rewrite_count + 1
        rewritten = _set_rewrite_count(rewritten, new_count)
        self._client.update_issue_description(task_id, rewritten)
        self._client.transition_issue(task_id, "Ready for AI")
        self._client.comment_issue(
            task_id,
            f"Description rewritten (attempt {new_count}/{self._max_rewrites}). Re-queued.",
        )
        result.tasks_unblocked += 1
        logger.info(
            '{"event": "blocked_task_unblocked", "task_id": "%s", "rewrite_count": %d}',
            task_id, new_count,
        )

    def _comment_parent(self, parents: list[dict], message: str) -> None:
        for parent in parents:
            try:
                self._client.comment_issue(str(parent["id"]), message)
            except Exception:
                pass
```

- [ ] **Step 4: Run the phase orchestrator tests**

```bash
python -m pytest tests/test_phase_orchestrator.py -v
```

Expected: all 7 tests pass.

- [ ] **Step 5: Run full suite**

```bash
python -m pytest tests/ -x -q 2>&1 | tail -20
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/control_plane/spec_director/phase_orchestrator.py \
        tests/test_phase_orchestrator.py
git commit -m "feat: add PhaseOrchestrator for spec campaign phase advancement and blocked-task unblocking"
```

---

## Task 7: Rewrite the main loop to wire everything together

The main loop needs to:
1. Fetch issues once
2. Run `PhaseOrchestrator.run(issues)` first
3. Then check recovery (should_abandon)
4. Then check triggers (only if no active campaign)
5. If trigger fires: brainstorm → spec write → create tasks → update state
6. Use the new `TriggerDetector` signature (no Plane client, `running_count` parameter)
7. Use the new `ContextBundle` (multi-repo git logs, board signals, available_repos)

**Files:**
- Modify: `src/control_plane/entrypoints/spec_director/main.py`

- [ ] **Step 1: Read the current main.py**

```bash
cat src/control_plane/entrypoints/spec_director/main.py
```

(Already read above — confirms current structure.)

- [ ] **Step 2: Write a smoke test first**

Add to `tests/test_spec_campaign_source.py`:

```python
def test_main_run_once_with_no_trigger_and_no_campaigns(tmp_path, monkeypatch):
    """run_once exits cleanly when board is non-empty and no drop file exists."""
    from unittest.mock import MagicMock, patch
    from control_plane.entrypoints.spec_director.main import run_once

    # Minimal settings
    settings = MagicMock()
    settings.spec_director.enabled = True
    settings.spec_director.spec_retention_days = 90
    settings.spec_director.campaign_stall_hours = 24
    settings.spec_director.campaign_abandon_hours = 72
    settings.spec_director.spec_trigger_queue_threshold = 0
    settings.spec_director.drop_file_path = str(tmp_path / "spec_direction.md")
    settings.spec_director.brainstorm_model = "claude-sonnet-4-6"
    settings.spec_director.max_tasks_per_campaign = 6
    settings.spec_director.brainstorm_context_snapshot_kb = 8
    settings.plane.project_id = "proj-1"
    settings.repos = {}

    client = MagicMock()
    client.list_issues.return_value = [
        {"id": "t1", "name": "Open task", "state": {"name": "Ready for AI"}, "labels": []},
    ]

    # Patch specs dir to tmp_path to avoid touching real docs/
    monkeypatch.chdir(tmp_path)
    (tmp_path / "docs" / "specs").mkdir(parents=True)
    (tmp_path / "state" / "campaigns").mkdir(parents=True)

    run_once(settings, client)  # Should not raise
```

- [ ] **Step 3: Run to verify the smoke test passes (or identify failures)**

```bash
python -m pytest tests/test_spec_campaign_source.py::test_main_run_once_with_no_trigger_and_no_campaigns -v 2>&1 | head -40
```

Note any failures — these reveal what `run_once` currently expects vs what we're about to change.

- [ ] **Step 4: Rewrite `run_once` in `main.py`**

Replace `run_once()` with:

```python
def _count_ready_and_running(issues: list[dict]) -> tuple[int, int]:
    """Return (ready_for_ai_count, running_count) from a pre-fetched issues list."""
    ready = sum(1 for i in issues if str((i.get("state") or {}).get("name", "")).lower() == "ready for ai")
    running = sum(1 for i in issues if str((i.get("state") or {}).get("name", "")).lower() == "running")
    return ready, running


def run_once(settings: Any, client: PlaneClient) -> None:
    sd = settings.spec_director
    if not sd.enabled:
        return

    logger.info(json.dumps({"event": "spec_cycle_start"}))

    state_mgr = CampaignStateManager()
    spec_writer = SpecWriter(specs_dir=_SPECS_DIR)

    # Rotate expired specs
    spec_writer.archive_expired(retention_days=sd.spec_retention_days)

    # Fetch issues once — used by phase orchestrator and trigger detection
    try:
        issues = client.list_issues()
    except Exception as exc:
        logger.error(json.dumps({"event": "spec_list_issues_failed", "error": str(exc)}))
        return

    # Phase orchestration — advance phases, unblock tasks, detect completions
    orchestrator = PhaseOrchestrator(
        client=client,
        state_manager=state_mgr,
        specs_dir=_SPECS_DIR,
    )
    orch_result = orchestrator.run(issues)
    if orch_result.phases_advanced or orch_result.campaigns_completed:
        logger.info(json.dumps({
            "event": "spec_orchestration_summary",
            "phases_advanced": orch_result.phases_advanced,
            "tasks_unblocked": orch_result.tasks_unblocked,
            "tasks_cancelled": orch_result.tasks_cancelled,
            "campaigns_completed": orch_result.campaigns_completed,
        }))

    # Recovery: abandon campaigns that have been active too long
    active = state_mgr.load()
    recovery = RecoveryService(
        client=client,
        state_manager=state_mgr,
        abandon_hours=sd.campaign_abandon_hours,
    )
    for campaign in active.active_campaigns():
        if recovery.should_abandon(campaign):
            recovery.self_cancel(campaign, "abandon_hours_exceeded", _SPECS_DIR)
            logger.info(json.dumps({
                "event": "spec_campaign_abandoned",
                "campaign_id": campaign.campaign_id,
            }))

    # Reload after orchestration + recovery
    active = state_mgr.load()

    # Trigger detection — only if no active campaign
    ready_count, running_count = _count_ready_and_running(issues)
    trigger_detector = TriggerDetector(
        drop_file_path=Path(sd.drop_file_path),
    )
    trigger = trigger_detector.detect(
        ready_count=ready_count,
        running_count=running_count,
        has_active_campaign=active.has_active(),
    )

    if trigger is None:
        logger.info(json.dumps({
            "event": "spec_no_trigger",
            "ready_count": ready_count,
            "running_count": running_count,
            "has_active": active.has_active(),
        }))
        return

    logger.info(json.dumps({
        "event": "spec_campaign_starting",
        "trigger_source": str(trigger.source),
        "seed_preview": trigger.seed_text[:80],
    }))

    # Disk space check before writing
    try:
        _check_disk_space(_SPECS_DIR)
    except OSError as exc:
        logger.error(json.dumps({"event": "spec_disk_space_critical", "error": str(exc)}))
        return

    # Build context bundle — multi-repo git logs
    available_repos = list(settings.repos.keys()) if settings.repos else []
    git_logs: dict[str, str] = {}
    for repo_key, repo_cfg in (settings.repos or {}).items():
        repo_path = Path(getattr(repo_cfg, "local_path", "")) if repo_cfg else None
        if repo_path and repo_path.exists():
            git_logs[repo_key] = ContextBundleBuilder.collect_git_log(repo_path)

    bundle_builder = ContextBundleBuilder()
    specs_index = ContextBundleBuilder.collect_specs_index(_SPECS_DIR)
    bundle = bundle_builder.build(
        seed_text=trigger.seed_text,
        board_issues=issues,
        specs_index=specs_index,
        git_logs=git_logs,
        available_repos=available_repos,
    )

    # Brainstorm
    brainstorm_svc = BrainstormService(model=sd.brainstorm_model)
    try:
        result = brainstorm_svc.brainstorm(bundle)
    except Exception as exc:
        logger.error(json.dumps({"event": "spec_brainstorm_failed", "error": str(exc)}))
        return

    # Determine repo and branch from brainstorm result
    # Use the repo specified in the spec front matter; fall back to first available repo.
    from control_plane.spec_director.models import SpecFrontMatter
    try:
        fm = SpecFrontMatter.from_spec_text(result.spec_text)
        spec_repo_key = fm.repos[0] if fm.repos else (available_repos[0] if available_repos else "")
    except Exception:
        spec_repo_key = available_repos[0] if available_repos else ""

    repo_cfg = settings.repos.get(spec_repo_key) if settings.repos else None
    base_branch = getattr(repo_cfg, "default_branch", "main") if repo_cfg else "main"

    # Write spec
    spec_path = spec_writer.write(slug=result.slug, spec_text=result.spec_text)

    # Create Plane campaign tasks
    builder = CampaignBuilder(
        client=client,
        project_id=settings.plane.project_id,
        max_tasks=sd.max_tasks_per_campaign,
    )
    try:
        task_ids = builder.build(
            spec_text=result.spec_text,
            repo_key=spec_repo_key,
            base_branch=base_branch,
        )
    except Exception as exc:
        logger.error(json.dumps({"event": "spec_campaign_build_failed", "error": str(exc)}))
        spec_path.unlink(missing_ok=True)
        return

    # Record in state (thin index only)
    campaign_record = CampaignRecord(
        campaign_id=result.campaign_id,
        slug=result.slug,
        spec_file=str(spec_path),
        status="active",
        created_at=datetime.now(UTC).isoformat(),
    )
    state_mgr.add_campaign(campaign_record)

    # Archive drop-file after successful campaign creation
    if trigger.source.value == "drop_file":
        trigger_detector.archive_drop_file()

    logger.info(json.dumps({
        "event": "spec_campaign_created",
        "campaign_id": result.campaign_id,
        "slug": result.slug,
        "repo": spec_repo_key,
        "tasks_created": len(task_ids),
    }))
```

Also update the imports at the top of `main.py` to add `PhaseOrchestrator`:

```python
from control_plane.spec_director.phase_orchestrator import PhaseOrchestrator
```

And update `RecoveryService` instantiation to use the trimmed constructor (remove `stall_hours` and `spec_revision_budget` params).

- [ ] **Step 5: Run the smoke test**

```bash
python -m pytest tests/test_spec_campaign_source.py::test_main_run_once_with_no_trigger_and_no_campaigns -v
```

Expected: PASS.

- [ ] **Step 6: Run full suite**

```bash
python -m pytest tests/ -x -q 2>&1 | tail -20
```

Fix any remaining breakage.

- [ ] **Step 7: Commit**

```bash
git add src/control_plane/entrypoints/spec_director/main.py \
        tests/test_spec_campaign_source.py
git commit -m "feat: rewrite spec director main loop — phase orchestration first, new trigger interface"
```

---

## Task 8: Final verification

- [ ] **Step 1: Run the complete test suite**

```bash
python -m pytest tests/ -q 2>&1 | tail -30
```

Expected: all tests pass (or same baseline failures as before this PR).

- [ ] **Step 2: Verify the spec-campaign label fix is deployed**

```bash
grep -n "spec-campaign" src/control_plane/entrypoints/worker/main.py
```

Expected: `_AUTO_SOURCES = {..., "spec-campaign"}` on line ~1084.

- [ ] **Step 3: Verify phase_orchestrator is imported in main**

```bash
grep "PhaseOrchestrator" src/control_plane/entrypoints/spec_director/main.py
```

Expected: `from control_plane.spec_director.phase_orchestrator import PhaseOrchestrator`

- [ ] **Step 4: Verify revise_spec is gone**

```bash
grep -rn "revise_spec" src/ tests/
```

Expected: no matches.

- [ ] **Step 5: Verify insight_snapshot is gone**

```bash
grep -rn "insight_snapshot" src/control_plane/spec_director/
```

Expected: no matches.

- [ ] **Step 6: Verify PLANE_LABEL trigger is gone**

```bash
grep -rn "plane_label\|PLANE_LABEL\|_check_plane_label" src/control_plane/spec_director/
```

Expected: no matches.

- [ ] **Step 7: Commit if any minor cleanup was needed, otherwise done**

```bash
git add -p  # stage any remaining cleanup
git commit -m "chore: final cleanup for spec-director redesign"
```
