# Flow Audit

The ghost-work audit asks "what's the system doing that produces no value?"
This audit asks the complementary question: **"what value-producing flow is
under-implemented or missing entirely?"** The two are siblings — ghost work
is wasted effort, flow gaps are *silent* losses (work that doesn't happen,
states the system can't represent, edges that aren't covered).

Like the ghost-work audit, the goal is a *finite catalog*: every observed
flow gap should match a listed pattern or motivate a new one, and each
pattern lists a status + a mechanical fix or a justification for not
fixing it yet.

## Patterns

| ID | Pattern | What's missing | Impact | Status | Fix |
|----|---------|---------------|--------|--------|-----|
| F1 | Stale `Running` task auto-recovery | A worker dies mid-task → Plane shows Running forever; ghost-audit detects but nothing reclaims | Operator must manually reset; queue stalls if it's the only worker per role | **Fixed** | New `recover_stale_running` step in the watchdog: any `Running` task with `updated_at` older than `reviewer.stale_running_reclaim_seconds` (default 4h) gets reset to `Ready for AI` with an audit comment |
| F2 | Transient kodo failure retry | A network blip / 429 mid-run blocks the task with `category=backend_error`; no retry | Operator manually re-promotes; can lose hours of progress | **Open** | Detect transient categories (BACKEND_ERROR + reason matches network-shaped patterns) → retry once with a fresh workspace before marking Blocked |
| F3 | Proposal deduplication | Two propose runs on overlapping signals create duplicate Backlog tasks (we observed two "Restore repeated missing test_signal coverage" tasks) | Workers double-claim, redundant kodo runs, redundant PRs | **Fixed** | Propose now refuses to create a Plane task whose normalised title matches an existing non-terminal task in the same family within the last 7 days |
| F4 | Quota-aware throttling | Workers keep claiming when daily LLM budget is exhausted, then crash on quota errors | Wasted retry attempts, confusing Blocked tasks | **Partial** | `RepoSettings.max_daily_executions` exists but nothing stops the *worker* from claiming when the budget is hit. Add a pre-claim budget check |
| F5 | Upstream task dependencies | Goal B implicitly depends on Goal A's merge (e.g. test follow-up needs the implementation merged) but no way to express it | Ordering races; failures when A is still in-flight | **Open** | `depends-on: <task_id>` label honoured by `_claim_next` (skip if dependency not Done) |
| F6 | Campaign progress visibility | Spec campaigns track phase advancement but no surface shows "75% done, 3 tasks left, 2 blocked" — operators can't tell if a campaign is healthy | Stalled campaigns invisible; investment lost | **Open** | New `campaign_progress` panel in the status pane, sourced from `state/campaigns/active.json` + Plane child counts |
| F7 | Cross-repo coordination | Multi-repo changes (touch shared interface in repo A, update consumer in repo B) have no shared session | Tasks land out of order, breaks builds | **Open** | Out of scope — needs a "linked tasks" Plane primitive. Document and defer. |
| F8 | Back-pressure on queue size | `propose` keeps generating when `Ready for AI` already has 100+ tasks | Compounds queue → impossible to triage | **Partial** | `propose_skip_when_ready_count: 8` exists but defaults are loose. Fixed by lowering default to 8 and surfacing the override in docs |
| F9 | Per-task cost accounting | LLM token usage is global, not attributed; can't tell which task type / family is expensive | Can't tune budgets, can't spot runaways | **Open** | Wrap kodo invocation in a token-counting recorder that writes to `state/cost_per_run/<run_id>.json`; aggregate per family |
| F10 | Intent verification before merge | Phase 1 LGTM is kodo grading kodo. No semantic check that the goal was actually achieved | Self-approved no-ops can land | **Open** | Add a separate "verifier" pass with a *different* model than the worker (Opus reviewing Sonnet, etc.). Tracked in F10. |
| F11 | Retry counter on non-split follow-ups | scope-split follow-ups have `retry-count`; verification follow-ups (`_create_follow_up`) don't, can loop | Same goal text retried indefinitely | **Fixed** | All follow-ups created by `_create_follow_up` now carry retry-count and refuse past depth 3 |
| F12 | Alerting on stall | If propose lane stops emitting for 24h or all workers crash-loop, no alert | Silent failures | **Open** | Watchdog can already detect heartbeat staleness; needs a notification channel (email / Slack / status pane red banner) |
| F13 | Stale state file cleanup | `state/proposal_feedback/`, `state/pr_reviews/` accumulate forever | Disk usage; slows scans | **Fixed** | New `cleanup_state` maintenance step: removes records older than `state_retention_days` (default 90) where the corresponding Plane task is terminal |
| F14 | Bisection on test_failure | Test follow-up fails → blame is unclear (which goal change broke things?) | Operator does manual bisection | **Open** | Out of scope — needs commit-level provenance the contract doesn't carry. Defer. |
| F15 | Trust ramp-up for reviewer | After N consecutive Phase 1 LGTMs that survive in main, reviewer's trust should increase (e.g., loosen review constraints, raise auto-merge thresholds) | Flat trust = either too strict or too loose | **Open** | Track LGTM-then-clean ratio per repo in `state/reviewer_trust/`; gate `auto_merge_on_ci_green` on it |

## Classification

- **Fixed in this audit pass**: F1, F3, F11, F13
- **Partially addressed**: F4 (existing config not enforced), F8 (defaults loose)
- **Open with concrete fix in mind**: F2, F5, F6, F9, F10, F12, F15
- **Out of scope / explicitly deferred**: F7, F14

## Encoded check

```
python -m operations_center.entrypoints.flow_audit \\
    --config config/operations_center.local.yaml [--since 24h]
```

For each fixed pattern, asserts the symptom did NOT occur in the window
(zero stale Running, zero proposal duplicates, etc.). For partial / open
patterns, reports the size of the gap (e.g. "47 stale state files older
than 90d").

## Adding a new flow gap

1. Append a row to the table with a new `Fn` ID.
2. If the gap is mechanically fixable, mark **Open** + describe the fix.
3. If a fix lands, change to **Fixed** + link the commit.
4. Update `entrypoints/flow_audit/main.py` with a detector that asserts the
   fix held (or measures the size of the unfixed gap).
