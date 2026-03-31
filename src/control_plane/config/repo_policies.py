from __future__ import annotations

import os
import re
from pathlib import Path

import httpx
from pydantic import BaseModel, Field

from control_plane.config.settings import Settings


class RepoPolicy(BaseModel):
    repo_key: str
    propose_enabled: bool = True


class RepoPolicyDocument(BaseModel):
    policies: list[RepoPolicy] = Field(default_factory=list)


class RepoDescriptor(BaseModel):
    repo_key: str
    clone_url: str
    default_branch: str
    allowed_base_branches: list[str] = Field(default_factory=list)
    branch_options: list[str] = Field(default_factory=list)
    propose_enabled: bool = True
    branch_source: str = "config"
    configured: bool = True
    owner: str | None = None


def default_repo_policy_path(config_path: str | Path | None = None) -> Path:
    if config_path is None:
        raw = os.environ.get("CONTROL_PLANE_CONFIG")
        config_path = Path(raw) if raw else Path("config/control_plane.local.yaml")
    path = Path(config_path)
    return path.parent / "control_plane.repo_policies.local.json"


class RepoPolicyStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else default_repo_policy_path()

    def load(self) -> RepoPolicyDocument:
        if not self.path.exists():
            return RepoPolicyDocument()
        return RepoPolicyDocument.model_validate_json(self.path.read_text())

    def save(self, document: RepoPolicyDocument) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(document.model_dump_json(indent=2) + "\n")

    def policy_map(self) -> dict[str, RepoPolicy]:
        return {item.repo_key: item for item in self.load().policies}

    def describe_repos(self, settings: Settings) -> list[RepoDescriptor]:
        policies = self.policy_map()
        rows: dict[str, RepoDescriptor] = {}
        for repo_key, repo_cfg in settings.repos.items():
            default_branch = str(getattr(repo_cfg, "default_branch", "")).strip()
            clone_url = str(getattr(repo_cfg, "clone_url", "")).strip()
            allowed_base_branches = list(getattr(repo_cfg, "allowed_base_branches", []) or [])
            repo_slug = github_repo_slug(clone_url)
            owner = repo_slug.split("/", 1)[0] if repo_slug else None
            branch_options, branch_source = resolve_branch_options(
                clone_url=clone_url,
                default_branch=default_branch,
                allowed_base_branches=allowed_base_branches,
                github_token=settings.git_token(),
            )
            rows[repo_key] = RepoDescriptor(
                repo_key=repo_key,
                clone_url=clone_url,
                default_branch=default_branch,
                allowed_base_branches=allowed_base_branches,
                branch_options=branch_options,
                propose_enabled=policies.get(repo_key, RepoPolicy(repo_key=repo_key)).propose_enabled,
                branch_source=branch_source,
                configured=True,
                owner=owner,
            )
        for owner in github_owners_from_settings(settings):
            try:
                discovered_repos = fetch_github_repositories(owner, github_token=settings.git_token())
            except Exception:
                discovered_repos = []
            for repo in discovered_repos:
                repo_key = repo["name"]
                if repo_key in rows:
                    continue
                branch_options, branch_source = resolve_branch_options(
                    clone_url=repo["clone_url"],
                    default_branch=repo["default_branch"],
                    allowed_base_branches=[repo["default_branch"]],
                    github_token=settings.git_token(),
                )
                rows[repo_key] = RepoDescriptor(
                    repo_key=repo_key,
                    clone_url=repo["clone_url"],
                    default_branch=repo["default_branch"],
                    allowed_base_branches=[repo["default_branch"]],
                    branch_options=branch_options,
                    propose_enabled=False,
                    branch_source=branch_source,
                    configured=False,
                    owner=owner,
                )
        return sorted(rows.values(), key=lambda row: (not row.configured, row.repo_key.lower()))

    def enabled_propose_repo_keys(self, settings: Settings) -> list[str]:
        enabled = [row.repo_key for row in self.describe_repos(settings) if row.propose_enabled]
        if enabled:
            return enabled
        return []


def configured_branch_options(default_branch: str, allowed_base_branches: list[str]) -> list[str]:
    branch_options: list[str] = []
    for value in [default_branch, *allowed_base_branches]:
        normalized = str(value).strip()
        if normalized and normalized not in branch_options:
            branch_options.append(normalized)
    return branch_options


def github_repo_slug(clone_url: str) -> str | None:
    value = clone_url.strip()
    patterns = [
        r"^git@github\.com:(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$",
        r"^https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$",
        r"^ssh://git@github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$",
    ]
    for pattern in patterns:
        match = re.match(pattern, value)
        if match:
            return f"{match.group('owner')}/{match.group('repo')}"
    return None


def github_owners_from_settings(settings: Settings) -> list[str]:
    owners: list[str] = []
    for repo_cfg in settings.repos.values():
        slug = github_repo_slug(str(getattr(repo_cfg, "clone_url", "")).strip())
        if slug is None:
            continue
        owner = slug.split("/", 1)[0]
        if owner not in owners:
            owners.append(owner)
    return owners


def fetch_github_repositories(owner: str, github_token: str | None = None) -> list[dict[str, str]]:
    headers = {"Accept": "application/vnd.github+json"}
    if github_token:
        headers["Authorization"] = f"Bearer {github_token}"
    urls = []
    if github_token:
        urls.append("https://api.github.com/user/repos")
    urls.extend(
        [
        f"https://api.github.com/users/{owner}/repos",
        f"https://api.github.com/orgs/{owner}/repos",
        ]
    )
    payload: list[dict[str, str]] = []
    for url in urls:
        response = httpx.get(url, headers=headers, params={"per_page": 100, "sort": "updated"}, timeout=20.0)
        if response.status_code == 404:
            continue
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, list):
            continue
        if url.endswith("/user/repos"):
            payload = [item for item in data if isinstance(item, dict) and str(item.get("owner", {}).get("login", "")).strip() == owner]
            if payload:
                break
            continue
        payload = data
        break

    repos: list[dict[str, str]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        default_branch = str(item.get("default_branch", "")).strip()
        clone_url = str(item.get("ssh_url") or item.get("clone_url") or "").strip()
        if not name or not clone_url:
            continue
        repos.append(
            {
                "name": name,
                "default_branch": default_branch or "main",
                "clone_url": clone_url,
            }
        )
    return repos


def fetch_github_branches(repo_slug: str, github_token: str | None = None) -> list[str]:
    headers = {"Accept": "application/vnd.github+json"}
    if github_token:
        headers["Authorization"] = f"Bearer {github_token}"
    response = httpx.get(
        f"https://api.github.com/repos/{repo_slug}/branches",
        headers=headers,
        params={"per_page": 100},
        timeout=20.0,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        return []
    names: list[str] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if name and name not in names:
            names.append(name)
    return names


def resolve_branch_options(
    *,
    clone_url: str,
    default_branch: str,
    allowed_base_branches: list[str],
    github_token: str | None = None,
) -> tuple[list[str], str]:
    configured = configured_branch_options(default_branch, allowed_base_branches)
    repo_slug = github_repo_slug(clone_url)
    if repo_slug is None:
        return configured, "config"
    try:
        github_branches = fetch_github_branches(repo_slug, github_token=github_token)
    except Exception:
        return configured, "config"

    merged = list(github_branches)
    for branch in configured:
        if branch not in merged:
            merged.append(branch)
    return merged, "github"
