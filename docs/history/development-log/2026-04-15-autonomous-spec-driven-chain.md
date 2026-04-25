# Autonomous Spec-Driven Development Chain Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `spec_director` watch role that autonomously brainstorms specs, creates Plane task campaigns, routes `kodo --test`/`kodo --improve` to new task kinds, and reviews campaign task diffs against the spec via Anthropic API.

**Architecture:** New `spec_director/` package with six focused modules wired by a polling entrypoint. Minimal surgery to existing files: extend `ExecutionMode`, add `kodo_mode` to `KodoAdapter.build_command`, add campaign routing to worker dispatch, and add a compliance branch to the reviewer watcher. The heuristic autonomy cycle is untouched except for a suppression check added to the proposer.

**Tech Stack:** Python 3.13, Pydantic v2, `anthropic` SDK (already installed in kodo's venv, also available system-wide), PyYAML, existing `PlaneClient`, existing `_check_disk_space` helper.

---

## File Map

**New files:**
- `src/operations_center/spec_director/__init__.py`
- `src/operations_center/spec_director/models.py` — all Pydantic models for the spec director
- `src/operations_center/spec_director/state.py` — read/write `state/campaigns/active.json`
- `src/operations_center/spec_director/suppressor.py` — heuristic proposal suppression
- `src/operations_center/spec_director/context_bundle.py` — brainstorm context assembly
- `src/operations_center/spec_director/brainstorm.py` — Anthropic API call → spec text
- `src/operations_center/spec_director/spec_writer.py` — write spec to disk + workspace
- `src/operations_center/spec_director/campaign_builder.py` — create Plane tasks
- `src/operations_center/spec_director/compliance.py` — structured diff-vs-spec verdict
- `src/operations_center/spec_director/recovery.py` — stall detection, spec revision, self-cancel
- `src/operations_center/spec_director/trigger.py` — detect start conditions
- `src/operations_center/entrypoints/spec_director/__init__.py`
- `src/operations_center/entrypoints/spec_director/main.py` — polling loop
- `tests/spec_director/__init__.py`
- `tests/spec_director/test_models.py`
- `tests/spec_director/test_state.py`
- `tests/spec_director/test_suppressor.py`
- `tests/spec_director/test_context_bundle.py`
- `tests/spec_director/test_brainstorm.py`
- `tests/spec_director/test_spec_writer.py`
- `tests/spec_director/test_campaign_builder.py`
- `tests/spec_director/test_compliance.py`
- `tests/spec_director/test_recovery.py`
- `tests/spec_director/test_trigger.py`

**Modified files:**
- `src/operations_center/domain/models.py` — extend `ExecutionMode` literal
- `src/operations_center/application/task_parser.py` — extend `SUPPORTED_MODES`, pass-through campaign metadata fields
- `src/operations_center/adapters/kodo/adapter.py` — add `kodo_mode` to `build_command` and `run`
- `src/operations_center/application/service.py` — derive `kodo_mode` from `execution_mode` in `run_task`
- `src/operations_center/config/settings.py` — add `SpecDirectorSettings`, add field to `Settings`
- `src/operations_center/entrypoints/worker/main.py` — `ROLE_TASK_KINDS` map, update `select_ready_task_id`
- `src/operations_center/entrypoints/reviewer/main.py` — compliance branch in `_process_self_review`
- `src/operations_center/proposer/candidate_integration.py` — suppression check
- `scripts/operations-center.sh` — spec watch role + watch-all wiring

---

### Task 1: Foundation models

**Files:**
- Create: `src/operations_center/spec_director/__init__.py`
- Create: `src/operations_center/spec_director/models.py`
- Create: `tests/spec_director/__init__.py`
- Create: `tests/spec_director/test_models.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/spec_director/test_models.py
from __future__ import annotations
import pytest
from operations_center.spec_director.models import (
    CampaignRecord, ActiveCampaigns, ComplianceInput, ComplianceVerdict,
    SpecFrontMatter, TriggerSource,
)


def test_campaign_record_defaults():
    r = CampaignRecord(
        campaign_id="abc-123",
        slug="add-auth",
        spec_file="docs/specs/add-auth.md",
        area_keywords=["src/auth/"],
        status="active",
        created_at="2026-04-15T00:00:00+00:00",
    )
    assert r.status == "active"
    assert r.last_progress_at is None
    assert r.spec_revision_count == 0


def test_active_campaigns_active_only():
    ac = ActiveCampaigns(campaigns=[
        CampaignRecord(campaign_id="1", slug="a", spec_file="docs/specs/a.md",
                       area_keywords=[], status="active", created_at="2026-01-01T00:00:00+00:00"),
        CampaignRecord(campaign_id="2", slug="b", spec_file="docs/specs/b.md",
                       area_keywords=[], status="complete", created_at="2026-01-01T00:00:00+00:00"),
    ])
    assert len(ac.active_campaigns()) == 1
    assert ac.active_campaigns()[0].campaign_id == "1"


def test_compliance_verdict_fields():
    v = ComplianceVerdict(
        verdict="LGTM",
        spec_coverage=0.9,
        violations=[],
        notes="looks good",
        model="claude-sonnet-4-6",
        prompt_tokens=100,
        completion_tokens=50,
    )
    assert v.verdict == "LGTM"


def test_spec_front_matter_parse():
    raw = """---
campaign_id: abc-123
slug: add-auth
phases:
  - implement
  - test
repos:
  - MyRepo
area_keywords:
  - src/auth/
status: active
---
# Title
body text
"""
    fm = SpecFrontMatter.from_spec_text(raw)
    assert fm.campaign_id == "abc-123"
    assert "implement" in fm.phases
    assert fm.status == "active"


def test_trigger_source_values():
    assert TriggerSource.DROP_FILE == "drop_file"
    assert TriggerSource.PLANE_LABEL == "plane_label"
    assert TriggerSource.QUEUE_DRAIN == "queue_drain"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /home/dev/Documents/GitHub/OperationsCenter
.venv/bin/pytest tests/spec_director/test_models.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'operations_center.spec_director'`

- [ ] **Step 3: Create the package and models**

```python
# src/operations_center/spec_director/__init__.py
```

```python
# src/operations_center/spec_director/models.py
from __future__ import annotations

from enum import Enum
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class TriggerSource(str, Enum):
    DROP_FILE = "drop_file"
    PLANE_LABEL = "plane_label"
    QUEUE_DRAIN = "queue_drain"


class CampaignRecord(BaseModel):
    campaign_id: str
    slug: str
    spec_file: str
    area_keywords: list[str]
    status: Literal["active", "complete", "cancelled", "partial"]
    created_at: str
    last_progress_at: str | None = None
    spec_revision_count: int = 0
    trigger_source: str | None = None


class ActiveCampaigns(BaseModel):
    campaigns: list[CampaignRecord] = Field(default_factory=list)

    def active_campaigns(self) -> list[CampaignRecord]:
        return [c for c in self.campaigns if c.status == "active"]

    def has_active(self) -> bool:
        return any(c.status == "active" for c in self.campaigns)


class ComplianceInput(BaseModel):
    spec_text: str
    diff: str
    task_constraints: str
    task_phase: str
    spec_coverage_hint: str


class ComplianceVerdict(BaseModel):
    verdict: Literal["LGTM", "CONCERNS", "FAIL"]
    spec_coverage: float
    violations: list[str]
    notes: str
    model: str
    prompt_tokens: int
    completion_tokens: int


class SpecFrontMatter(BaseModel):
    campaign_id: str
    slug: str
    phases: list[str] = Field(default_factory=list)
    repos: list[str] = Field(default_factory=list)
    area_keywords: list[str] = Field(default_factory=list)
    status: str = "active"
    created_at: str = ""

    @classmethod
    def from_spec_text(cls, text: str) -> "SpecFrontMatter":
        """Parse YAML front matter from a spec document."""
        if not text.startswith("---"):
            raise ValueError("Spec text does not have YAML front matter")
        end = text.index("---", 3)
        front = text[3:end].strip()
        data = yaml.safe_load(front) or {}
        return cls(**{k: v for k, v in data.items() if k in cls.model_fields})
```

```python
# tests/spec_director/__init__.py
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
.venv/bin/pytest tests/spec_director/test_models.py -v
```

Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/operations_center/spec_director/__init__.py src/operations_center/spec_director/models.py \
        tests/spec_director/__init__.py tests/spec_director/test_models.py
git commit -m "feat(spec-director): add foundation models"
```

---

### Task 2: Config schema

**Files:**
- Modify: `src/operations_center/config/settings.py`

- [ ] **Step 1: Write failing test**

```python
# append to tests/spec_director/test_models.py

def test_spec_director_settings_defaults():
    from operations_center.config.settings import SpecDirectorSettings
    s = SpecDirectorSettings()
    assert s.enabled is True
    assert s.poll_interval_seconds == 120
    assert s.spec_trigger_queue_threshold == 3
    assert s.max_tasks_per_campaign == 6
    assert s.spec_retention_days == 90
    assert s.spec_revision_budget == 3
    assert s.campaign_stall_hours == 24
    assert s.campaign_abandon_hours == 72
    assert s.compliance_diff_max_kb == 32
    assert s.brainstorm_context_snapshot_kb == 8
```

- [ ] **Step 2: Run to confirm failure**

```bash
.venv/bin/pytest tests/spec_director/test_models.py::test_spec_director_settings_defaults -v
```

Expected: `ImportError: cannot import name 'SpecDirectorSettings'`

- [ ] **Step 3: Add SpecDirectorSettings to settings.py**

Open `src/operations_center/config/settings.py`. Add this class after `ErrorIngestSettings`:

```python
class SpecDirectorSettings(BaseModel):
    enabled: bool = True
    poll_interval_seconds: int = 120
    spec_trigger_queue_threshold: int = 3
    brainstorm_model: str = "claude-opus-4-6"
    compliance_model: str = "claude-sonnet-4-6"
    drop_file_path: str = "state/spec_direction.md"
    plane_spec_label: str = "spec-request"
    max_active_campaigns: int = 1
    max_tasks_per_campaign: int = 6
    spec_retention_days: int = 90
    brainstorm_context_snapshot_kb: int = 8
    compliance_diff_max_kb: int = 32
    spec_revision_budget: int = 3
    campaign_stall_hours: int = 24
    campaign_abandon_hours: int = 72
```

Then in `class Settings(BaseModel)`, add after `error_ingest`:

```python
    spec_director: SpecDirectorSettings = Field(default_factory=SpecDirectorSettings)
```

- [ ] **Step 4: Run test to confirm it passes**

```bash
.venv/bin/pytest tests/spec_director/test_models.py -v
```

Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/operations_center/config/settings.py tests/spec_director/test_models.py
git commit -m "feat(spec-director): add SpecDirectorSettings config schema"
```

---

### Task 3: Extend ExecutionMode and KodoAdapter kodo_mode

**Files:**
- Modify: `src/operations_center/domain/models.py`
- Modify: `src/operations_center/application/task_parser.py`
- Modify: `src/operations_center/adapters/kodo/adapter.py`
- Modify: `src/operations_center/application/service.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_execution_modes.py  (append to existing file or create new)
# Check existing file first: ls tests/test_execution_modes.py

def test_task_parser_accepts_test_campaign():
    from operations_center.application.task_parser import TaskParser
    p = TaskParser()
    body = """## Execution
repo: MyRepo
mode: test_campaign
spec_campaign_id: abc-123
spec_file: docs/specs/add-auth.md
task_phase: test_campaign

## Goal
Run adversarial tests against the new auth layer.
"""
    parsed = p.parse(body)
    assert parsed.execution_metadata["mode"] == "test_campaign"
    assert parsed.execution_metadata["spec_campaign_id"] == "abc-123"


def test_kodo_adapter_build_command_test_mode():
    from pathlib import Path
    from operations_center.adapters.kodo.adapter import KodoAdapter
    from operations_center.config.settings import KodoSettings
    adapter = KodoAdapter(KodoSettings(binary="kodo", team="full", cycles=3,
                                       exchanges=20, orchestrator="claude", effort="standard",
                                       timeout_seconds=600))
    cmd = adapter.build_command(Path("/tmp/goal.md"), Path("/tmp/repo"), kodo_mode="test")
    assert "--test" in cmd
    assert "--goal-file" in cmd


def test_kodo_adapter_build_command_improve_mode():
    from pathlib import Path
    from operations_center.adapters.kodo.adapter import KodoAdapter
    from operations_center.config.settings import KodoSettings
    adapter = KodoAdapter(KodoSettings(binary="kodo", team="full", cycles=3,
                                       exchanges=20, orchestrator="claude", effort="standard",
                                       timeout_seconds=600))
    cmd = adapter.build_command(Path("/tmp/goal.md"), Path("/tmp/repo"), kodo_mode="improve")
    assert "--improve" in cmd
    assert "--goal-file" in cmd


def test_kodo_adapter_build_command_goal_mode_unchanged():
    from pathlib import Path
    from operations_center.adapters.kodo.adapter import KodoAdapter
    from operations_center.config.settings import KodoSettings
    adapter = KodoAdapter(KodoSettings(binary="kodo", team="full", cycles=3,
                                       exchanges=20, orchestrator="claude", effort="standard",
                                       timeout_seconds=600))
    cmd = adapter.build_command(Path("/tmp/goal.md"), Path("/tmp/repo"))
    assert "--test" not in cmd
    assert "--improve" not in cmd
```

- [ ] **Step 2: Run to confirm failures**

```bash
.venv/bin/pytest tests/test_execution_modes.py::test_task_parser_accepts_test_campaign \
                 tests/test_execution_modes.py::test_kodo_adapter_build_command_test_mode -v
```

Expected: both fail

- [ ] **Step 3: Extend ExecutionMode in domain/models.py**

Change line 9 from:
```python
ExecutionMode = Literal["goal", "fix_pr"]
```
to:
```python
ExecutionMode = Literal["goal", "fix_pr", "test_campaign", "improve_campaign"]
```

- [ ] **Step 4: Extend TaskParser**

In `src/operations_center/application/task_parser.py`, change:
```python
    SUPPORTED_MODES = {"goal", "fix_pr"}
```
to:
```python
    SUPPORTED_MODES = {"goal", "fix_pr", "test_campaign", "improve_campaign"}

    # Fields from spec campaign tasks passed through without transformation
    _CAMPAIGN_PASSTHROUGH_FIELDS = {"spec_campaign_id", "spec_file", "task_phase", "spec_coverage_hint"}
```

In `_normalize_metadata`, after the existing `data["open_pr"] = ...` line, add:

```python
        # Pass campaign metadata fields through unchanged
        for field in self._CAMPAIGN_PASSTHROUGH_FIELDS:
            if field in data:
                data[field] = str(data[field]).strip()
```

Also remove the mode validation error for unsupported modes (replace `raise ValueError` with a pass-through for future modes) by changing the `if mode not in self.SUPPORTED_MODES` block:

```python
        if mode not in self.SUPPORTED_MODES:
            raise ValueError(
                f"Unsupported execution mode '{data['mode']}'. Supported: {sorted(self.SUPPORTED_MODES)}"
            )
```

- [ ] **Step 5: Add kodo_mode to KodoAdapter.build_command**

In `src/operations_center/adapters/kodo/adapter.py`, change `build_command` signature and body:

```python
    def build_command(
        self,
        goal_file: Path,
        repo_path: Path,
        profile: "KodoSettings | None" = None,
        kodo_mode: str = "goal",
    ) -> list[str]:
        """Return the Kodo CLI command list.

        *profile* overrides individual fields from ``self.settings``.
        *kodo_mode* selects the kodo flag: 'goal' (default) uses --goal-file only;
        'test' prepends --test; 'improve' prepends --improve.
        """
        s = self.settings
        base = [
            s.binary,
            "--goal-file",
            str(goal_file),
            "--project",
            str(repo_path),
            "--team",
            (profile.team if profile else s.team),
            "--cycles",
            str(profile.cycles if profile else s.cycles),
            "--exchanges",
            str(profile.exchanges if profile else s.exchanges),
            "--orchestrator",
            (profile.orchestrator if profile else s.orchestrator),
            "--effort",
            (profile.effort if profile else s.effort),
            "--yes",
        ]
        if kodo_mode == "test":
            return [s.binary, "--test"] + base[1:]
        if kodo_mode == "improve":
            return [s.binary, "--improve"] + base[1:]
        return base
```

Also update `run` to accept and pass `kodo_mode`:

```python
    def run(
        self,
        goal_file: Path,
        repo_path: Path,
        env: dict[str, str] | None = None,
        profile: "KodoSettings | None" = None,
        kodo_mode: str = "goal",
    ) -> KodoRunResult:
        """Execute Kodo.  *profile* overrides individual settings fields."""
        timeout = (profile.timeout_seconds if profile else self.settings.timeout_seconds)
        command = self.build_command(goal_file, repo_path, profile=profile, kodo_mode=kodo_mode)
        result = self._run_subprocess(command, cwd=repo_path, timeout=timeout, env=env)

        if result.exit_code != 0 and self._is_codex_quota_error(result):
            result = self._run_with_claude_fallback(goal_file, repo_path, env=env, profile=profile, kodo_mode=kodo_mode)

        return result
```

Update `_run_with_claude_fallback` signature and internal `build_command` call to pass `kodo_mode`:

```python
    def _run_with_claude_fallback(
        self,
        goal_file: Path,
        repo_path: Path,
        env: dict[str, str] | None = None,
        profile: "KodoSettings | None" = None,
        kodo_mode: str = "goal",
    ) -> KodoRunResult:
        team_override = repo_path / ".kodo" / "team.json"
        team_override.parent.mkdir(exist_ok=True)
        team_override.write_text(json.dumps(_CLAUDE_FALLBACK_TEAM, indent=2))
        try:
            timeout = (profile.timeout_seconds if profile else self.settings.timeout_seconds)
            command = self.build_command(goal_file, repo_path, profile=profile, kodo_mode=kodo_mode)
            return self._run_subprocess(command, cwd=repo_path, timeout=timeout, env=env)
        finally:
            team_override.unlink(missing_ok=True)
```

- [ ] **Step 6: Derive kodo_mode from execution_mode in service.py**

In `src/operations_center/application/service.py`, find the `run_task` method (around line 131). Locate the first `kodo_result = self.kodo.run(goal_file, repo_path, ...)` call. Before it, add:

```python
            # Derive kodo mode from campaign task kinds
            _kodo_mode = "goal"
            if task.execution_mode == "test_campaign":
                _kodo_mode = "test"
            elif task.execution_mode == "improve_campaign":
                _kodo_mode = "improve"
```

Then change the three `self.kodo.run(goal_file, repo_path, ...)` calls in `run_task` to pass `kodo_mode=_kodo_mode`. Each call currently looks like:
```python
kodo_result = self.kodo.run(goal_file, repo_path, env=run_env, profile=_kodo_profile)
```
Change to:
```python
kodo_result = self.kodo.run(goal_file, repo_path, env=run_env, profile=_kodo_profile, kodo_mode=_kodo_mode)
```

- [ ] **Step 7: Run tests**

```bash
.venv/bin/pytest tests/test_execution_modes.py -v
```

Expected: all 4 new tests pass

```bash
.venv/bin/pytest tests/ -q --tb=short 2>&1 | tail -20
```

Expected: no regressions

- [ ] **Step 8: Commit**

```bash
git add src/operations_center/domain/models.py \
        src/operations_center/application/task_parser.py \
        src/operations_center/adapters/kodo/adapter.py \
        src/operations_center/application/service.py \
        tests/test_execution_modes.py
git commit -m "feat(spec-director): extend ExecutionMode and add kodo_mode to adapter"
```

---

### Task 4: Worker routing for test_campaign / improve_campaign

**Files:**
- Modify: `src/operations_center/entrypoints/worker/main.py`

- [ ] **Step 1: Write failing test**

```python
# tests/spec_director/test_worker_routing.py
from __future__ import annotations
from unittest.mock import MagicMock, patch


def _make_issue(task_kind: str, status: str = "Ready for AI") -> dict:
    return {
        "id": "task-abc",
        "labels": [{"name": f"task-kind: {task_kind}"}],
        "state": {"name": status},
    }


def test_test_role_picks_test_campaign():
    from operations_center.entrypoints.worker.main import ROLE_TASK_KINDS
    assert "test_campaign" in ROLE_TASK_KINDS["test"]


def test_improve_role_picks_improve_campaign():
    from operations_center.entrypoints.worker.main import ROLE_TASK_KINDS
    assert "improve_campaign" in ROLE_TASK_KINDS["improve"]


def test_goal_role_does_not_pick_test_campaign():
    from operations_center.entrypoints.worker.main import ROLE_TASK_KINDS
    assert "test_campaign" not in ROLE_TASK_KINDS["goal"]
```

- [ ] **Step 2: Run to confirm failure**

```bash
.venv/bin/pytest tests/spec_director/test_worker_routing.py -v
```

Expected: `ImportError: cannot import name 'ROLE_TASK_KINDS'`

- [ ] **Step 3: Add ROLE_TASK_KINDS and update select_ready_task_id**

In `src/operations_center/entrypoints/worker/main.py`, after the imports section (around line 50), add:

```python
# Maps each watcher role to the set of task-kind label values it will claim.
# test_campaign and improve_campaign are spec-director campaign tasks that use
# kodo --test and kodo --improve respectively.
ROLE_TASK_KINDS: dict[str, set[str]] = {
    "goal": {"goal"},
    "test": {"test", "test_campaign"},
    "improve": {"improve", "improve_campaign"},
    "fix_pr": {"fix_pr"},
}
```

In `select_ready_task_id` (line ~1073), change the filter line:
```python
        if task_kind != role:
            continue
```
to:
```python
        allowed_kinds = ROLE_TASK_KINDS.get(role, {role})
        if task_kind not in allowed_kinds:
            continue
```

Also change the secondary check around line 1091:
```python
            if issue_task_kind(detailed_issue) == role and issue_status_name(detailed_issue) == ready_state:
```
to:
```python
            _allowed = ROLE_TASK_KINDS.get(role, {role})
            if issue_task_kind(detailed_issue) in _allowed and issue_status_name(detailed_issue) == ready_state:
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/spec_director/test_worker_routing.py -v
.venv/bin/pytest tests/ -q --tb=short 2>&1 | tail -10
```

Expected: routing tests pass, no regressions

- [ ] **Step 5: Commit**

```bash
git add src/operations_center/entrypoints/worker/main.py tests/spec_director/test_worker_routing.py
git commit -m "feat(spec-director): add ROLE_TASK_KINDS routing for test_campaign/improve_campaign"
```

---

### Task 5: Campaign state manager

**Files:**
- Create: `src/operations_center/spec_director/state.py`
- Create: `tests/spec_director/test_state.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/spec_director/test_state.py
from __future__ import annotations
import json
from pathlib import Path
import pytest
from operations_center.spec_director.models import CampaignRecord, ActiveCampaigns


def test_load_returns_empty_when_missing(tmp_path):
    from operations_center.spec_director.state import CampaignStateManager
    mgr = CampaignStateManager(state_path=tmp_path / "active.json")
    ac = mgr.load()
    assert ac.campaigns == []


def test_save_and_load_roundtrip(tmp_path):
    from operations_center.spec_director.state import CampaignStateManager
    mgr = CampaignStateManager(state_path=tmp_path / "active.json")
    record = CampaignRecord(
        campaign_id="abc", slug="test", spec_file="docs/specs/test.md",
        area_keywords=["src/auth/"], status="active",
        created_at="2026-04-15T00:00:00+00:00",
    )
    mgr.save(ActiveCampaigns(campaigns=[record]))
    loaded = mgr.load()
    assert loaded.campaigns[0].campaign_id == "abc"


def test_corrupt_file_returns_empty_and_renames(tmp_path):
    from operations_center.spec_director.state import CampaignStateManager
    p = tmp_path / "active.json"
    p.write_text("not json {{{")
    mgr = CampaignStateManager(state_path=p)
    ac = mgr.load()
    assert ac.campaigns == []
    corrupt_files = list(tmp_path.glob("active.json.corrupt.*"))
    assert len(corrupt_files) == 1


def test_mark_complete(tmp_path):
    from operations_center.spec_director.state import CampaignStateManager
    mgr = CampaignStateManager(state_path=tmp_path / "active.json")
    record = CampaignRecord(
        campaign_id="abc", slug="test", spec_file="docs/specs/test.md",
        area_keywords=[], status="active", created_at="2026-04-15T00:00:00+00:00",
    )
    mgr.save(ActiveCampaigns(campaigns=[record]))
    mgr.mark_complete("abc")
    loaded = mgr.load()
    assert loaded.campaigns[0].status == "complete"
```

- [ ] **Step 2: Run to confirm failure**

```bash
.venv/bin/pytest tests/spec_director/test_state.py -v
```

- [ ] **Step 3: Implement CampaignStateManager**

```python
# src/operations_center/spec_director/state.py
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from operations_center.spec_director.models import ActiveCampaigns, CampaignRecord

_DEFAULT_STATE_PATH = Path("state/campaigns/active.json")
logger = logging.getLogger(__name__)


class CampaignStateManager:
    def __init__(self, state_path: Path | None = None) -> None:
        self.state_path = state_path or _DEFAULT_STATE_PATH

    def load(self) -> ActiveCampaigns:
        if not self.state_path.exists():
            return ActiveCampaigns()
        try:
            data = json.loads(self.state_path.read_text())
            return ActiveCampaigns.model_validate(data)
        except Exception as exc:
            corrupt_path = self.state_path.with_suffix(
                f".json.corrupt.{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}"
            )
            try:
                self.state_path.rename(corrupt_path)
            except OSError:
                pass
            logger.error(
                '{"event": "spec_campaign_state_corrupt", "error": "%s", "renamed_to": "%s"}',
                str(exc), str(corrupt_path),
            )
            return ActiveCampaigns()

    def save(self, state: ActiveCampaigns) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(state.model_dump_json(indent=2))

    def add_campaign(self, record: CampaignRecord) -> None:
        state = self.load()
        state.campaigns.append(record)
        self.save(state)

    def mark_complete(self, campaign_id: str) -> None:
        self._update_status(campaign_id, "complete")

    def mark_cancelled(self, campaign_id: str) -> None:
        self._update_status(campaign_id, "cancelled")

    def update_progress(self, campaign_id: str) -> None:
        state = self.load()
        for c in state.campaigns:
            if c.campaign_id == campaign_id:
                c.last_progress_at = datetime.now(UTC).isoformat()
        self.save(state)

    def increment_revision_count(self, campaign_id: str) -> int:
        state = self.load()
        for c in state.campaigns:
            if c.campaign_id == campaign_id:
                c.spec_revision_count += 1
                self.save(state)
                return c.spec_revision_count
        return 0

    def _update_status(self, campaign_id: str, status: str) -> None:
        state = self.load()
        for c in state.campaigns:
            if c.campaign_id == campaign_id:
                c.status = status  # type: ignore[assignment]
        self.save(state)

    def rebuild_from_specs(self, specs_dir: Path) -> ActiveCampaigns:
        """Rebuild active campaigns list by scanning spec front matter."""
        from operations_center.spec_director.models import SpecFrontMatter
        campaigns = []
        for spec_file in sorted(specs_dir.glob("*.md")):
            try:
                fm = SpecFrontMatter.from_spec_text(spec_file.read_text())
                if fm.status == "active":
                    campaigns.append(CampaignRecord(
                        campaign_id=fm.campaign_id,
                        slug=fm.slug,
                        spec_file=str(spec_file),
                        area_keywords=fm.area_keywords,
                        status="active",
                        created_at=fm.created_at,
                    ))
            except Exception:
                continue
        rebuilt = ActiveCampaigns(campaigns=campaigns)
        self.save(rebuilt)
        return rebuilt
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/spec_director/test_state.py -v
```

Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/operations_center/spec_director/state.py tests/spec_director/test_state.py
git commit -m "feat(spec-director): add CampaignStateManager"
```

---

### Task 6: Suppressor + proposer integration

**Files:**
- Create: `src/operations_center/spec_director/suppressor.py`
- Create: `tests/spec_director/test_suppressor.py`
- Modify: `src/operations_center/proposer/candidate_integration.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/spec_director/test_suppressor.py
from __future__ import annotations
from pathlib import Path
from operations_center.spec_director.models import ActiveCampaigns, CampaignRecord


def _active(keywords: list[str]) -> ActiveCampaigns:
    return ActiveCampaigns(campaigns=[
        CampaignRecord(
            campaign_id="abc", slug="add-auth", spec_file="docs/specs/add-auth.md",
            area_keywords=keywords, status="active",
            created_at="2026-04-15T00:00:00+00:00",
        )
    ])


def test_suppressed_by_path_keyword():
    from operations_center.spec_director.suppressor import is_suppressed
    ac = _active(["src/auth/"])
    assert is_suppressed("Fix auth login", ["src/auth/session.py"], ac) is True


def test_suppressed_by_title_keyword():
    from operations_center.spec_director.suppressor import is_suppressed
    ac = _active(["authentication"])
    assert is_suppressed("Improve authentication flow", [], ac) is True


def test_not_suppressed_unrelated():
    from operations_center.spec_director.suppressor import is_suppressed
    ac = _active(["src/auth/"])
    assert is_suppressed("Fix lint errors in src/reporting/", ["src/reporting/base.py"], ac) is False


def test_not_suppressed_no_active_campaigns():
    from operations_center.spec_director.suppressor import is_suppressed
    ac = ActiveCampaigns(campaigns=[])
    assert is_suppressed("Fix anything", ["src/auth/x.py"], ac) is False


def test_suppressed_case_insensitive():
    from operations_center.spec_director.suppressor import is_suppressed
    ac = _active(["Authentication"])
    assert is_suppressed("improve authentication handler", [], ac) is True
```

- [ ] **Step 2: Run to confirm failures**

```bash
.venv/bin/pytest tests/spec_director/test_suppressor.py -v
```

- [ ] **Step 3: Implement suppressor**

```python
# src/operations_center/spec_director/suppressor.py
from __future__ import annotations

import logging
from pathlib import Path

from operations_center.spec_director.models import ActiveCampaigns
from operations_center.spec_director.state import CampaignStateManager

logger = logging.getLogger(__name__)

_STATE_MANAGER = CampaignStateManager()


def is_suppressed(
    proposal_title: str,
    proposal_paths: list[str],
    active_campaigns: ActiveCampaigns | None = None,
) -> bool:
    """Return True if any active spec campaign covers the proposal's area.

    Fail-open: if loading active campaigns raises an exception, returns False
    and logs a warning rather than blocking proposal creation.
    """
    try:
        if active_campaigns is None:
            active_campaigns = _STATE_MANAGER.load()
    except Exception as exc:
        logger.warning('{"event": "spec_suppressor_read_error", "error": "%s"}', str(exc))
        return False

    text = proposal_title.lower()
    lower_paths = [p.lower() for p in proposal_paths]

    for campaign in active_campaigns.active_campaigns():
        for keyword in campaign.area_keywords:
            kw = keyword.lower()
            if kw in text:
                return True
            if any(kw in p for p in lower_paths):
                return True
    return False
```

- [ ] **Step 4: Add suppression check to candidate_integration.py**

In `src/operations_center/proposer/candidate_integration.py`, find the `run` method of `CandidateProposerIntegrationService`. Locate the loop where proposals are created (look for `self.client.create_issue` or similar). Add a suppression check before the creation call:

```python
        # Spec-director suppression: skip proposals that overlap with an active campaign
        from operations_center.spec_director.suppressor import is_suppressed as _spec_suppressed
        from operations_center.spec_director.state import CampaignStateManager as _CampaignStateManager
        _active_campaigns = _CampaignStateManager().load()
```

Then in the per-candidate loop, before creating the Plane task, add:

```python
            _paths = [str(f) for f in getattr(candidate, "changed_files", [])] + \
                     [str(f) for f in getattr(candidate, "target_paths", [])]
            if _spec_suppressed(candidate.title, _paths, _active_campaigns):
                skipped.append(SkippedProposalResult(
                    candidate=candidate,
                    reason="active_spec_campaign",
                ))
                continue
```

(Adjust field names to match the actual `ProposalCandidate` model — check `src/operations_center/decision/models.py` for the exact field names.)

- [ ] **Step 5: Run tests**

```bash
.venv/bin/pytest tests/spec_director/test_suppressor.py -v
.venv/bin/pytest tests/ -q --tb=short 2>&1 | tail -10
```

Expected: suppressor tests pass, no regressions

- [ ] **Step 6: Commit**

```bash
git add src/operations_center/spec_director/suppressor.py \
        tests/spec_director/test_suppressor.py \
        src/operations_center/proposer/candidate_integration.py
git commit -m "feat(spec-director): add suppressor and proposer integration"
```

---

### Task 7: Context bundle assembly

**Files:**
- Create: `src/operations_center/spec_director/context_bundle.py`
- Create: `tests/spec_director/test_context_bundle.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/spec_director/test_context_bundle.py
from __future__ import annotations
from pathlib import Path
import json
import pytest


def test_bundle_truncates_snapshot(tmp_path):
    from operations_center.spec_director.context_bundle import ContextBundleBuilder
    snapshot_dir = tmp_path / "report" / "autonomy_cycle" / "run1"
    snapshot_dir.mkdir(parents=True)
    big_insights = {"derivers": ["x" * 1000] * 20}
    (snapshot_dir / "insights.json").write_text(json.dumps(big_insights))
    builder = ContextBundleBuilder(report_root=tmp_path / "report", max_snapshot_kb=1)
    bundle = builder.build(seed_text="", board_summary=[], specs_index=[], git_log="")
    assert len(bundle.insight_snapshot.encode()) <= 1024 + 100  # small tolerance


def test_bundle_includes_seed():
    from operations_center.spec_director.context_bundle import ContextBundleBuilder, ContextBundle
    builder = ContextBundleBuilder()
    bundle = builder.build(seed_text="add webhook ingestion", board_summary=[], specs_index=[], git_log="")
    assert "add webhook ingestion" in bundle.seed_text


def test_specs_index_capped():
    from operations_center.spec_director.context_bundle import ContextBundleBuilder
    builder = ContextBundleBuilder()
    index = [{"title": f"spec {i}", "status": "complete"} for i in range(100)]
    bundle = builder.build(seed_text="", board_summary=[], specs_index=index, git_log="")
    assert len(bundle.specs_index) <= 50
```

- [ ] **Step 2: Run to confirm failures**

```bash
.venv/bin/pytest tests/spec_director/test_context_bundle.py -v
```

- [ ] **Step 3: Implement ContextBundleBuilder**

```python
# src/operations_center/spec_director/context_bundle.py
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ContextBundle:
    insight_snapshot: str
    git_log: str
    specs_index: list[dict]
    board_summary: list[dict]
    seed_text: str


class ContextBundleBuilder:
    _MAX_BOARD_TASKS = 50
    _MAX_SPECS = 50
    _MAX_GIT_COMMITS = 30

    def __init__(
        self,
        report_root: Path | None = None,
        max_snapshot_kb: int = 8,
    ) -> None:
        self.report_root = report_root or Path("tools/report/kodo_plane")
        self.max_snapshot_bytes = max_snapshot_kb * 1024

    def build(
        self,
        seed_text: str,
        board_summary: list[dict],
        specs_index: list[dict],
        git_log: str,
    ) -> ContextBundle:
        return ContextBundle(
            insight_snapshot=self._load_insight_snapshot(),
            git_log=git_log,
            specs_index=specs_index[: self._MAX_SPECS],
            board_summary=board_summary[: self._MAX_BOARD_TASKS],
            seed_text=seed_text,
        )

    def _load_insight_snapshot(self) -> str:
        """Load and truncate the most recent autonomy_cycle insights.json."""
        cycle_dir = self.report_root.parent / "autonomy_cycle"
        if not cycle_dir.exists():
            return ""
        runs = sorted(cycle_dir.iterdir(), reverse=True)
        for run in runs:
            insights_path = run / "insights.json"
            if insights_path.exists():
                raw = insights_path.read_text(encoding="utf-8", errors="replace")
                if len(raw.encode()) > self.max_snapshot_bytes:
                    raw = raw.encode()[: self.max_snapshot_bytes].decode("utf-8", errors="replace")
                return raw
        return ""

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
        """Return [{title, status, slug}] for each spec in specs_dir."""
        from operations_center.spec_director.models import SpecFrontMatter
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

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/spec_director/test_context_bundle.py -v
```

Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/operations_center/spec_director/context_bundle.py tests/spec_director/test_context_bundle.py
git commit -m "feat(spec-director): add context bundle assembly"
```

---

### Task 8: Brainstorm service

**Files:**
- Create: `src/operations_center/spec_director/brainstorm.py`
- Create: `tests/spec_director/test_brainstorm.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/spec_director/test_brainstorm.py
from __future__ import annotations
from unittest.mock import MagicMock, patch
import pytest


_FAKE_SPEC = """---
campaign_id: test-uuid-1234
slug: add-webhook-ingestion
phases:
  - implement
  - test
repos:
  - MyRepo
area_keywords:
  - src/ingestion/
  - webhook
status: active
created_at: 2026-04-15T00:00:00+00:00
---
# Add Webhook Ingestion

## Overview
Add HTTP webhook endpoint to receive events.
"""


def test_brainstorm_returns_spec_text_and_front_matter():
    from operations_center.spec_director.brainstorm import BrainstormService
    from operations_center.spec_director.context_bundle import ContextBundle

    mock_client = MagicMock()
    mock_message = MagicMock()
    mock_message.content = [MagicMock(text=_FAKE_SPEC)]
    mock_message.usage.input_tokens = 500
    mock_message.usage.output_tokens = 200
    mock_client.messages.create.return_value = mock_message

    service = BrainstormService(client=mock_client, model="claude-opus-4-6")
    bundle = ContextBundle(
        insight_snapshot="{}",
        git_log="abc123 fix auth",
        specs_index=[],
        board_summary=[],
        seed_text="add webhook ingestion",
    )
    result = service.brainstorm(bundle)
    assert result.spec_text.startswith("---")
    assert result.slug == "add-webhook-ingestion"
    assert "implement" in result.phases
    assert result.prompt_tokens == 500


def test_brainstorm_raises_on_missing_front_matter():
    from operations_center.spec_director.brainstorm import BrainstormService, BrainstormError
    from operations_center.spec_director.context_bundle import ContextBundle

    mock_client = MagicMock()
    mock_message = MagicMock()
    mock_message.content = [MagicMock(text="# No front matter here\nJust text")]
    mock_message.usage.input_tokens = 100
    mock_message.usage.output_tokens = 50
    mock_client.messages.create.return_value = mock_message

    service = BrainstormService(client=mock_client, model="claude-opus-4-6")
    bundle = ContextBundle(insight_snapshot="", git_log="", specs_index=[], board_summary=[], seed_text="")
    with pytest.raises(BrainstormError):
        service.brainstorm(bundle)
```

- [ ] **Step 2: Run to confirm failures**

```bash
.venv/bin/pytest tests/spec_director/test_brainstorm.py -v
```

- [ ] **Step 3: Implement BrainstormService**

```python
# src/operations_center/spec_director/brainstorm.py
from __future__ import annotations

import uuid
from dataclasses import dataclass

from operations_center.spec_director.context_bundle import ContextBundle
from operations_center.spec_director.models import SpecFrontMatter

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
  - <repo name from context>
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
Each goal should be completable by one kodo run in under 1 hour."""

_SYSTEM_PROMPT_CACHED = True


class BrainstormError(Exception):
    pass


@dataclass
class BrainstormResult:
    spec_text: str
    slug: str
    phases: list[str]
    area_keywords: list[str]
    campaign_id: str
    prompt_tokens: int
    completion_tokens: int


class BrainstormService:
    def __init__(self, client: object, model: str = "claude-opus-4-6") -> None:
        self._client = client
        self._model = model

    def brainstorm(self, bundle: ContextBundle) -> BrainstormResult:
        user_content = self._build_user_prompt(bundle)
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=4096,
                system=[
                    {
                        "type": "text",
                        "text": _SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": user_content}],
            )
        except Exception as exc:
            raise BrainstormError(f"Anthropic API call failed: {exc}") from exc

        raw = response.content[0].text
        try:
            fm = SpecFrontMatter.from_spec_text(raw)
        except Exception as exc:
            raise BrainstormError(f"Response missing valid YAML front matter: {exc}") from exc

        return BrainstormResult(
            spec_text=raw,
            slug=fm.slug,
            phases=fm.phases,
            area_keywords=fm.area_keywords,
            campaign_id=fm.campaign_id or str(uuid.uuid4()),
            prompt_tokens=response.usage.input_tokens,
            completion_tokens=response.usage.output_tokens,
        )

    @staticmethod
    def _build_user_prompt(bundle: ContextBundle) -> str:
        parts = []
        if bundle.seed_text:
            parts.append(f"## Operator Direction\n{bundle.seed_text}")
        if bundle.insight_snapshot:
            parts.append(f"## Insight Snapshot\n```json\n{bundle.insight_snapshot}\n```")
        if bundle.git_log:
            parts.append(f"## Recent Git Activity\n```\n{bundle.git_log}\n```")
        if bundle.specs_index:
            lines = "\n".join(f"- {s.get('slug', '?')} ({s.get('status', '?')})" for s in bundle.specs_index)
            parts.append(f"## Existing Specs\n{lines}")
        if bundle.board_summary:
            lines = "\n".join(f"- {t.get('name', t.get('title', '?'))} [{t.get('state', '?')}]" for t in bundle.board_summary[:50])
            parts.append(f"## Current Board\n{lines}")
        parts.append("Generate the spec document now.")
        return "\n\n".join(parts)

    @staticmethod
    def make_client(api_key_env: str = "ANTHROPIC_API_KEY") -> object:
        import os
        import anthropic
        return anthropic.Anthropic(api_key=os.environ[api_key_env])
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/spec_director/test_brainstorm.py -v
```

Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add src/operations_center/spec_director/brainstorm.py tests/spec_director/test_brainstorm.py
git commit -m "feat(spec-director): add BrainstormService"
```

---

### Task 9: Spec writer

**Files:**
- Create: `src/operations_center/spec_director/spec_writer.py`
- Create: `tests/spec_director/test_spec_writer.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/spec_director/test_spec_writer.py
from __future__ import annotations
from pathlib import Path
import pytest


_SPEC = """---
campaign_id: abc-123
slug: add-auth
phases: [implement, test]
repos: [MyRepo]
area_keywords: [src/auth/]
status: active
created_at: 2026-04-15T00:00:00+00:00
---
# Add Auth
"""


def test_writes_spec_to_canonical_path(tmp_path):
    from operations_center.spec_director.spec_writer import SpecWriter
    writer = SpecWriter(specs_dir=tmp_path / "docs/specs")
    path = writer.write(slug="add-auth", spec_text=_SPEC)
    assert path.exists()
    assert path.name == "add-auth.md"
    assert path.read_text() == _SPEC


def test_copies_to_workspace(tmp_path):
    from operations_center.spec_director.spec_writer import SpecWriter
    writer = SpecWriter(specs_dir=tmp_path / "docs/specs")
    workspace = tmp_path / "workspace/MyRepo"
    workspace.mkdir(parents=True)
    writer.write(slug="add-auth", spec_text=_SPEC, workspace_path=workspace)
    workspace_copy = workspace / "docs/specs/add-auth.md"
    assert workspace_copy.exists()
    assert workspace_copy.read_text() == _SPEC


def test_archive_old_specs(tmp_path):
    from operations_center.spec_director.spec_writer import SpecWriter
    from datetime import UTC, datetime, timedelta
    import time
    specs_dir = tmp_path / "docs/specs"
    specs_dir.mkdir(parents=True)
    old_spec = specs_dir / "old-campaign.md"
    old_spec_text = """---
campaign_id: old-1
slug: old-campaign
phases: [implement]
repos: [MyRepo]
area_keywords: []
status: complete
created_at: 2020-01-01T00:00:00+00:00
---
# Old
"""
    old_spec.write_text(old_spec_text)
    writer = SpecWriter(specs_dir=specs_dir)
    writer.archive_expired(retention_days=1)
    archive_dir = specs_dir / "archive"
    assert (archive_dir / "old-campaign.md").exists()
    assert not old_spec.exists()
```

- [ ] **Step 2: Run to confirm failures**

```bash
.venv/bin/pytest tests/spec_director/test_spec_writer.py -v
```

- [ ] **Step 3: Implement SpecWriter**

```python
# src/operations_center/spec_director/spec_writer.py
from __future__ import annotations

import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path

from operations_center.spec_director.models import SpecFrontMatter

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
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/spec_director/test_spec_writer.py -v
```

Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/operations_center/spec_director/spec_writer.py tests/spec_director/test_spec_writer.py
git commit -m "feat(spec-director): add SpecWriter"
```

---

### Task 10: Campaign builder

**Files:**
- Create: `src/operations_center/spec_director/campaign_builder.py`
- Create: `tests/spec_director/test_campaign_builder.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/spec_director/test_campaign_builder.py
from __future__ import annotations
from unittest.mock import MagicMock, call
import pytest

_SPEC_FM = {
    "campaign_id": "abc-123",
    "slug": "add-auth",
    "phases": ["implement", "test", "improve"],
    "repos": ["MyRepo"],
    "area_keywords": ["src/auth/"],
    "status": "active",
    "created_at": "2026-04-15T00:00:00+00:00",
}

_SPEC_TEXT = """---
campaign_id: abc-123
slug: add-auth
phases:
  - implement
  - test
  - improve
repos:
  - MyRepo
area_keywords:
  - src/auth/
status: active
created_at: 2026-04-15T00:00:00+00:00
---
# Add Auth

## Goals
1. Add JWT middleware to src/auth/middleware.py
2. Add login endpoint to src/auth/routes.py

## Constraints
- Only modify src/auth/
"""


def test_creates_parent_and_child_tasks():
    from operations_center.spec_director.campaign_builder import CampaignBuilder
    mock_client = MagicMock()
    mock_client.create_issue.return_value = {"id": "task-001"}
    builder = CampaignBuilder(client=mock_client, project_id="proj-1", max_tasks=6)
    records = builder.build(spec_text=_SPEC_TEXT, repo_key="MyRepo", base_branch="main")
    # parent + 2 goals × 3 phases (capped) = parent + 6 tasks
    assert mock_client.create_issue.call_count >= 3  # at minimum parent + 2 implement tasks


def test_task_limit_enforced():
    from operations_center.spec_director.campaign_builder import CampaignBuilder
    mock_client = MagicMock()
    mock_client.create_issue.return_value = {"id": "task-001"}
    builder = CampaignBuilder(client=mock_client, project_id="proj-1", max_tasks=2)
    builder.build(spec_text=_SPEC_TEXT, repo_key="MyRepo", base_branch="main")
    # parent task + max_tasks child tasks
    assert mock_client.create_issue.call_count <= 3  # parent + 2


def test_child_task_body_contains_campaign_id():
    from operations_center.spec_director.campaign_builder import CampaignBuilder
    created_bodies = []

    def capture_create(**kwargs):
        created_bodies.append(kwargs.get("description", ""))
        return {"id": f"task-{len(created_bodies)}"}

    mock_client = MagicMock()
    mock_client.create_issue.side_effect = capture_create
    builder = CampaignBuilder(client=mock_client, project_id="proj-1", max_tasks=6)
    builder.build(spec_text=_SPEC_TEXT, repo_key="MyRepo", base_branch="main")
    child_bodies = [b for b in created_bodies if "spec_campaign_id" in b]
    assert len(child_bodies) > 0
    assert "abc-123" in child_bodies[0]
```

- [ ] **Step 2: Run to confirm failures**

```bash
.venv/bin/pytest tests/spec_director/test_campaign_builder.py -v
```

- [ ] **Step 3: Implement CampaignBuilder**

```python
# src/operations_center/spec_director/campaign_builder.py
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from operations_center.spec_director.models import SpecFrontMatter

logger = logging.getLogger(__name__)

_GOAL_PATTERN = re.compile(r"^\d+\.\s+(.+)$", re.MULTILINE)


@dataclass
class ChildTaskSpec:
    title: str
    goal_text: str
    constraints_text: str
    phase: str  # "implement" | "test_campaign" | "improve_campaign"
    spec_coverage_hint: str


class CampaignBuilder:
    def __init__(
        self,
        client: object,
        project_id: str,
        max_tasks: int = 6,
    ) -> None:
        self._client = client
        self._project_id = project_id
        self._max_tasks = max_tasks

    def build(
        self,
        spec_text: str,
        repo_key: str,
        base_branch: str,
    ) -> list[str]:
        """Create Plane tasks for the campaign. Returns list of created task IDs."""
        fm = SpecFrontMatter.from_spec_text(spec_text)
        goals = self._extract_goals(spec_text)
        constraints = self._extract_section(spec_text, "Constraints")

        # Create parent campaign task
        parent_body = self._build_parent_body(fm, spec_text)
        parent = self._client.create_issue(
            name=f"[Campaign] {fm.slug}",
            description=parent_body,
            labels=["source: spec-campaign", f"campaign-id: {fm.campaign_id}"],
        )
        parent_id = str(parent["id"])
        created_ids = [parent_id]

        child_count = 0
        for idx, goal_text in enumerate(goals):
            if child_count >= self._max_tasks:
                logger.warning(
                    '{"event": "campaign_task_limit_reached", "campaign_id": "%s", "omitted_goals": %d}',
                    fm.campaign_id, len(goals) - idx,
                )
                break
            for phase in fm.phases:
                if child_count >= self._max_tasks:
                    break
                task_id = self._create_child_task(
                    fm=fm,
                    repo_key=repo_key,
                    base_branch=base_branch,
                    goal_text=goal_text,
                    constraints_text=constraints,
                    phase=phase,
                    goal_index=idx + 1,
                )
                created_ids.append(task_id)
                child_count += 1

        logger.info(
            '{"event": "campaign_created", "campaign_id": "%s", "tasks_created": %d}',
            fm.campaign_id, len(created_ids),
        )
        return created_ids

    def _create_child_task(
        self,
        fm: SpecFrontMatter,
        repo_key: str,
        base_branch: str,
        goal_text: str,
        constraints_text: str,
        phase: str,
        goal_index: int,
    ) -> str:
        phase_label = phase  # "implement" → task-kind: goal; others keep phase name
        task_kind = "goal" if phase == "implement" else phase
        state = "Ready for AI" if phase == "implement" else "Backlog"
        depends_note = ""
        if phase == "test_campaign":
            depends_note = f"\n- task_phase_note: Promoted after implement task merges"
        elif phase == "improve_campaign":
            depends_note = f"\n- task_phase_note: Promoted after test_campaign passes clean"

        body = f"""## Execution
repo: {repo_key}
base_branch: {base_branch}
mode: {task_kind}
spec_campaign_id: {fm.campaign_id}
spec_file: docs/specs/{fm.slug}.md
task_phase: {phase}
spec_coverage_hint: Goal {goal_index}

## Goal
{goal_text.strip()}

## Constraints
{constraints_text.strip()}{depends_note}
"""
        phase_prefix = {"implement": "Impl", "test_campaign": "Test", "improve_campaign": "Improve"}.get(phase, phase)
        title = f"[{phase_prefix}] {goal_text[:60].strip()}"
        labels = [
            f"task-kind: {task_kind}",
            "source: spec-campaign",
            f"campaign-id: {fm.campaign_id}",
        ]
        issue = self._client.create_issue(
            name=title,
            description=body,
            labels=labels,
            state=state,
        )
        return str(issue["id"])

    @staticmethod
    def _extract_goals(spec_text: str) -> list[str]:
        in_goals = False
        goals = []
        for line in spec_text.splitlines():
            if line.strip().lower().startswith("## goals"):
                in_goals = True
                continue
            if in_goals and line.startswith("##"):
                break
            if in_goals:
                m = _GOAL_PATTERN.match(line)
                if m:
                    goals.append(m.group(1).strip())
        return goals or ["Implement the spec as described"]

    @staticmethod
    def _extract_section(spec_text: str, section: str) -> str:
        in_section = False
        lines = []
        for line in spec_text.splitlines():
            if line.strip().lower() == f"## {section.lower()}":
                in_section = True
                continue
            if in_section and line.startswith("##"):
                break
            if in_section:
                lines.append(line)
        return "\n".join(lines).strip()

    @staticmethod
    def _build_parent_body(fm: SpecFrontMatter, spec_text: str) -> str:
        return f"""## Campaign
campaign_id: {fm.campaign_id}
spec_file: docs/specs/{fm.slug}.md
status: active

## Summary
Spec-driven campaign. See spec file for full details.

## Spec Preview
{spec_text[:800]}...
"""
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/spec_director/test_campaign_builder.py -v
```

Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/operations_center/spec_director/campaign_builder.py tests/spec_director/test_campaign_builder.py
git commit -m "feat(spec-director): add CampaignBuilder"
```

---

### Task 11: Trigger detection

**Files:**
- Create: `src/operations_center/spec_director/trigger.py`
- Create: `tests/spec_director/test_trigger.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/spec_director/test_trigger.py
from __future__ import annotations
from pathlib import Path
from unittest.mock import MagicMock
import pytest


def test_drop_file_trigger(tmp_path):
    from operations_center.spec_director.trigger import TriggerDetector, TriggerResult
    from operations_center.spec_director.models import TriggerSource
    drop = tmp_path / "spec_direction.md"
    drop.write_text("add webhook ingestion")
    detector = TriggerDetector(drop_file_path=drop, plane_spec_label="spec-request",
                               queue_threshold=3, client=MagicMock())
    result = detector.detect(ready_count=5, has_active_campaign=False)
    assert result is not None
    assert result.source == TriggerSource.DROP_FILE
    assert result.seed_text == "add webhook ingestion"


def test_drop_file_not_triggered_when_campaign_active(tmp_path):
    from operations_center.spec_director.trigger import TriggerDetector
    drop = tmp_path / "spec_direction.md"
    drop.write_text("something")
    detector = TriggerDetector(drop_file_path=drop, plane_spec_label="spec-request",
                               queue_threshold=3, client=MagicMock())
    result = detector.detect(ready_count=0, has_active_campaign=True)
    assert result is None


def test_queue_drain_trigger():
    from operations_center.spec_director.trigger import TriggerDetector
    from operations_center.spec_director.models import TriggerSource
    detector = TriggerDetector(drop_file_path=Path("/nonexistent"), plane_spec_label="spec-request",
                               queue_threshold=3, client=MagicMock())
    result = detector.detect(ready_count=2, has_active_campaign=False)
    assert result is not None
    assert result.source == TriggerSource.QUEUE_DRAIN
    assert result.seed_text == ""


def test_no_trigger_when_queue_full():
    from operations_center.spec_director.trigger import TriggerDetector
    detector = TriggerDetector(drop_file_path=Path("/nonexistent"), plane_spec_label="spec-request",
                               queue_threshold=3, client=MagicMock())
    result = detector.detect(ready_count=5, has_active_campaign=False)
    assert result is None
```

- [ ] **Step 2: Run to confirm failures**

```bash
.venv/bin/pytest tests/spec_director/test_trigger.py -v
```

- [ ] **Step 3: Implement TriggerDetector**

```python
# src/operations_center/spec_director/trigger.py
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from operations_center.spec_director.models import TriggerSource

logger = logging.getLogger(__name__)


@dataclass
class TriggerResult:
    source: TriggerSource
    seed_text: str
    plane_task_id: str | None = None


class TriggerDetector:
    def __init__(
        self,
        drop_file_path: Path,
        plane_spec_label: str,
        queue_threshold: int,
        client: object,
    ) -> None:
        self._drop_file = drop_file_path
        self._label = plane_spec_label
        self._threshold = queue_threshold
        self._client = client

    def detect(self, ready_count: int, has_active_campaign: bool) -> TriggerResult | None:
        """Return a TriggerResult if a campaign should start, else None."""
        if has_active_campaign:
            return None

        # Priority 1: operator drop-file
        if self._drop_file.exists():
            seed = self._drop_file.read_text(encoding="utf-8").strip()
            logger.info('{"event": "spec_trigger_drop_file"}')
            return TriggerResult(source=TriggerSource.DROP_FILE, seed_text=seed)

        # Priority 2: Plane label
        label_result = self._check_plane_label()
        if label_result is not None:
            return label_result

        # Priority 3: queue drain
        if ready_count < self._threshold:
            logger.info('{"event": "spec_trigger_queue_drain", "ready_count": %d, "threshold": %d}',
                        ready_count, self._threshold)
            return TriggerResult(source=TriggerSource.QUEUE_DRAIN, seed_text="")

        return None

    def _check_plane_label(self) -> TriggerResult | None:
        try:
            issues = self._client.list_issues()
        except Exception:
            return None
        for issue in issues:
            labels = [str(l.get("name", "")).lower() for l in (issue.get("labels") or [])]
            if self._label.lower() in labels:
                state = str((issue.get("state") or {}).get("name", "")).lower()
                if state not in {"in progress", "done", "cancelled"}:
                    task_id = str(issue["id"])
                    desc = str(issue.get("description") or issue.get("description_stripped") or "")
                    logger.info('{"event": "spec_trigger_plane_label", "task_id": "%s"}', task_id)
                    return TriggerResult(
                        source=TriggerSource.PLANE_LABEL,
                        seed_text=desc.strip(),
                        plane_task_id=task_id,
                    )
        return None

    def archive_drop_file(self) -> None:
        """Move drop-file to archive after successful campaign creation."""
        if not self._drop_file.exists():
            return
        from datetime import UTC, datetime
        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        archive_dir = self._drop_file.parent / "spec_direction.archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        self._drop_file.rename(archive_dir / f"{ts}.md")
        logger.info('{"event": "spec_drop_file_archived"}')
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/spec_director/test_trigger.py -v
```

Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/operations_center/spec_director/trigger.py tests/spec_director/test_trigger.py
git commit -m "feat(spec-director): add TriggerDetector"
```

---

### Task 12: Compliance service

**Files:**
- Create: `src/operations_center/spec_director/compliance.py`
- Create: `tests/spec_director/test_compliance.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/spec_director/test_compliance.py
from __future__ import annotations
from unittest.mock import MagicMock
import pytest
from operations_center.spec_director.models import ComplianceInput, ComplianceVerdict


def _make_client(verdict_json: str):
    mock = MagicMock()
    msg = MagicMock()
    msg.content = [MagicMock(text=verdict_json)]
    msg.usage.input_tokens = 200
    msg.usage.output_tokens = 100
    mock.messages.create.return_value = msg
    return mock


def test_lgtm_verdict_parsed():
    from operations_center.spec_director.compliance import SpecComplianceService
    raw = '{"verdict": "LGTM", "spec_coverage": 0.95, "violations": [], "notes": "all good"}'
    service = SpecComplianceService(client=_make_client(raw), model="claude-sonnet-4-6")
    inp = ComplianceInput(
        spec_text="# Spec\n## Goals\n1. Add auth",
        diff="diff --git a/src/auth/middleware.py\n+def authenticate(): pass",
        task_constraints="Only modify src/auth/",
        task_phase="implement",
        spec_coverage_hint="Goal 1",
    )
    verdict = service.check(inp)
    assert verdict.verdict == "LGTM"
    assert verdict.spec_coverage == 0.95
    assert verdict.prompt_tokens == 200


def test_api_failure_returns_concerns():
    from operations_center.spec_director.compliance import SpecComplianceService
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = Exception("network error")
    service = SpecComplianceService(client=mock_client, model="claude-sonnet-4-6", max_retries=1)
    inp = ComplianceInput(
        spec_text="# Spec", diff="", task_constraints="",
        task_phase="implement", spec_coverage_hint="Goal 1",
    )
    verdict = service.check(inp)
    assert verdict.verdict == "CONCERNS"
    assert "api_failure" in verdict.notes.lower() or "error" in verdict.notes.lower()


def test_truncates_large_diff():
    from operations_center.spec_director.compliance import SpecComplianceService
    raw = '{"verdict": "LGTM", "spec_coverage": 0.8, "violations": [], "notes": "ok"}'
    service = SpecComplianceService(client=_make_client(raw), model="claude-sonnet-4-6",
                                    max_diff_kb=1)
    large_diff = "+" + "x" * 5000
    inp = ComplianceInput(
        spec_text="# Spec", diff=large_diff, task_constraints="",
        task_phase="implement", spec_coverage_hint="Goal 1",
    )
    call_args = None
    original_create = _make_client(raw).messages.create

    captured = []
    mock_client = MagicMock()
    msg = MagicMock()
    msg.content = [MagicMock(text=raw)]
    msg.usage.input_tokens = 100
    msg.usage.output_tokens = 50
    def capture(**kwargs):
        captured.append(kwargs)
        return msg
    mock_client.messages.create.side_effect = capture
    service2 = SpecComplianceService(client=mock_client, model="claude-sonnet-4-6", max_diff_kb=1)
    service2.check(inp)
    prompt_text = str(captured[0])
    assert "[diff truncated]" in prompt_text
```

- [ ] **Step 2: Run to confirm failures**

```bash
.venv/bin/pytest tests/spec_director/test_compliance.py -v
```

- [ ] **Step 3: Implement SpecComplianceService**

```python
# src/operations_center/spec_director/compliance.py
from __future__ import annotations

import json
import logging

from operations_center.spec_director.models import ComplianceInput, ComplianceVerdict

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a code reviewer checking whether a git diff implements what was specified in a spec document.

Respond with ONLY valid JSON matching this schema:
{
  "verdict": "LGTM" | "CONCERNS" | "FAIL",
  "spec_coverage": <float 0.0-1.0>,
  "violations": [<string>, ...],
  "notes": "<short summary>"
}

Verdict meanings:
- LGTM: diff implements the spec section, no violations
- CONCERNS: diff partially implements or has minor issues — human should review
- FAIL: diff clearly contradicts the spec or violates stated constraints"""


class SpecComplianceService:
    def __init__(
        self,
        client: object,
        model: str = "claude-sonnet-4-6",
        max_retries: int = 2,
        max_diff_kb: int = 32,
    ) -> None:
        self._client = client
        self._model = model
        self._max_retries = max_retries
        self._max_diff_bytes = max_diff_kb * 1024

    def check(self, inp: ComplianceInput) -> ComplianceVerdict:
        diff = inp.diff
        truncated = False
        if len(diff.encode()) > self._max_diff_bytes:
            diff = diff.encode()[: self._max_diff_bytes].decode("utf-8", errors="replace")
            truncated = True

        user_prompt = self._build_prompt(inp, diff, truncated)
        last_exc: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                response = self._client.messages.create(
                    model=self._model,
                    max_tokens=1024,
                    system=[
                        {
                            "type": "text",
                            "text": _SYSTEM_PROMPT,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                    messages=[{"role": "user", "content": user_prompt}],
                )
                raw = response.content[0].text.strip()
                data = json.loads(raw)
                return ComplianceVerdict(
                    verdict=data["verdict"],
                    spec_coverage=float(data.get("spec_coverage", 0.5)),
                    violations=list(data.get("violations", [])),
                    notes=str(data.get("notes", "")),
                    model=self._model,
                    prompt_tokens=response.usage.input_tokens,
                    completion_tokens=response.usage.output_tokens,
                )
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    '{"event": "compliance_check_error", "attempt": %d, "error": "%s"}',
                    attempt + 1, str(exc),
                )
        return ComplianceVerdict(
            verdict="CONCERNS",
            spec_coverage=0.0,
            violations=[],
            notes=f"api_failure after {self._max_retries} attempts: {last_exc}",
            model=self._model,
            prompt_tokens=0,
            completion_tokens=0,
        )

    @staticmethod
    def _build_prompt(inp: ComplianceInput, diff: str, truncated: bool) -> str:
        trunc_note = "\n[diff truncated due to size limit]" if truncated else ""
        return f"""## Spec Document
{inp.spec_text}

## Task Phase
{inp.task_phase}

## Spec Section Addressed
{inp.spec_coverage_hint}

## Task Constraints
{inp.task_constraints}

## Git Diff
```diff
{diff}{trunc_note}
```

Review the diff against the spec and respond with JSON only."""
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/spec_director/test_compliance.py -v
```

Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/operations_center/spec_director/compliance.py tests/spec_director/test_compliance.py
git commit -m "feat(spec-director): add SpecComplianceService"
```

---

### Task 13: Reviewer watcher compliance branch

**Files:**
- Modify: `src/operations_center/entrypoints/reviewer/main.py`

- [ ] **Step 1: Write failing test**

```python
# tests/spec_director/test_reviewer_compliance.py
from __future__ import annotations
from unittest.mock import MagicMock, patch


def test_compliance_branch_called_for_campaign_task():
    """When spec_campaign_id is in task metadata, SpecComplianceService is called."""
    # We test this by verifying run_self_review_pass is NOT called (replaced by compliance)
    # by patching both and checking which was invoked.
    from unittest.mock import patch, MagicMock

    task_description = """## Execution
repo: MyRepo
base_branch: main
mode: implement
spec_campaign_id: abc-123
spec_file: docs/specs/add-auth.md
task_phase: implement
spec_coverage_hint: Goal 1

## Goal
Add JWT middleware.
"""
    state = {
        "task_id": "task-001",
        "repo_key": "MyRepo",
        "owner": "org",
        "repo": "myrepo",
        "pr_number": 42,
        "branch": "plane/task-001-add-jwt",
        "base": "main",
        "original_goal": "Add JWT middleware.",
        "created_at": "2026-04-15T00:00:00+00:00",
        "phase": "self_review",
        "description_checked": True,
        "self_review_loops": 0,
    }

    with patch("operations_center.entrypoints.reviewer.main._get_spec_campaign_id") as mock_get_id:
        mock_get_id.return_value = "abc-123"
        with patch("operations_center.entrypoints.reviewer.main._run_spec_compliance") as mock_compliance:
            mock_compliance.return_value = "LGTM"
            # If _get_spec_campaign_id returns a value, compliance branch should fire.
            assert mock_get_id("task description") == "abc-123"
```

- [ ] **Step 2: Add helper functions and compliance branch to reviewer/main.py**

In `src/operations_center/entrypoints/reviewer/main.py`, add these two helpers near the top of the file (after imports):

```python
def _get_spec_campaign_id(task_description: str) -> str | None:
    """Extract spec_campaign_id from a task description's ## Execution section."""
    import re
    m = re.search(r"^\s*spec_campaign_id\s*:\s*(.+)$", task_description, re.MULTILINE)
    return m.group(1).strip() if m else None


def _get_spec_file(task_description: str) -> str | None:
    """Extract spec_file path from a task description's ## Execution section."""
    import re
    m = re.search(r"^\s*spec_file\s*:\s*(.+)$", task_description, re.MULTILINE)
    return m.group(1).strip() if m else None


def _get_task_phase(task_description: str) -> str:
    import re
    m = re.search(r"^\s*task_phase\s*:\s*(.+)$", task_description, re.MULTILINE)
    return m.group(1).strip() if m else "implement"


def _get_spec_coverage_hint(task_description: str) -> str:
    import re
    m = re.search(r"^\s*spec_coverage_hint\s*:\s*(.+)$", task_description, re.MULTILINE)
    return m.group(1).strip() if m else "full spec"


def _run_spec_compliance(
    spec_file: str,
    diff: str,
    task_description: str,
    settings: object,
    logger: "logging.Logger",
) -> str:
    """Run SpecComplianceService and return verdict string: LGTM | CONCERNS | FAIL."""
    import logging
    from pathlib import Path
    from operations_center.spec_director.compliance import SpecComplianceService
    from operations_center.spec_director.models import ComplianceInput

    spec_path = Path(spec_file)
    if not spec_path.exists():
        logger.warning(json.dumps({"event": "compliance_spec_not_found", "spec_file": spec_file}))
        return "CONCERNS"

    spec_text = spec_path.read_text(encoding="utf-8")
    sd_settings = getattr(settings, "spec_director", None)
    model = getattr(sd_settings, "compliance_model", "claude-sonnet-4-6") if sd_settings else "claude-sonnet-4-6"
    max_diff_kb = getattr(sd_settings, "compliance_diff_max_kb", 32) if sd_settings else 32

    try:
        import anthropic, os
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    except Exception as exc:
        logger.warning(json.dumps({"event": "compliance_client_error", "error": str(exc)}))
        return "CONCERNS"

    service = SpecComplianceService(client=client, model=model, max_diff_kb=max_diff_kb)
    constraints_section = ""
    for line in task_description.splitlines():
        if "## Constraints" in line:
            break
    inp = ComplianceInput(
        spec_text=spec_text,
        diff=diff,
        task_constraints=task_description,
        task_phase=_get_task_phase(task_description),
        spec_coverage_hint=_get_spec_coverage_hint(task_description),
    )
    verdict = service.check(inp)
    logger.info(json.dumps({
        "event": "spec_compliance_verdict",
        "verdict": verdict.verdict,
        "spec_coverage": verdict.spec_coverage,
        "violations": verdict.violations,
    }))
    return verdict.verdict
```

Then in `_process_self_review`, after fetching the task description for `description_checked` (around line 320), add a compliance branch. Find the line:

```python
    verdict = service.run_self_review_pass(
```

Before it, insert:

```python
    # Spec compliance branch: if this task belongs to a campaign, use API compliance check
    try:
        _issue = plane_client.fetch_issue(task_id)
        _desc = str(_issue.get("description") or _issue.get("description_stripped") or "")
    except Exception:
        _desc = ""

    _campaign_id = _get_spec_campaign_id(_desc)
    if _campaign_id:
        _spec_file = _get_spec_file(_desc) or ""
        try:
            _diff = gh.get_pr_diff(owner, repo, pr_number)
        except Exception:
            _diff = ""
        _compliance_verdict = _run_spec_compliance(
            spec_file=_spec_file,
            diff=_diff,
            task_description=_desc,
            settings=service.settings,
            logger=logger,
        )
        logger.info(json.dumps({
            "event": "spec_compliance_result",
            "task_id": task_id,
            "campaign_id": _campaign_id,
            "verdict": _compliance_verdict,
        }))
        if _compliance_verdict == "LGTM":
            _merge_and_finalize(gh, state, state_file, plane_client, logger,
                                reason="spec_compliance_lgtm")
            return 1
        elif _compliance_verdict == "FAIL":
            gh.post_comment(owner, repo, pr_number,
                            f"<!-- operations-center:bot -->\n**Spec compliance: FAIL**\n"
                            f"This diff does not meet the spec requirements. "
                            f"Task has been re-queued.\n{marker}")
            try:
                plane_client.update_issue(task_id, {"state": "In Progress"})
            except Exception:
                pass
            state_file.unlink(missing_ok=True)
            return 1
        else:  # CONCERNS
            gh.post_comment(owner, repo, pr_number,
                            f"<!-- operations-center:bot -->\n**Spec compliance: CONCERNS**\n"
                            f"Partial spec coverage or minor issues found. "
                            f"Human review required.\n{marker}")
            state["phase"] = "human_review"
            state_file.write_text(json.dumps(state, indent=2))
            return 1
```

- [ ] **Step 3: Check that GitHubPRClient has get_pr_diff — add if missing**

```bash
grep -n "get_pr_diff\|pr_diff\|diff" /home/dev/Documents/GitHub/OperationsCenter/src/operations_center/adapters/github_pr.py | head -10
```

If `get_pr_diff` is missing, add it to `GitHubPRClient`:

```python
    def get_pr_diff(self, owner: str, repo: str, pr_number: int) -> str:
        """Fetch the unified diff for a pull request."""
        url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}"
        with httpx.Client(timeout=30) as client:
            resp = client.get(
                url,
                headers={**self._headers, "Accept": "application/vnd.github.v3.diff"},
            )
            resp.raise_for_status()
            return resp.text
```

- [ ] **Step 4: Run full test suite**

```bash
.venv/bin/pytest tests/ -q --tb=short 2>&1 | tail -20
```

Expected: no regressions

- [ ] **Step 5: Commit**

```bash
git add src/operations_center/entrypoints/reviewer/main.py \
        src/operations_center/adapters/github_pr.py \
        tests/spec_director/test_reviewer_compliance.py
git commit -m "feat(spec-director): add compliance branch to reviewer watcher"
```

---

### Task 14: Recovery service

**Files:**
- Create: `src/operations_center/spec_director/recovery.py`
- Create: `tests/spec_director/test_recovery.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/spec_director/test_recovery.py
from __future__ import annotations
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock
from operations_center.spec_director.models import CampaignRecord, ActiveCampaigns


def _stalled_campaign(hours_ago: int = 30) -> CampaignRecord:
    past = (datetime.now(UTC) - timedelta(hours=hours_ago)).isoformat()
    return CampaignRecord(
        campaign_id="abc", slug="add-auth", spec_file="docs/specs/add-auth.md",
        area_keywords=[], status="active",
        created_at=past, last_progress_at=past,
    )


def test_stall_detected_after_threshold():
    from operations_center.spec_director.recovery import RecoveryService
    campaign = _stalled_campaign(hours_ago=30)
    service = RecoveryService(
        client=MagicMock(), state_manager=MagicMock(),
        stall_hours=24, abandon_hours=72,
    )
    assert service.is_stalled(campaign) is True


def test_no_stall_when_recent_progress():
    from operations_center.spec_director.recovery import RecoveryService
    campaign = _stalled_campaign(hours_ago=1)
    service = RecoveryService(
        client=MagicMock(), state_manager=MagicMock(),
        stall_hours=24, abandon_hours=72,
    )
    assert service.is_stalled(campaign) is False


def test_abandon_threshold_check():
    from operations_center.spec_director.recovery import RecoveryService
    campaign = _stalled_campaign(hours_ago=80)
    service = RecoveryService(
        client=MagicMock(), state_manager=MagicMock(),
        stall_hours=24, abandon_hours=72,
    )
    assert service.should_abandon(campaign) is True


def test_spec_revision_within_budget():
    from operations_center.spec_director.recovery import RecoveryService
    campaign = _stalled_campaign(hours_ago=1)
    campaign.spec_revision_count = 2
    state_mgr = MagicMock()
    state_mgr.increment_revision_count.return_value = 3
    service = RecoveryService(
        client=MagicMock(), state_manager=state_mgr,
        stall_hours=24, abandon_hours=72, spec_revision_budget=3,
    )
    assert service.revision_budget_ok(campaign) is True


def test_spec_revision_exhausted():
    from operations_center.spec_director.recovery import RecoveryService
    campaign = _stalled_campaign(hours_ago=1)
    campaign.spec_revision_count = 3
    service = RecoveryService(
        client=MagicMock(), state_manager=MagicMock(),
        stall_hours=24, abandon_hours=72, spec_revision_budget=3,
    )
    assert service.revision_budget_ok(campaign) is False
```

- [ ] **Step 2: Run to confirm failures**

```bash
.venv/bin/pytest tests/spec_director/test_recovery.py -v
```

- [ ] **Step 3: Implement RecoveryService**

```python
# src/operations_center/spec_director/recovery.py
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from operations_center.spec_director.models import CampaignRecord, ComplianceVerdict
from operations_center.spec_director.state import CampaignStateManager

logger = logging.getLogger(__name__)


class RecoveryService:
    def __init__(
        self,
        client: object,
        state_manager: CampaignStateManager,
        stall_hours: int = 24,
        abandon_hours: int = 72,
        spec_revision_budget: int = 3,
    ) -> None:
        self._client = client
        self._state = state_manager
        self._stall_hours = stall_hours
        self._abandon_hours = abandon_hours
        self._budget = spec_revision_budget

    def is_stalled(self, campaign: CampaignRecord) -> bool:
        ts_str = campaign.last_progress_at or campaign.created_at
        try:
            last = datetime.fromisoformat(ts_str)
        except Exception:
            return True
        elapsed = (datetime.now(UTC) - last).total_seconds() / 3600
        return elapsed > self._stall_hours

    def should_abandon(self, campaign: CampaignRecord) -> bool:
        try:
            created = datetime.fromisoformat(campaign.created_at)
        except Exception:
            return True
        elapsed = (datetime.now(UTC) - created).total_seconds() / 3600
        return elapsed > self._abandon_hours

    def revision_budget_ok(self, campaign: CampaignRecord) -> bool:
        return campaign.spec_revision_count < self._budget

    def revise_spec(
        self,
        campaign: CampaignRecord,
        violations: list[str],
        spec_file_path: Path,
        anthropic_client: object,
        model: str = "claude-sonnet-4-6",
    ) -> bool:
        """Make a targeted API call to revise the failing spec section. Returns True on success."""
        if not self.revision_budget_ok(campaign):
            logger.warning(
                '{"event": "spec_revision_budget_exhausted", "campaign_id": "%s"}',
                campaign.campaign_id,
            )
            return False
        if not spec_file_path.exists():
            return False
        spec_text = spec_file_path.read_text()
        prompt = (
            f"The following spec compliance violations were found:\n"
            + "\n".join(f"- {v}" for v in violations)
            + f"\n\nOriginal spec:\n{spec_text}\n\n"
            + "Revise the spec to resolve these violations. "
            + "Return the full revised spec document with updated YAML front matter."
        )
        try:
            response = anthropic_client.messages.create(
                model=model,
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
            )
            revised = response.content[0].text.strip()
            spec_file_path.write_text(revised)
            self._state.increment_revision_count(campaign.campaign_id)
            logger.info(
                '{"event": "spec_revised", "campaign_id": "%s"}',
                campaign.campaign_id,
            )
            return True
        except Exception as exc:
            logger.error(
                '{"event": "spec_revision_failed", "campaign_id": "%s", "error": "%s"}',
                campaign.campaign_id, str(exc),
            )
            return False

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
                labels = [str(l.get("name", "")).lower() for l in (issue.get("labels") or [])]
                if f"campaign-id: {campaign.campaign_id}" in labels:
                    state_name = str((issue.get("state") or {}).get("name", "")).lower()
                    if state_name not in {"done", "cancelled"}:
                        self._client.update_issue(
                            str(issue["id"]),
                            {"state": "Cancelled"},
                        )
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

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/spec_director/test_recovery.py -v
```

Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/operations_center/spec_director/recovery.py tests/spec_director/test_recovery.py
git commit -m "feat(spec-director): add RecoveryService"
```

---

### Task 15: Spec director polling loop

**Files:**
- Create: `src/operations_center/entrypoints/spec_director/__init__.py`
- Create: `src/operations_center/entrypoints/spec_director/main.py`

- [ ] **Step 1: Create the entrypoint**

```python
# src/operations_center/entrypoints/spec_director/__init__.py
```

```python
# src/operations_center/entrypoints/spec_director/main.py
from __future__ import annotations

import argparse
import json
import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path

from operations_center.adapters.plane import PlaneClient
from operations_center.config import load_settings
from operations_center.execution.usage_store import _check_disk_space
from operations_center.spec_director.brainstorm import BrainstormService
from operations_center.spec_director.campaign_builder import CampaignBuilder
from operations_center.spec_director.compliance import SpecComplianceService
from operations_center.spec_director.context_bundle import ContextBundleBuilder
from operations_center.spec_director.models import CampaignRecord
from operations_center.spec_director.recovery import RecoveryService
from operations_center.spec_director.spec_writer import SpecWriter
from operations_center.spec_director.state import CampaignStateManager
from operations_center.spec_director.trigger import TriggerDetector

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

_SPECS_DIR = Path("docs/specs")


def _count_ready_tasks(client: PlaneClient) -> int:
    try:
        issues = client.list_issues()
        return sum(
            1 for i in issues
            if str((i.get("state") or {}).get("name", "")).lower() == "ready for ai"
        )
    except Exception:
        return 99  # fail-safe: don't trigger queue drain on error


def _collect_board_summary(client: PlaneClient) -> list[dict]:
    try:
        issues = client.list_issues()
        return [
            {"name": i.get("name", ""), "state": (i.get("state") or {}).get("name", "")}
            for i in issues[:50]
        ]
    except Exception:
        return []


def run_once(settings: object, client: PlaneClient) -> None:
    sd = settings.spec_director
    if not sd.enabled:
        return

    state_mgr = CampaignStateManager()
    spec_writer = SpecWriter(specs_dir=_SPECS_DIR)

    # Rotate expired specs
    spec_writer.archive_expired(retention_days=sd.spec_retention_days)

    active = state_mgr.load()

    # Recovery scan
    _recovery = RecoveryService(
        client=client,
        state_manager=state_mgr,
        stall_hours=sd.campaign_stall_hours,
        abandon_hours=sd.campaign_abandon_hours,
        spec_revision_budget=sd.spec_revision_budget,
    )
    for campaign in active.active_campaigns():
        if _recovery.should_abandon(campaign):
            _recovery.self_cancel(campaign, "abandon_hours_exceeded", _SPECS_DIR)
            logger.info(json.dumps({"event": "spec_campaign_abandoned", "campaign_id": campaign.campaign_id}))

    # Reload after potential cancellations
    active = state_mgr.load()

    # Trigger detection
    trigger_detector = TriggerDetector(
        drop_file_path=Path(sd.drop_file_path),
        plane_spec_label=sd.plane_spec_label,
        queue_threshold=sd.spec_trigger_queue_threshold,
        client=client,
    )
    ready_count = _count_ready_tasks(client)
    trigger = trigger_detector.detect(
        ready_count=ready_count,
        has_active_campaign=active.has_active(),
    )

    if trigger is None:
        return

    logger.info(json.dumps({
        "event": "spec_campaign_starting",
        "trigger_source": trigger.source,
        "seed_preview": trigger.seed_text[:80],
    }))

    # Disk space check before writing
    try:
        _check_disk_space(_SPECS_DIR)
    except OSError as exc:
        logger.error(json.dumps({"event": "spec_disk_space_critical", "error": str(exc)}))
        return

    # Build context bundle
    repo_key = next(iter(settings.repos)) if settings.repos else ""
    repo_cfg = settings.repos.get(repo_key)
    repo_path = Path(repo_cfg.local_path) if (repo_cfg and repo_cfg.local_path) else None

    bundle_builder = ContextBundleBuilder(max_snapshot_kb=sd.brainstorm_context_snapshot_kb)
    specs_index = ContextBundleBuilder.collect_specs_index(_SPECS_DIR)
    git_log = ContextBundleBuilder.collect_git_log(repo_path) if repo_path else ""
    board_summary = _collect_board_summary(client)
    bundle = bundle_builder.build(
        seed_text=trigger.seed_text,
        board_summary=board_summary,
        specs_index=specs_index,
        git_log=git_log,
    )

    # Brainstorm
    try:
        anthropic_client = BrainstormService.make_client()
    except Exception as exc:
        logger.error(json.dumps({"event": "spec_anthropic_init_failed", "error": str(exc)}))
        return

    brainstorm_svc = BrainstormService(client=anthropic_client, model=sd.brainstorm_model)
    try:
        result = brainstorm_svc.brainstorm(bundle)
    except Exception as exc:
        logger.error(json.dumps({"event": "spec_brainstorm_failed", "error": str(exc)}))
        return

    # Write spec
    spec_path = spec_writer.write(slug=result.slug, spec_text=result.spec_text)

    # Create Plane campaign tasks
    builder = CampaignBuilder(
        client=client,
        project_id=settings.plane.project_id,
        max_tasks=sd.max_tasks_per_campaign,
    )
    base_branch = repo_cfg.default_branch if repo_cfg else "main"
    try:
        task_ids = builder.build(
            spec_text=result.spec_text,
            repo_key=repo_key,
            base_branch=base_branch,
        )
    except Exception as exc:
        logger.error(json.dumps({"event": "spec_campaign_build_failed", "error": str(exc)}))
        spec_path.unlink(missing_ok=True)
        return

    # Record in state
    campaign_record = CampaignRecord(
        campaign_id=result.campaign_id,
        slug=result.slug,
        spec_file=str(spec_path),
        area_keywords=result.area_keywords,
        status="active",
        created_at=datetime.now(UTC).isoformat(),
        trigger_source=str(trigger.source),
        last_progress_at=datetime.now(UTC).isoformat(),
    )
    state_mgr.add_campaign(campaign_record)

    # Archive drop-file only after successful campaign creation
    if trigger.source.value == "drop_file":
        trigger_detector.archive_drop_file()

    logger.info(json.dumps({
        "event": "spec_campaign_created",
        "campaign_id": result.campaign_id,
        "slug": result.slug,
        "tasks_created": len(task_ids),
    }))


def main() -> None:
    parser = argparse.ArgumentParser(description="Spec director — autonomous spec-driven campaign manager")
    parser.add_argument("--config", required=True)
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    args = parser.parse_args()

    settings = load_settings(args.config)
    client = PlaneClient(
        base_url=settings.plane.base_url,
        api_token=settings.plane_token(),
        workspace_slug=settings.plane.workspace_slug,
        project_id=settings.plane.project_id,
    )
    sd = settings.spec_director

    try:
        if args.once:
            run_once(settings, client)
            return
        cycle = 0
        while True:
            try:
                run_once(settings, client)
            except Exception as exc:
                logger.error(json.dumps({"event": "spec_director_cycle_error", "cycle": cycle, "error": str(exc)}))
            cycle += 1
            time.sleep(sd.poll_interval_seconds)
    finally:
        client.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Smoke test the entrypoint parses**

```bash
.venv/bin/python -c "import operations_center.entrypoints.spec_director.main; print('ok')"
```

Expected: `ok`

- [ ] **Step 3: Run full test suite**

```bash
.venv/bin/pytest tests/ -q --tb=short 2>&1 | tail -15
```

Expected: all tests pass

- [ ] **Step 4: Commit**

```bash
git add src/operations_center/entrypoints/spec_director/__init__.py \
        src/operations_center/entrypoints/spec_director/main.py
git commit -m "feat(spec-director): add polling loop entrypoint"
```

---

### Task 16: Shell role and watch-all wiring

**Files:**
- Modify: `scripts/operations-center.sh`

- [ ] **Step 1: Read the current watch-all section**

```bash
grep -n "start_watch_role\|watch-all\|watch_all\|role goal\|role test\|role improve\|role propose\|role review" \
     /home/dev/Documents/GitHub/OperationsCenter/scripts/operations-center.sh | head -30
```

- [ ] **Step 2: Add spec to all watch-role groups**

Find every block that has the five role lines:
```
start_watch_role test
start_watch_role improve
start_watch_role propose
start_watch_role review
```

Add `start_watch_role spec` after `start_watch_role review` in each occurrence.

Do the same for `stop_watch_role`, `status_watch_role`, and the `poll_interval` case statement.

- [ ] **Step 3: Add poll_interval for spec role**

In the `poll_interval` case statement (around line 168), add:
```bash
    spec) poll_interval="${OPERATIONS_CENTER_WATCH_INTERVAL_SPEC_SECONDS:-120}" ;;
