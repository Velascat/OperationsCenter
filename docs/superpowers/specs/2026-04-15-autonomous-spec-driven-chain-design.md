# Autonomous Spec-Driven Development Chain

**Date:** 2026-04-15
**Status:** approved
**Author:** operator + Claude

---

## Overview

Add a fully autonomous spec-driven development chain to ControlPlane. The existing heuristic autonomy cycle (observe → insights → decide → propose) continues to run for mechanical fixes (lint, types, coverage). A new `spec` watch role sits above it as a priority layer: when a spec campaign is active, it suppresses heuristic proposals that overlap with the campaign's area and drives directed feature work through a Claude-authored spec.

The chain follows the superpowers pattern: brainstorm → spec → task campaign → execute → compliance review → PR.

Human interaction: operator drops a file or creates a labelled Plane task. System picks it up and starts a campaign. Fully autonomous otherwise.

---

## Architecture

Six modules in a new `spec_director/` package:

```
src/control_plane/spec_director/
  trigger.py          — detects start conditions (drop-file, Plane label, queue drain)
  brainstorm.py       — builds context bundle, calls Anthropic API, returns spec text
  spec_writer.py      — writes spec to ControlPlane repo + copies into target workspace
  campaign_builder.py — creates Plane parent task + child tasks with campaign metadata
  compliance.py       — structured verdict (diff vs spec) via Anthropic API
  suppressor.py       — blocks heuristic proposals while a campaign covers their area

src/control_plane/entrypoints/spec_director/
  main.py             — polling loop, wires the above together

scripts/control-plane.sh  — new "spec" watch role (alongside goal/test/improve/propose/review)
```

**Existing code changes (minimal):**
- `worker/main.py`: two new `task_kind` routes (`test_campaign` → `kodo --test`, `improve_campaign` → `kodo --improve`)
- `adapters/kodo/adapter.py`: `build_command` gains `kodo_mode` parameter (`"goal"` | `"test"` | `"improve"`)
- `entrypoints/reviewer/main.py`: compliance branch — if task has `spec_campaign_id`, call `SpecComplianceService` instead of kodo self-review
- `proposer/candidate_integration.py`: suppression check before creating a heuristic task

---

## Campaign Lifecycle

```
TRIGGER DETECTED
  ├── drop-file: state/spec_direction.md
  ├── Plane task with label "spec-request"
  └── queue drain: ready_for_ai_count < threshold AND no active campaign

BRAINSTORM  (claude-opus-4-6, one call per campaign)
  Context bundle:
    - insight snapshot (22-deriver JSON from latest autonomy_cycle run)
    - git log summary (last 30 commits of target repo)
    - specs index (title + status of all docs/specs/*.md)
    - board summary (open task titles + states, last 48h)
    - seed text (from drop-file or Plane task description, or empty for autonomous)
  Output: spec text, suggested slug, phase flags [implement, test, improve]

SPEC WRITE
  - docs/specs/<slug>.md in ControlPlane repo (canonical)
  - copied into target workspace clone at same relative path
  - Plane parent campaign task created with campaign_id, spec_file reference, short summary

TASK CREATION  (campaign_builder)
  For each goal in spec:
    implement task  → state: Ready for AI  kind: goal             spec_campaign_id: <uuid>
    test task       → state: Backlog        kind: test_campaign    depends_on: implement
    improve task    → state: Backlog        kind: improve_campaign depends_on: test (if phases include improve)
  Confidence gate: improve task promoted to Ready for AI only after test_campaign passes clean.

EXECUTION  (existing workers + new routing)
  goal worker    → kodo --goal-file (unchanged)
  test worker    → kodo --test --goal-file (new route for test_campaign kind)
  improve worker → kodo --improve --goal-file (new route for improve_campaign kind)

COMPLIANCE CHECK  (per task, in reviewer watcher)
  Calls SpecComplianceService with: spec text, diff, task constraints, task phase
  Verdict: LGTM → PR proceeds; CONCERNS → human gates merge; FAIL → task back to In Progress
  Non-campaign tasks: existing kodo self-review unchanged.

CAMPAIGN COMPLETE
  All tasks Done/Cancelled → campaign marked complete in state/campaigns/active.json
  Suppressor releases heuristic proposals for the campaign's area
  drop-file (if present) archived to state/spec_direction.archive/<timestamp>.md
```

---

## Data Flow and Linking

Every object in a campaign carries the same `campaign_id` (UUID, generated at spec creation).

**Spec file front matter** (`docs/specs/<slug>.md`):
```yaml
---
campaign_id: <uuid>
slug: <slug>
created_at: <iso>
phases: [implement, test, improve]
repos: [MyRepo]
area_keywords: [src/auth/, authentication, login]
status: active   # active | complete | cancelled
---
```

**Plane parent task** description section:
```
## Campaign
campaign_id: <uuid>
spec_file: docs/specs/<slug>.md
status: active
```

**Each child task** description section:
```
## Execution
repo: MyRepo
base_branch: main
mode: goal
spec_campaign_id: <uuid>
spec_file: docs/specs/<slug>.md
task_phase: implement   # or test_campaign / improve_campaign
```

