# Spec Director Redesign

**Date:** 2026-04-17
**Status:** approved
**Author:** operator + Claude
**Supersedes:** `docs/superpowers/specs/2026-04-15-autonomous-spec-driven-chain-design.md`

---

## Goal

Fix the spec director so campaigns actually complete. The existing code has five gaps that prevent any campaign from finishing:

1. `source: spec-campaign` not in `_AUTO_SOURCES` → implement tasks sit in Backlog forever
2. No phase orchestration → test/improve tasks never promoted from Backlog
3. Blocked tasks have no autonomous unblock path
4. Context bundle passes noisy heuristic signals (insight_snapshot), misses useful board signals
5. `RecoveryService.revise_spec()` revises the spec to match violating code — backwards, removes the point of having a spec

**All Claude calls use Claude CLI** (`claude` subprocess via `ClaudeCodeOrchestrator`). No direct Anthropic SDK calls.

---

## Architecture

New module `phase_orchestrator.py` is the primary addition. Everything else is a targeted fix or trim.

| Module | Change |
|---|---|
| `spec_director/phase_orchestrator.py` | **New** — owns implement→test→improve advancement and blocked-task unblocking |
| `spec_director/trigger.py` | **Rewrite** — queue drain primary, drop file secondary, remove Plane label trigger |
| `spec_director/context_bundle.py` | **Refactor** — strip insight_snapshot, add recent Done/Cancelled tasks, per-repo git log |
| `spec_director/state.py` | **Trim** — board is ground truth, local JSON becomes thin index only |
| `spec_director/recovery.py` | **Trim** — remove `revise_spec()`, keep `should_abandon()` and `self_cancel()` |
| `spec_director/models.py` | **Minor** — remove phase tracking fields (now derived from board) |
| `spec_director/campaign_builder.py` | **Minor** — ensure `source: spec-campaign` label is set |
| `spec_director/brainstorm.py` | **Minor** — improve prompt to include available repos |
| `entrypoints/spec_director/main.py` | **Rewrite** — run phase orchestration first each cycle, then trigger detection |
| `entrypoints/worker/main.py` | **Fix** — add `"spec-campaign"` to `_AUTO_SOURCES` |

---

## Section 1: `_AUTO_SOURCES` Fix

In `entrypoints/worker/main.py`, add `"spec-campaign"` to the `_AUTO_SOURCES` set so implement-phase tasks created by `campaign_builder` are eligible for standard backlog promotion:

```python
_AUTO_SOURCES: frozenset[str] = frozenset({
    "proposer", "autonomy", "improve-worker",
    "reviewer-dep-conflict", "post-merge-ci",
    "multi-step-plan", "spec-campaign",   # ← add this
})
```

This fixes implement-phase tasks getting stuck in Backlog. Test/improve-phase tasks are managed exclusively by the phase orchestrator (not standard backlog promotion).

---

## Section 2: Phase Orchestrator

New file: `src/control_plane/spec_director/phase_orchestrator.py`

Runs at the top of every spec-director cycle before trigger detection.

### Phase advancement

For each active campaign, reads the board to determine if a phase transition is due:

```
implement → test_campaign:
  ALL tasks where (campaign-id=<id> AND task_phase=implement) are in {Done, Cancelled}
  AND at least one test_campaign task exists in Backlog

test_campaign → improve_campaign:
  ALL tasks where (campaign-id=<id> AND task_phase=test_campaign) are in {Done, Cancelled}
  AND at least one improve_campaign task exists in Backlog
```

**Blocked tasks pause advancement.** If any task in the current phase is Blocked, the next phase stays in Backlog until it is resolved.

On advancement: calls `transition_issue()` for each next-phase task (Backlog → Ready for AI), then posts a comment on the `[Campaign]` parent task: `"Advancing to test phase: N tasks promoted."`

### Blocked task unblocking

For each Blocked campaign task, the phase orchestrator runs a Claude-assisted description rewrite:

1. Assemble context: spec text, original task description, failure comment kodo left, task_phase
2. Call Claude CLI with goal: "Rewrite this task description so it is clearer and more actionable. Do not change the spec. Output only the new task description."
3. Update the task description in Plane with the rewritten text
4. Move task from Blocked → Ready for AI
5. Increment `block_rewrite_count` in the task description's `## Execution` section

**Two-strike rule:** if `block_rewrite_count >= 2`, cancel the task instead of rewriting. Post comment: `"Task cancelled after 2 rewrite attempts: <reason>."` The phase orchestrator then re-evaluates phase advancement — a cancelled task counts as terminal.

`block_rewrite_count` is stored in the task description body so it survives worker restarts with no extra state file.

### Campaign completion

When all three phases are terminal (all tasks Done or Cancelled):

1. Transition `[Campaign]` parent task to Done
2. Post summary comment: `"Campaign complete. X tasks done, Y cancelled."`
3. Mark campaign `status: complete` in local index

### Interface

```python
class PhaseOrchestrator:
    def __init__(self, client: PlaneClient, settings: Settings) -> None: ...

    def run(self, issues: list[dict]) -> PhaseOrchestrationResult: ...
    # Issues list is fetched once per cycle by main.py and passed in.
    # Returns counts of promotions, rewrites, completions for logging.
```

---

## Section 3: Trigger Detection (Rewrite)

File: `src/control_plane/spec_director/trigger.py`

Two triggers in priority order. Checked only when no campaign is currently active.

