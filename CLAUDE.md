<!-- console-context -->
## OperatorConsole Briefing

At the start of each session, read the compiled briefing before acting:

- `.console/.briefing` — compiled startup context (generated fresh each launch)

The briefing contains your mission, standing orders, objectives, recent log, and runtime context.

**Source files** (editable truth — update these, not the briefing):

| File | Role |
|------|------|
| `.console/active-task.md` | Current objective and definition of done |
| `.console/directives.md` | Repo policy, branch rules, operating constraints |
| `.console/objectives.md` | Work inventory — in-progress, up-next, done |
| `.console/mission-log.md` | Recent decisions, stop points, what changed and why |

After meaningful progress, update `.console/objectives.md` and `.console/mission-log.md`.
Do not edit `.console/.briefing` directly — it is overwritten at each launch.
