---
campaign_id: 6d1e8f3a-4c27-4b9e-a1d5-9f0e2b7c83a4
slug: decompose-setup-main
phases:
  - implement
  - test
  - improve
repos:
  - ControlPlane
area_keywords:
  - entrypoints/setup
  - setup
  - cli
  - ssh
  - config-render
status: active
created_at: 2026-04-18T00:00:00Z
---

## Overview

`src/control_plane/entrypoints/setup/main.py` is a 1,210-line file containing 43 top-level functions and 3 dataclasses that mix SSH key management, GitHub remote manipulation, interactive CLI prompting, repository discovery, config/env file rendering, and the main wizard flow. This campaign extracts cohesive function groups into focused submodules inside the `entrypoints/setup/` package (which already contains `providers.py` and `doctor.py`), reducing `main.py` to a thin wizard orchestrator of ≤ 400 lines.

## Goals

1. **Extract SSH and GitHub remote helpers** — Move `find_ssh_key_pair`, `generate_ssh_key`, `start_ssh_agent`, `add_ssh_key_to_agent`, `verify_github_ssh`, `github_https_to_ssh`, `get_origin_remote_url`, `set_origin_remote_url`, and `ensure_github_ssh_setup` into `entrypoints/setup/ssh.py`. These 9 functions form a self-contained group that manages SSH key generation, agent interaction, and GitHub remote URL conversion. They have no dependencies on the wizard state or rendering logic. Update imports in `main.py` to re-export from the new module.

2. **Extract config/env rendering functions** — Move the 3 dataclasses (`RepoSetupAnswers`, `RepoDiscoveryChoice`, `SetupAnswers`) and the rendering functions (`render_settings_yaml`, `render_env_file`, `render_task_template`, `shell_quote`) into `entrypoints/setup/rendering.py`. These are pure functions that take a `SetupAnswers` object and produce file content strings. They depend on no interactive I/O or external commands.

3. **Extract repo-discovery and CLI utility helpers** — Move `split_multiline`, `split_csv`, `print_section`, `print_banner`, `prompt_with_default`, `prompt_choice`, `parse_github_remote`, `infer_repo_key_from_clone_url`, `discover_repo_choices`, `parse_remote_branches`, `discover_remote_branches`, `prompt_branch_selection`, `prompt_repo`, `prompt_repo_with_discovery`, `load_env_exports`, `load_existing_config`, `existing_config_value`, `read_saved_plane_start_command`, and `resolve_default_plane_start_command` into `entrypoints/setup/prompts.py`. These functions handle interactive prompting, repo discovery heuristics, and config file loading. After this extraction, `main.py` retains only the `main()` wizard function, tool-installation helpers (`prepend_local_bin_to_path`, `check_command_installed`, `ensure_uv_installed`, `ensure_kodo_installed`, `verify_kodo`, `verify_plane_configuration`, `run_local_command`, `maybe_open_url`, `provider_default_orchestrator`, `default_orchestrator_for_statuses`), and re-exports.

## Constraints

- **Backward-compatible imports**: `main.py` must re-export every moved symbol so that any existing `from control_plane.entrypoints.setup.main import X` continues to work.
- **No logic changes**: Each goal is a pure move-and-import refactor. Do not rename functions, change signatures, or alter behavior.
- **Incremental**: Each goal is a standalone PR-able commit. Tests must pass after each extraction.
- **Goal ordering**: Goal 2 should be done before Goal 3 because `prompts.py` will import the dataclasses from `rendering.py`. Goal 1 is independent of both.
- **Existing modules untouched**: `providers.py` and `doctor.py` are not modified by this campaign.
- **Test files stay as-is**: `test_setup_cli.py` should not be split in this campaign.

## Success Criteria

- `main.py` contains only the `main()` wizard function, tool-installation helpers, and re-export lines — under 400 lines total.
- Three new modules exist: `ssh.py`, `rendering.py`, `prompts.py`, each under 400 lines.
- `python -m pytest tests/test_setup_cli.py` passes without modification to the test file.
- `ruff check src/control_plane/entrypoints/setup/` reports no errors.
- No circular imports between the new modules or with `main.py`.