**Priority 1 — Drop file (operator-directed):**
- `state/spec_direction.md` exists → read content as seed text → start campaign
- File is deleted only after campaign is successfully created (brainstorm + spec write + Plane tasks all succeed)
- If campaign creation fails, file stays in place for retry next cycle

**Priority 2 — Queue drain (autonomous):**
- Zero Ready for AI tasks AND zero Running tasks across all watched repos → start autonomous campaign with empty seed
- Claude picks direction from context bundle alone

**Removed:** Plane label trigger (`spec-request`). It was a workaround — the drop file serves the same purpose more cleanly.

**One campaign at a time.** Both triggers are skipped while `state/campaigns/active.json` has any entry with `status: active`.

---

## Section 4: Context Bundle (Refactor)

File: `src/control_plane/spec_director/context_bundle.py`

What Claude receives when brainstorming a new spec:

| Signal | Source | Notes |
|---|---|---|
| Recent Done tasks (last 14 days) | Board | What was just built — avoid duplicating |
| Recent Cancelled tasks (last 14 days) | Board | What failed — avoid re-proposing |
| Active campaigns summary | Board (or local index) | What is already in flight |
| Specs index | `docs/specs/` front matter | Previously specced areas |
| Git log per watched repo (last 30 commits) | `git log --oneline -30` | What is actually changing |
| Open task count by state | Board | How busy the board is |
| Seed text (if any) | Drop file or empty string | Optional operator hint |
| Available repos | `settings.repos` keys | Tells Claude what repos exist |

**Removed:** `insight_snapshot` (noisy heuristic output, not useful strategic signal).

The brainstorm prompt includes the list of available repo keys so Claude can scope its suggestions to a real repo.

---

## Section 5: Campaign State (Thin Index)

File: `src/control_plane/spec_director/state.py`

Board is the ground truth for phase state, task counts, and task status. The local JSON becomes a thin index — only what cannot be efficiently derived from the board at poll time.

**`state/campaigns/active.json` schema (trimmed):**

```json
[
  {
    "campaign_id": "<uuid>",
    "slug": "<slug>",
    "spec_file": "docs/specs/<slug>.md",
    "status": "active",
    "created_at": "<iso>"
  }
]
```

**Removed from state file:** `area_keywords` (read from spec front matter when needed), `last_progress_at` (derived from board task updated_at), `phase` (derived from board task states).

`CampaignStateManager` interface:

```python
class CampaignStateManager:
    def load(self) -> list[CampaignRecord]: ...
    def add(self, record: CampaignRecord) -> None: ...
    def mark_complete(self, campaign_id: str) -> None: ...
    def mark_cancelled(self, campaign_id: str) -> None: ...
```

No phase tracking. No progress tracking. The phase orchestrator derives everything it needs from `list_issues()` filtered by `campaign-id` label.

---

## Section 6: Recovery (Trim)

File: `src/control_plane/spec_director/recovery.py`

**Remove `revise_spec()`** — it revised the spec to match violating code. This is backwards: if a task produces non-compliant code, the code is wrong, not the spec. Blocked task rewriting (section 2) is the correct resolution path.

**Keep:**

```python
class RecoveryService:
    def should_abandon(self, campaign: CampaignRecord, issues: list[dict]) -> bool:
        """True if campaign has been active beyond campaign_abandon_hours with no terminal tasks."""

    def self_cancel(self, campaign: CampaignRecord, reason: str) -> None:
        """Cancel all non-Done tasks, close open PRs, mark campaign cancelled, release suppression."""
```

`should_abandon()` and `self_cancel()` are called by `main.py` at the end of each cycle after phase orchestration runs.

---

## Section 7: Main Loop (Rewrite)

File: `src/control_plane/entrypoints/spec_director/main.py`

Cycle order:

```
1. Fetch all issues once (single list_issues call)
2. PhaseOrchestrator.run(issues)      — advance phases, unblock tasks, detect completions
3. RecoveryService.should_abandon()   — if yes: self_cancel() and continue
4. TriggerDetector.detect(issues)     — only if no active campaign after steps 2-3
5. If trigger fired: BrainstormService → spec write → CampaignBuilder → state.add()
6. Sleep poll_interval_seconds
```

Phase orchestration runs before trigger detection so a completing campaign releases the "one campaign at a time" gate in the same cycle it completes, rather than one cycle later.

---

## Section 8: Brainstorm Prompt (Minor Improvement)

File: `src/control_plane/spec_director/brainstorm.py`

Add available repos to the system prompt:

```
Available repos: {", ".join(settings.repos.keys())}

Scope your spec to exactly one of these repos. Set the `repos:` front matter field to the repo key.
```

No other changes to brainstorm.py.

---

## What This Does Not Change

- Existing autonomy cycle (observe → insights → decide → propose) — untouched
- All five existing worker lanes — untouched
- `KodoAdapter.build_command()` — already handles `kodo_mode` (`goal`/`test`/`improve`), keep as-is
- `SpecComplianceService` — already integrated in reviewer worker, keep as-is
- `suppressor.py` — keep as-is
- `campaign_builder.py` task structure — keep as-is (labels, description sections, phase task creation)
- Reviewer watcher self-review path for non-campaign tasks — untouched

---

## Out of Scope

- Multi-repo campaigns (single-repo for now; architecture does not prevent extension)
- Parallel campaigns (one at a time)
- Webhook-driven triggers (polling only)
- Auto-merge without human review
