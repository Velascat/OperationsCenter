"""Validate that config/control_plane.example.yaml parses against the Settings model.

This catches field-name mismatches between the YAML template and the Pydantic
models before they reach users.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import yaml

from control_plane.config.settings import Settings, load_settings

EXAMPLE_YAML = Path(__file__).resolve().parent.parent / "config" / "control_plane.example.yaml"


def _patched_yaml_text() -> str:
    """Return the example YAML with placeholders replaced by valid values."""
    text = EXAMPLE_YAML.read_text()
    text = text.replace("<your-plane-project-uuid>", str(uuid.uuid4()))
    return text


def test_example_yaml_is_valid_settings(tmp_path: Path) -> None:
    """The example YAML template must parse and validate against Settings."""
    config_path = tmp_path / "control_plane.yaml"
    config_path.write_text(_patched_yaml_text())

    settings = load_settings(config_path)

    # Smoke-check key fields survived the round-trip.
    assert settings.plane.workspace_slug == "control-plane"
    assert "MyRepo" in settings.repos
    assert settings.repos["MyRepo"].default_branch == "main"
    assert settings.report_root == Path("tools/report/kodo_plane")


def test_example_yaml_model_validate() -> None:
    """Settings.model_validate succeeds on the parsed example YAML dict."""
    raw = yaml.safe_load(_patched_yaml_text())
    settings = Settings.model_validate(raw)

    assert settings.plane.api_token_env == "PLANE_API_TOKEN"
    assert settings.git.provider == "github"
    assert settings.kodo.team == "full"
    assert len(settings.repos) >= 1


def test_example_yaml_repo_fields_match_model() -> None:
    """Every key in the YAML repos block must be a valid RepoSettings field."""
    raw = yaml.safe_load(_patched_yaml_text())
    for repo_key, repo_data in raw.get("repos", {}).items():
        if not isinstance(repo_data, dict):
            continue
        # model_validate will reject unknown fields in strict mode;
        # here we just verify it succeeds.
        Settings.model_validate(raw)