**Fast-lookup state file** `state/campaigns/active.json`:
```json
[
  {
    "campaign_id": "<uuid>",
    "slug": "<slug>",
    "spec_file": "docs/specs/<slug>.md",
    "area_keywords": ["src/auth/", "authentication"],
    "status": "active",
    "created_at": "<iso>"
  }
]
```
Written at campaign creation. Updated to `"status": "complete"` when all tasks are done. Suppressor reads this file — no disk scan of spec files needed at proposal time.

---

## Trigger Detection

Polling loop in `spec_director/trigger.py`. Three checks in priority order, skipped entirely if a campaign is already active:

1. **Drop-file**: `state/spec_direction.md` exists → read as seed text, start campaign. Archive to `state/spec_direction.archive/<timestamp>.md` only after the campaign is successfully created (brainstorm + spec write + Plane tasks all succeed).
2. **Plane label**: fetch open tasks with label `spec-request` → use first unprocessed task's description as seed, mark task `In Progress` to claim it, start campaign.
3. **Queue drain**: `ready_for_ai_count < spec_trigger_queue_threshold` (config, default `3`) AND no active campaign → start autonomous campaign with empty seed (Claude picks direction from context bundle).

One campaign at a time per repo. The spec director skips all trigger checks while `state/campaigns/active.json` has any entry with `status: active`.

---

## Brainstorm Step

`spec_director/brainstorm.py` assembles the context bundle and makes a single `claude-opus-4-6` call via the Anthropic SDK (`anthropic` package already available in the environment).

**Context bundle assembly:**
- Insight snapshot: read latest `tools/report/autonomy_cycle/*/insights.json`
- Git log: `git log --oneline -30` on the target repo (uses existing `run_git` helper)
- Specs index: scan `docs/specs/*.md` front matter for title + status fields
- Board summary: `PlaneClient.list_issues` filtered to last 48h, titles + states only
- Seed text: from trigger source, or empty string

**Output parsing:** Claude returns structured YAML front matter block followed by spec body. `brainstorm.py` parses the front matter for `slug`, `phases`, `area_keywords`; passes the full text to `spec_writer.py`.

**Prompt caching:** the static system prompt is passed as a `cache_control: ephemeral` block.

**Model:** `claude-opus-4-6` for brainstorm (one call per campaign start, quality matters). `claude-sonnet-4-6` for all compliance checks (per-task, cost matters).

---

## Spec Compliance Service

`spec_director/compliance.py` — direct Anthropic API call, structured Pydantic output.

```python
class ComplianceInput(BaseModel):
    spec_text: str
    diff: str
    task_constraints: str
    task_phase: str          # "implement" | "test_campaign" | "improve_campaign"
    spec_coverage_hint: str  # which spec section this task addressed (written into task body by campaign_builder at creation time, parsed from task body by reviewer watcher at check time)

class ComplianceVerdict(BaseModel):
    verdict: Literal["LGTM", "CONCERNS", "FAIL"]
    spec_coverage: float          # 0.0–1.0
    violations: list[str]
    notes: str
    model: str
    prompt_tokens: int
    completion_tokens: int
```

**Reviewer watcher integration:** existing `run_self_review_pass` is called for non-campaign tasks (unchanged). For tasks with `spec_campaign_id` in the parsed task body, the watcher calls `SpecComplianceService.check(input)` instead.

Downstream flow:
- `LGTM` → PR proceeds to merge (existing reviewer merge path)
- `CONCERNS` → compliance verdict posted as PR comment, task stays `In Review`, human must approve or close
- `FAIL` → PR closed, task transitions back to `In Progress` for the worker to retry; reviewer watcher posts a comment explaining the spec violations

**Retry:** 2 attempts on transient API errors. Both fail → CONCERNS verdict (human review, not silent failure).

**Prompt caching:** spec text passed as `cache_control: ephemeral` prefix block. Multiple tasks in the same campaign share the cached spec.

---

## Heuristic Suppression

`spec_director/suppressor.py` — called by `proposer/candidate_integration.py` before creating a heuristic task.

```python
def is_suppressed(proposal_title: str, proposal_paths: list[str]) -> bool:
    active = load_active_campaigns()  # reads state/campaigns/active.json
    for campaign in active:
        if any_keyword_matches(campaign.area_keywords, proposal_title, proposal_paths):
            return True
    return False
```

Suppressed proposals are logged to the decision artifact with `reason: active_spec_campaign` — not silently dropped. Operator can see what was held back during a campaign.

Suppression lifts automatically when `state/campaigns/active.json` has no `active` entries.

---

## Worker Routing Changes

**`KodoAdapter.build_command`** gains a `kodo_mode: str = "goal"` parameter:
- `"goal"` → `kodo --goal-file <path> ...` (current behaviour, unchanged)
- `"test"` → `kodo --test --goal-file <path> ...`
- `"improve"` → `kodo --improve --goal-file <path> ...`

