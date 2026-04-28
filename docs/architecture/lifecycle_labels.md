# Lifecycle Labels

Lifecycle labels are the autonomy loop's vocabulary for *where in its life* a
task currently sits, beyond the four Plane states (Backlog / Ready for AI /
Running / Done|Blocked|Cancelled). They mark situations the state machine
alone can't express — for example, "this task's work has been delegated to
children and the parent is intentionally quiescent."

Every lifecycle label is honoured by *some* downstream consumer. We don't add
labels speculatively; each entry below has a corresponding code path that
reads it and modifies behaviour.

## Vocabulary

| Label | Meaning | Applied by | Honoured by |
|-------|---------|-----------|-------------|
| `lifecycle: expanded` | Work delegated to spawned children; parent is quiescent and waiting for them. | `board_worker._create_split_followups` (today: scope_too_wide split; future: any decomposition) | `spec_director._handle_blocked` (skip rewrite); `board_worker._maybe_close_split_parent` (auto-close on last child Done) |
| `lifecycle: superseded` | Replaced by a better-formed successor. Original description preserved as a comment so history is auditable. | `spec_director._handle_blocked` when it rewrites a description (the *previous version* is superseded by the new one). Also future: deduplication. | Ghost-audit (regression detection); operators inspecting task history |
| `lifecycle: escalated` | Escalated beyond automated handling — needs a human or longer review window. | `pr_review_watcher` Phase 2 entry, `phase_orchestrator` cancellation | Status pane (highlight escalated tasks); auto-promote (skip) |
| `lifecycle: archived` | Retired but kept for traceability. No further automated action will touch it. | `spec_writer.archive_expired` (expired campaign specs); future: campaign completion | All processors (universal skip — terminal-and-frozen) |

## Adding a new label

1. Append a row to the table above with a real consumer cited.
2. Define a constant where it's applied (e.g. `_LIFECYCLE_FOO = "lifecycle: foo"`).
3. Wire the consumer(s). At minimum, every blocked-task processor must check
   for `expanded` and `archived`.
4. Add a detector to `entrypoints/ghost_audit/main.py` if violation of the
   semantics would constitute ghost work.

## Naming convention

`lifecycle: <verb-past-participle>` — describes a state of being, not an
action. So `expanded` (state) not `expanding` (active), `superseded` not
`superseding`. This makes it grammatically clear the label describes
where the task IS, not what's happening to it.

## Why labels and not Plane states

Plane states are global and finite (a workspace setting). Lifecycle is
fine-grained: a task can be Blocked AND expanded, or Done AND archived.
Labels stack; states don't. We could have built a parallel state machine
on top of Plane fields, but labels are simpler, queryable via the existing
list_issues path, and human-readable on the board.
