<!-- console-context -->
## OperatorConsole Context

At the start of each session, read the compiled context before acting:

- `.console/.context` — compiled startup context (generated fresh each launch)

The context file contains your current task, guidelines, backlog, log, and runtime context.

**Source files** (editable truth — update these, not the context file):

| File | Role |
|------|------|
| `.console/task.md` | Current objective and definition of done |
| `.console/guidelines.md` | Repo policy, branch rules, operating constraints |
| `.console/backlog.md` | Work inventory — in-progress, up-next, done |
| `.console/log.md` | Recent decisions, stop points, what changed and why |

After meaningful progress, update `.console/backlog.md` and `.console/log.md`.
Do not edit `.console/.context` directly — it is regenerated at each launch.