**`worker/main.py`** dispatch:
- `test` lane: if `task.execution_mode == "test_campaign"` → `kodo_mode="test"`, else `kodo_mode="goal"` (existing behaviour)
- `improve` lane: if `task.execution_mode == "improve_campaign"` → `kodo_mode="improve"`, else `kodo_mode="goal"` (existing behaviour)

`execution_mode` is parsed from the `mode:` field in the task body's `## Execution` section by the existing `TaskParser`.

Zero breaking change. Existing tasks without `task_kind` set continue to use `kodo --goal-file`.

---

## Resource Constraints

The spec director runs on the same machine as all other workers. It must not crowd out kodo processes or fill the disk.

### Concurrency

- **Brainstorm and compliance are API-only** (no kodo subprocess) — they do not increment the `max_concurrent_kodo` counter and do not need to pass the kodo gate.
- **Campaign task creation is gated** — `campaign_builder` checks `max_concurrent_kodo` before promoting any child task to `Ready for AI`. If the machine is already at the kodo concurrency limit, the task is created in `Backlog` and a note is added: `[queued: kodo concurrency limit reached]`. The reviewer watcher promotes it when a slot opens.
- **One campaign at a time** — enforced by `state/campaigns/active.json`. The spec director polls check this file before starting any new brainstorm call.

### Memory

- **Brainstorm does not launch kodo**, so `min_kodo_available_mb` is not checked before brainstorm calls.
- **Context bundle size is capped** before the brainstorm API call:
  - Insight snapshot: truncated to 8 KB (the most recent deriver outputs)
  - Git log: capped at 30 commits (already specified)
  - Board summary: capped at 50 task titles
  - Diff passed to compliance: truncated at 32 KB — if diff exceeds this, only the first 32 KB is sent with a `[diff truncated]` note in the prompt

### Disk Space

The spec director calls `_check_disk_space` (existing helper, `src/control_plane/execution/usage_store.py`) at two points:

1. **Before writing spec file** — checked against `docs/specs/` path. Below 200 MB free: log `spec_disk_space_low` warning and continue. Below 50 MB free: abort campaign creation, log `spec_disk_space_critical`, retry on next poll.
2. **Before writing `state/campaigns/active.json`** — same thresholds.

### Spec File Rotation

Completed and cancelled campaign specs accumulate in `docs/specs/`. To keep the directory bounded:

- On each spec director poll cycle, scan `docs/specs/*.md` for entries with `status: complete` or `status: cancelled` older than `spec_retention_days` (config, default `90`).
- Expired specs are moved to `docs/specs/archive/<slug>.md`.
- The archive is not automatically deleted — operator manages it.
- This prevents the specs index passed to brainstorm from growing unbounded over time.

### Campaign Task Limit

`campaign_builder` enforces `max_tasks_per_campaign` (config, default `6`) — the maximum number of child tasks (implement + test + improve across all spec goals) a single campaign can create. Specs that decompose into more goals than this cap are truncated at creation time with a `[campaign_task_limit: N goals omitted]` note on the parent Plane task. This prevents a single verbose spec from flooding the board.

---

## Configuration

New fields in `config/control_plane.local.yaml` (all optional, with defaults):

```yaml
spec_director:
  enabled: true
  poll_interval_seconds: 120
  spec_trigger_queue_threshold: 3    # queue drain trigger
  brainstorm_model: claude-opus-4-6
  compliance_model: claude-sonnet-4-6
  drop_file_path: state/spec_direction.md
  plane_spec_label: spec-request
  max_active_campaigns: 1
  max_tasks_per_campaign: 6          # child task cap per campaign
  spec_retention_days: 90            # days before completed specs are archived
  brainstorm_context_snapshot_kb: 8  # insight snapshot truncation limit
  compliance_diff_max_kb: 32         # diff truncation limit for compliance checks
```

---

## Error Handling

- **Brainstorm API failure**: log error, skip campaign creation, retry on next poll cycle. Do not archive the drop-file until a campaign is successfully created.
- **Spec write failure**: log error, do not create Plane tasks (partial state worse than no state).
- **Campaign builder partial failure**: if some child tasks fail to create, mark campaign `status: partial` in active.json and alert via Plane comment on parent task.
- **Compliance API failure**: 2 retries → CONCERNS verdict. Never blocks a PR silently.
- **Suppressor read failure**: fail open (allow heuristic proposal) and log warning. Better to create a duplicate than to silently block.

---

## What This Does Not Change

- Existing autonomy cycle (observe → insights → decide → propose) — untouched
- All five existing worker lanes — untouched except two new `task_kind` routes
- Kodo invocation pattern — `--goal-file` always present; `--test`/`--improve` are additive flags
- Reviewer watcher self-review path for non-campaign tasks — untouched
- Phase 6 (confidence calibration) and Phase 7 (experiment mode) — unblocked by this, not replaced

---

## Out of Scope

- Multi-campaign parallelism (one campaign per repo, one repo at a time)
- Spec editing loop (Claude revises spec based on failed tasks — future work)
- Webhook-driven triggers (polling only, consistent with existing architecture)
- Auto-merge without human review for any task kind
