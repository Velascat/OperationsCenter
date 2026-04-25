## Summary

<!-- One or two sentences describing what this PR does and why. -->

## Changes

<!-- Bullet list of what changed. -->

-

## Invariant Checklist

- [ ] Contracts remain single-sourced in `contracts/`
- [ ] Policy gate is still enforced before adapter dispatch
- [ ] No alternate execution paths introduced
- [ ] Failures are explicit — no silent fallback routing

## Testing

- [ ] Tests pass: `.venv/bin/python -m pytest tests/ -v`
- [ ] Linter passes: `ruff check src/`
- [ ] New behavior is covered by tests
- [ ] Policy-blocking case tested (if policy-related change)

## Related Issues

<!-- Closes #N or References #N -->

## Notes for Reviewer

<!-- Anything non-obvious: edge cases, trade-offs, adapter-specific behavior, follow-up items. -->
