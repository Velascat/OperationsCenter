from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class PlaneSettings(BaseModel):
    base_url: str
    api_token_env: str
    workspace_slug: str
    project_id: str


class GitSettings(BaseModel):
    provider: str = "github"
    token_env: str | None = None
    open_pr_default: bool = True
    push_on_validation_failure: bool = True
    author_name: str = "Control Plane Bot"
    author_email: str = "control-plane-bot@example.com"


class KodoSettings(BaseModel):
    binary: str = "kodo"
    team: str = "full"
    cycles: int = 3
    exchanges: int = 20
    orchestrator: str = "api"
    effort: str = "medium"
    timeout_seconds: int = 3600


class RepoSettings(BaseModel):
    clone_url: str
    default_branch: str
    validation_commands: list[str] = Field(default_factory=list)
    allowed_base_branches: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    bootstrap_enabled: bool = True
    python_binary: str = "python3"
    venv_dir: str = ".venv"
    install_dev_command: str | None = None


class Settings(BaseModel):
    plane: PlaneSettings
    git: GitSettings
    kodo: KodoSettings
    repos: dict[str, RepoSettings]
    report_root: Path = Path("tools/report/kodo_plane")

    def plane_token(self) -> str:
        return os.environ[self.plane.api_token_env]

    def git_token(self) -> str | None:
        if self.git.token_env is None:
            return None
        return os.environ.get(self.git.token_env)


def load_settings(path: str | Path) -> Settings:
    raw = yaml.safe_load(Path(path).read_text())
    return Settings.model_validate(raw)
