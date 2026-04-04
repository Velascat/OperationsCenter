from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import httpx

from control_plane.adapters.plane import PlaneClient
from control_plane.adapters.reporting import Reporter
from control_plane.config import Settings, load_settings
from control_plane.entrypoints.setup.main import load_env_exports
from control_plane.entrypoints.setup.providers import PROVIDER_SPECS, detect_all_provider_statuses

GITHUB_ACCEPT = "application/vnd.github+json"


@dataclass
class DependencyStatus:
    key: str
    label: str
    kind: str
    installed_version: str | None
    pinned_version: str | None
    upstream_latest: str | None
    healthy: bool
    notes: list[str]


def normalize_version(value: str | None) -> str | None:
    if not value:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    match = re.search(r"\b\d+\.\d+\.\d+(?:[-+][A-Za-z0-9._-]+)?\b", stripped)
    if match:
        return match.group(0)
    if re.fullmatch(r"[A-Fa-f0-9]{7,40}", stripped):
        return stripped.lower()
    return stripped


def fetch_github_latest_release(owner: str, repo: str) -> str | None:
    url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
    response = httpx.get(url, headers={"Accept": GITHUB_ACCEPT}, timeout=20.0)
    if response.status_code != 200:
        return None
    payload = response.json()
    if isinstance(payload, dict):
        return normalize_version(str(payload.get("tag_name") or "").strip())
    return None


def fetch_npm_latest(package_name: str) -> str | None:
    url = f"https://registry.npmjs.org/{package_name}/latest"
    response = httpx.get(url, timeout=20.0)
    if response.status_code != 200:
        return None
    payload = response.json()
    if isinstance(payload, dict):
        return normalize_version(str(payload.get("version") or "").strip())
    return None


def plane_latest_from_env(env: dict[str, str]) -> tuple[str | None, str | None]:
    pinned = normalize_version(env.get("CONTROL_PLANE_PLANE_VERSION"))
    setup_url = env.get("CONTROL_PLANE_PLANE_SETUP_URL") or None
    return pinned, setup_url


def current_plane_health(settings: Settings) -> bool:
    try:
        response = httpx.get(settings.plane.base_url, timeout=10.0)
    except httpx.HTTPError:
        return False
    return response.status_code < 500


def collect_dependency_statuses(settings: Settings, env: dict[str, str]) -> list[DependencyStatus]:
    statuses: list[DependencyStatus] = []

    plane_pinned, _ = plane_latest_from_env(env)
    plane_latest = fetch_github_latest_release("makeplane", "plane")
    plane_notes: list[str] = []
    plane_healthy = current_plane_health(settings)
    if not plane_healthy:
        plane_notes.append("Plane base URL is not reachable.")
    if plane_pinned and plane_latest and plane_pinned != plane_latest:
        plane_notes.append(f"Pinned release {plane_pinned} differs from upstream latest {plane_latest}.")
    statuses.append(
        DependencyStatus(
            key="plane",
            label="Plane",
            kind="service",
            installed_version=None,
            pinned_version=plane_pinned,
            upstream_latest=plane_latest,
            healthy=plane_healthy,
            notes=plane_notes,
        )
    )

    try:
        proc = subprocess.run(["kodo", "--version"], check=False, capture_output=True, text=True)
        kodo_version_raw = (proc.stdout or proc.stderr).strip() if proc.returncode == 0 else ""
    except Exception:
        kodo_version_raw = ""
    kodo_installed = bool(kodo_version_raw)
    kodo_installed_version = normalize_version(kodo_version_raw)
    kodo_pinned = normalize_version(env.get("CONTROL_PLANE_KODO_INSTALL_REF"))
    kodo_latest = fetch_github_latest_release("ikamensh", "kodo")
    kodo_notes: list[str] = []
    if not kodo_installed:
        kodo_notes.append("Kodo is not installed or not on PATH.")
    if kodo_pinned and kodo_installed_version and kodo_pinned != kodo_installed_version:
        kodo_notes.append(f"Installed version {kodo_installed_version} does not match pinned ref {kodo_pinned}.")
    if kodo_pinned and kodo_latest and kodo_pinned != kodo_latest:
        kodo_notes.append(f"Pinned ref {kodo_pinned} differs from upstream latest {kodo_latest}.")
    statuses.append(
        DependencyStatus(
            key="kodo",
            label="Kodo",
            kind="cli",
            installed_version=kodo_installed_version,
            pinned_version=kodo_pinned,
            upstream_latest=kodo_latest,
            healthy=kodo_installed,
            notes=kodo_notes,
        )
    )

    provider_statuses = {status.key: status for status in detect_all_provider_statuses()}
    provider_pin_env = {
        "claude": "CONTROL_PLANE_PROVIDER_CLAUDE_VERSION",
        "codex": "CONTROL_PLANE_PROVIDER_CODEX_VERSION",
        "gemini": "CONTROL_PLANE_PROVIDER_GEMINI_VERSION",
    }
    for key in ["claude", "codex", "gemini"]:
        provider = provider_statuses[key]
        pinned = normalize_version(env.get(provider_pin_env[key]))
        npm_pkg = PROVIDER_SPECS[key].npm_package
        latest = fetch_npm_latest(npm_pkg) if npm_pkg else None
        notes: list[str] = []
        if not provider.installed:
            notes.append("Provider CLI is not installed.")
        if provider.installed and not provider.authenticated and key in {"claude", "codex"}:
            notes.append("Provider CLI is installed but not logged in.")
        installed_version = normalize_version(provider.version)
        if pinned and installed_version and pinned != installed_version:
            notes.append(f"Installed version {installed_version} does not match pinned version {pinned}.")
        if pinned and latest and pinned != latest:
            notes.append(f"Pinned version {pinned} differs from upstream latest {latest}.")
        statuses.append(
            DependencyStatus(
                key=key,
                label=provider.label,
                kind="provider",
                installed_version=installed_version,
                pinned_version=pinned,
                upstream_latest=latest,
                healthy=provider.installed,
                notes=notes,
            )
        )

    return statuses


