# Kodo — Subjective Commentary

> Capability/runtime cards are objective. This file holds the opinion-shaped
> commentary the catalog is forbidden to use for routing decisions.

## Strengths

- Already supports the CLI-subscription pattern through team config
  (e.g. `_CLAUDE_FALLBACK_TEAM` shells out to the local `claude` CLI).
- Structured rate-limit / quota signals — adapter classifies Sonnet
  weekly cap, codex quota exhaustion, etc. cleanly.
- Single-agent — internal-routing complexity is N/A.

## Weaknesses

- Runtime selection lives in team config, not OC RuntimeBinding (G-001).
- Tracking drift between bound RuntimeBinding and actual team-config-driven
  invocation requires a wrapper layer until G-001 closes.

## Good for

- Architecture-design lane via Claude CLI Opus (the spec's Special Use Case).
- Small/medium repo patches with test runs.

## Bad for

- Multi-agent graph workflows (Archon's territory).
- Workflows that need per-call provider switching without team config edits.
