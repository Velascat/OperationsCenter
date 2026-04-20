<!-- fob-context -->
## FOB Briefing

At the start of each session, read the compiled briefing before acting:

- `.fob/.briefing` — compiled startup context (generated fresh each launch)

The briefing contains your mission, standing orders, objectives, recent log, and runtime context.

**Source files** (editable truth — update these, not the briefing):

| File | Role |
|------|------|
| `.fob/active-mission.md` | Current objective and definition of done |
| `.fob/standing-orders.md` | Repo policy, branch rules, operating constraints |
| `.fob/objectives.md` | Work inventory — in-progress, up-next, done |
| `.fob/mission-log.md` | Recent decisions, stop points, what changed and why |

After meaningful progress, update `.fob/objectives.md` and `.fob/mission-log.md`.
Do not edit `.fob/.briefing` directly — it is overwritten at each launch.