def actionable_statuses(statuses: list[DependencyStatus]) -> list[DependencyStatus]:
    return [status for status in statuses if status.notes]


def dependency_task_description(settings: Settings, status: DependencyStatus) -> str:
    repo_key = next(iter(settings.repos.keys()))
    repo_cfg = settings.repos[repo_key]
    lines = [
        "## Execution",
        f"repo: {repo_key}",
        f"base_branch: {repo_cfg.default_branch}",
        "mode: goal",
        "",
        "## Goal",
        f"Investigate and resolve dependency maintenance issue for {status.label}.",
        "",
        "## Constraints",
        f"- dependency: {status.key}",
        f"- pinned_version: {status.pinned_version or 'none'}",
        f"- installed_version: {status.installed_version or 'none'}",
        f"- upstream_latest: {status.upstream_latest or 'unknown'}",
    ]
    lines.extend(f"- note: {note}" for note in status.notes)
    return "\n".join(lines)


def ensure_follow_up_task(client: PlaneClient, settings: Settings, status: DependencyStatus) -> str | None:
    title = f"Dependency maintenance: {status.label}"
    for issue in client.list_issues():
        if str(issue.get("name", "")).strip() == title and issue_status_name(issue) not in {"Done", "Cancelled"}:
            return None
    created = client.create_issue(
        name=title,
        description=dependency_task_description(settings, status),
        state="Ready for AI",
        label_names=["task-kind: improve", "source: dependency-check"],
    )
    return str(created.get("id"))


def issue_status_name(issue: dict[str, Any]) -> str:
    state = issue.get("state")
    if isinstance(state, dict):
        return str(state.get("name", ""))
    return str(state or "")


def write_dependency_report(run_dir: Path, statuses: list[DependencyStatus], created_task_ids: list[str]) -> list[str]:
    json_path = run_dir / "dependency_report.json"
    md_path = run_dir / "dependency_summary.md"
    json_path.write_text(
        json.dumps(
            {
                "statuses": [asdict(status) for status in statuses],
                "created_task_ids": created_task_ids,
            },
            indent=2,
        )
    )
    lines = ["# Dependency Check", "", "## Statuses"]
    for status in statuses:
        lines.append(f"- {status.label}: healthy={status.healthy} pinned={status.pinned_version or 'none'} installed={status.installed_version or 'none'} upstream={status.upstream_latest or 'unknown'}")
        for note in status.notes:
            lines.append(f"  - {note}")
    lines.extend(["", "## Created Tasks"])
    lines.extend([f"- {task_id}" for task_id in created_task_ids] or ["- none"])
    md_path.write_text("\n".join(lines))
    return [str(json_path), str(md_path)]


def main() -> None:
    parser = argparse.ArgumentParser(description="Check pinned tool versions against installed state and upstream latest versions")
    parser.add_argument("--config", required=True)
    parser.add_argument("--create-plane-tasks", action="store_true")
    args = parser.parse_args()

    settings = load_settings(args.config)
    env_path = Path(os.environ.get("CONTROL_PLANE_ENV_FILE", ".env.control-plane.local"))
    env = load_env_exports(env_path)
    reporter = Reporter(settings.report_root)
    run_id = uuid.uuid4().hex[:12]
    run_dir = reporter.create_run_dir("dependency-check", run_id)
    reporter.write_request_context(run_dir, "dependency-check", run_id, phase="dependency_check")

    statuses = collect_dependency_statuses(settings, env)
    created_task_ids: list[str] = []

    if args.create_plane_tasks:
        client = PlaneClient(
            base_url=settings.plane.base_url,
            api_token=settings.plane_token(),
            workspace_slug=settings.plane.workspace_slug,
            project_id=settings.plane.project_id,
        )
        try:
            for status in actionable_statuses(statuses):
                task_id = ensure_follow_up_task(client, settings, status)
                if task_id:
                    created_task_ids.append(task_id)
        finally:
            client.close()

    artifacts = write_dependency_report(run_dir, statuses, created_task_ids)
    print(
        json.dumps(
            {
                "run_id": run_id,
                "artifacts": artifacts,
                "statuses": [asdict(status) for status in statuses],
                "created_task_ids": created_task_ids,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