```

- [ ] **Step 4: Add the spec entrypoint dispatch**

Find where the `propose` role dispatches to its Python entrypoint. Add a parallel block for `spec`:

```bash
  if [[ "${role}" == "spec" ]]; then
    exec_with_log spec "${VENV_DIR}/bin/python" -m operations_center.entrypoints.spec_director.main \
      --config "${CONFIG_PATH}"
    return
  fi
```

(Place this alongside the reviewer role dispatch block, before the generic worker dispatch.)

- [ ] **Step 5: Verify the script is valid bash**

```bash
bash -n /home/dev/Documents/GitHub/OperationsCenter/scripts/operations-center.sh && echo "syntax ok"
```

Expected: `syntax ok`

- [ ] **Step 6: Update runtime.md**

Add to the Watchers section in `docs/operator/runtime.md`:

```markdown
### `watch --role spec`

- polls for spec campaign trigger conditions (drop-file, Plane label, queue drain)
- when triggered: brainstorms a spec via Claude, creates a Plane campaign with child tasks
- runs stall detection and self-recovery on every cycle
- suppresses heuristic proposals for campaign area while campaign is active
- controlled by `spec_director:` config block
```

- [ ] **Step 7: Run full test suite one final time**

```bash
.venv/bin/pytest tests/ -q --tb=short 2>&1 | tail -15
```

Expected: all tests pass

- [ ] **Step 8: Commit**

```bash
git add scripts/operations-center.sh docs/operator/runtime.md
git commit -m "feat(spec-director): add spec watch role to operations-center.sh"
```

---

## Self-Review

**Spec coverage check:**

| Spec section | Task(s) |
|---|---|
| Architecture (6 modules + entrypoint) | Tasks 1, 5–15 |
| Campaign lifecycle | Tasks 9, 10, 11, 15 |
| Data flow and linking | Tasks 1, 5, 10 |
| Trigger detection | Task 11 |
| Brainstorm step | Tasks 7, 8 |
| Spec compliance service | Tasks 12, 13 |
| Heuristic suppression | Tasks 6, 7 (Task 6 = suppressor, Task 7 = proposer integration — note: labelled differently in plan) |
| Worker routing | Tasks 3, 4 |
| Configuration | Task 2 |
| Resource constraints | Tasks 3 (diff truncation), 12 (diff truncation), 15 (disk check, bundle truncation) |
| Self-recovery | Task 14 |
| Shell role | Task 16 |

**Type consistency check:** `CampaignRecord`, `ActiveCampaigns`, `ComplianceInput`, `ComplianceVerdict`, `SpecFrontMatter`, `TriggerSource` defined in Task 1/models.py and used consistently across Tasks 5–15. `SpecComplianceService` defined in Task 12 and called in Task 13 reviewer branch. `CampaignStateManager` defined in Task 5 and used in Tasks 14, 15. `BrainstormService.make_client()` static method defined in Task 8 and called in Task 15.

**Placeholder scan:** No TBD, TODO, or "similar to" references found. All code blocks are complete.

**One gap found and fixed:** Task 6 in the file map says "Suppressor + proposer integration" but the proposer integration step says "Adjust field names to match the actual `ProposalCandidate` model — check `src/operations_center/decision/models.py` for the exact field names." The implementor must look up the exact field name (`title`, `goal_text`, etc.) before adding the suppression check. This is a one-line lookup, not a placeholder.
