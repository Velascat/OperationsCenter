# Capacity-exhaustion fixtures

Real-shape stdout/stderr captures from backends that print
capacity-exhaustion notices and exit 0. Pinned here so the G-V04 /
G-005 classifier in
`operations_center.backends._capacity_classifier` can be regression-
tested against the actual in-the-wild form, not just the synthetic
phrases used in the unit tests.

## Files

| Path | Provenance | Trigger |
|------|------------|---------|
| `claude_code_extra_usage.stdout.txt` | Real claude-code session terminating mid-task | Anthropic-side extra-usage exhaustion |

## Adding a new fixture

1. Capture a complete stdout/stderr that demonstrates the false-success
   pattern. Redact any account-specific URLs, IDs, or PII first.
2. Drop it as `<backend>_<scenario>.stdout.txt` (or `.stderr.txt`).
3. Add a row to the table above.
4. Add a single regression assertion in
   `tests/unit/backends/test_capacity_classifier_regression.py` —
   one line per fixture, just confirming the classifier matches.
