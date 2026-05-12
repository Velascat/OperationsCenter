# Managed-Repo Config — Troubleshooting Guide

Operators bind OperationsCenter to a managed repo by placing a YAML config in
`config/managed_repos/local/<repo_id>.yaml` (gitignored). This guide covers
common mistakes, field migrations, and debugging steps.

---

## Quick checks

```bash
# Validate a local config right now
python - <<'EOF'
import yaml
from operations_center.managed_repos.models import ManagedRepoConfig
data = yaml.safe_load(open("config/managed_repos/local/my_repo.yaml"))
cfg = ManagedRepoConfig.model_validate(data)
print("OK:", cfg.repo_id)
EOF
```

---

## Common mistakes

### 1. Local config file not created

`loader.py` falls back to the tracked example when `local/` is absent. If OC
dispatches against the example repo instead of your managed repo, this is why.

**Fix:** Copy the template and edit it:
```bash
cp config/managed_repos/example_managed_repo.yaml \
   config/managed_repos/local/<repo_id>.yaml
# Edit the copy — change every placeholder value
```

### 2. `repo_root` path is wrong

`repo_root` must be a path **relative to the OperationsCenter repo root**, not
relative to the local config file or to `$HOME`.

**Fix:**
```yaml
repo_root: ../MyManagedRepo    # sibling of OperationsCenter on disk
```

Not:
```yaml
repo_root: /home/dev/Documents/GitHub/MyManagedRepo  # absolute paths break portability
repo_root: MyManagedRepo                               # relative to nowhere useful
```

### 3. `capabilities: [audit]` but `audit:` block is absent

The semantic validator in `ManagedRepoConfig` raises at load time:

```
ValueError: capabilities includes 'audit' but audit field is absent
```

**Fix:** Add a complete `audit:` block, or remove `audit` from `capabilities`.

### 4. `audit:` present but `audit_types` is empty

```
ValueError: audit field is present but audit_types is empty
```

**Fix:** Add at least one `audit_types` entry. Copy the shape from
`config/managed_repos/example_managed_repo.yaml`.

### 5. Missing field after a model upgrade (OC11 alert)

When `ManagedRepoConfig` gains a new Pydantic field, Custodian's OC11 detector
flags it:

```
models.py field `new_field` has no matching key in example_managed_repo.yaml
```

This also means any local configs created before the upgrade are missing the
field. Pydantic will use the field's default if one exists, or raise a
`ValidationError` if the field is required.

**Fix:**
1. Resolve the OC11 alert by adding the new field to `example_managed_repo.yaml`
   with a documented placeholder value.
2. Update each `local/*.yaml` binding to include the new field.
3. Re-validate with the quick-check snippet above.

### 6. Private repo name appears in a tracked OC file (B1 alert)

Custodian B1 blocks any tracked OC file that mentions a private repo name.
Never put private repo details in:

- `src/` Python source files
- `docs/` markdown files
- `config/managed_repos/` tracked YAML (only the `local/` subdirectory is safe)
- `.custodian/config.yaml`

The plumbing contract for a private managed repo belongs in **the private repo's
own** `.custodian/config.yaml`, with `reader_path: ../OperationsCenter/...`
pointing at the OC reader. OC never needs to declare the private repo's name.

---

## Field upgrade / migration path

When a new field is added to `ManagedRepoConfig`:

1. The PR that adds the field must also update `example_managed_repo.yaml`
   (OC11 enforces this).
2. The field's Pydantic default documents the safe fallback value.
3. Operators update their `local/*.yaml` at their own pace — the default ensures
   existing configs don't break immediately.
4. Required fields (no default) must be added with caution; they break all
   existing local configs until updated. Add a default or phase the rollout.

---

## Debugging a failing audit dispatch

1. **Validate the config** — run the quick-check snippet above.
2. **Check `repo_root` resolves** — `Path(repo_root).resolve()` from OC's working
   directory should point to the managed repo.
3. **Check `audit_types[*].command`** — run the command manually from `working_dir`
   with `AUDIT_RUN_ID=test_run_id` injected.
4. **Check `command_status`** — set to `verified` only after a successful manual run.
5. **Check `status_file` template** — the `{output_dir}` and `{bucket}` placeholders
   must expand to a path that the managed repo actually writes.
6. **Check `run_status_finalization`** — if `false`, OC never waits for a final
   status; set to `true` only when the managed repo writes a terminal status value.

---

## Related

- `config/managed_repos/example_managed_repo.yaml` — annotated template
- `src/operations_center/managed_repos/models.py` — Pydantic schema
- `src/operations_center/managed_repos/loader.py` — two-tier config loader
- `docs/architecture/adr/0004-managed-repo-private-overlay.md` — design rationale
