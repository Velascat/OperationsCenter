# Upstream Patch Evaluation Examples

## Example 1: OpenClaw Changed-File Friction With Strong Evidence

Observed evidence:

- repeated `changed_file_uncertainty`
- execution records plus routing findings both show the issue
- recurring or persistent frequency
- major architectural impact for audit-sensitive tasks
- adapter workaround is high-complexity and brittle

Expected result:

- strong-evidence friction finding
- workaround assessment that still documents adapter-first as the baseline
- review-only proposal for an observability-improving upstream patch

## Example 2: Archon Limitation With Weak Evidence

Observed evidence:

- one or two support-check failures
- provider or workflow limitation appears real but is sparse

Expected result:

- weak-evidence finding
- no upstream patch proposal
- recommendation posture stays: document limitation and keep adapting locally

## Example 3: kodo Ergonomic Issue Best Left In Adapter Layer

Observed evidence:

- recurring wrapper or setup inconvenience
- workaround complexity is moderate
- workaround reliability is stable
- architectural impact is minor

Expected result:

- friction finding may still be recurring
- no proposal, because annoyance alone is not enough
- adapter-first posture remains clearly preferred

## Example 4: High-Value Candidate With High Divergence Risk

Observed evidence:

- recurring brittle parsing or missing structured results
- major impact and high expected value if fixed upstream
- but divergence risk is high

Expected result:

- proposal can still be generated
- divergence risk and maintenance burden must be explicit in the proposal
- proposal remains review-only, not an automatic commitment
