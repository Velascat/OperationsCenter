# Code-Health Audit

Third audit catalog, alongside `ghost_work_audit.md` (wasted runtime work)
and `flow_audit.md` (under-implemented runtime gaps). Where those two
inspect runtime symptoms, this one inspects the **source tree itself** —
underutilised, unfinished, or dead code: scaffolded backends with no
implementation, settings fields that nobody reads, entrypoints with no
consumers, untagged TODO debt, and so on.

## Patterns

| ID | Pattern | What it flags | Status |
|----|---------|---------------|--------|
| C1 | Scaffolded-but-unimplemented backends | Modules under `src/operations_center/backends/<name>/` whose `invoke.py` raises NotImplementedError as the main op | **Open** |
| C2 | Untagged TODO/FIXME debt | TODO/FIXME/XXX/HACK markers without `[deferred, reviewed YYYY-MM-DD]` and not in a TODO-domain module (e.g. todo_signal collector) | **Open** |
| C3 | Orphaned entrypoints | `entrypoints/<name>/` modules with no reference outside themselves (no test, no script, no doc, no `pyproject.toml` console_scripts entry) | **Open** |
| C4 | Ruff lint findings | Anything `ruff check` reports — treated as a code-health regression rather than a pre-commit nag | **Fixed when zero** |
| C5 | Unconditional skipped tests | `pytest.skip` / `@pytest.mark.skip` whose surrounding context lacks an env-probe (e.g. `shutil.which` for an external binary) | **Open** |
| C6 | Modules called only from tests | Production modules whose every importer lives under tests/ — placeholder; needs proper static analysis | **Open / placeholder** |
| C7 | Dead settings fields | Fields declared on `Settings` / `RepoSettings` / etc. that no production code reads via `.field` or `["field"]`, even self-references inside settings.py | **Open** |

## Encoded check

```
python -m operations_center.entrypoints.code_health_audit
```

Output is JSON keyed by `Cn`, symmetric with the other audits.

## Acting on findings

Unlike the runtime audits, code-health findings are **not** all
mechanically fixable — many require deliberate decisions. Suggested
treatment per pattern:

- **C1** scaffolded backends: either finish the implementation, delete the
  module, or move it to a `experimental/` subtree with a clear note. We
  shipped two backend skeletons (openclaw, archon) that haven't been used
  in production; they're candidates for the chopping block once we're
  certain nobody plans to revisit them.
- **C2** untagged TODOs: either fix, delete, or convert to
  `[deferred, reviewed YYYY-MM-DD]` to acknowledge the debt.
- **C3** orphaned entrypoints: wire into `operations-center.sh`,
  add to `pyproject.toml` console_scripts, document in a README, or
  delete.
- **C4** ruff findings: just fix them — this is the cheapest category.
- **C5** unconditional skips: turn into env-gated skips, fix the
  underlying breakage, or delete the test.
- **C7** dead settings fields: each one represents a knob that was
  designed-in but never consumed. Either wire the consumer or remove the
  field. Worth pruning every quarter — keeps the config schema honest.

## Adding a new pattern

1. Append a row to the table above.
2. Add a detector to `entrypoints/code_health_audit/main.py`.
3. If the pattern is mechanically fixable, the fix lands separately;
   the detector just measures the gap.
