from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from control_plane.adapters.plane import PlaneClient
from control_plane.application.task_parser import TaskParser
from control_plane.config import RepoPolicy, RepoPolicyDocument, RepoPolicyStore, load_settings

app = FastAPI(title="control-plane")


def resolve_config_path(explicit: str | None = None) -> Path:
    raw = explicit or os.environ.get("CONTROL_PLANE_CONFIG") or "config/control_plane.local.yaml"
    return Path(raw)


def load_runtime_config(config_path: str | None = None):
    path = resolve_config_path(config_path)
    os.environ.setdefault("CONTROL_PLANE_CONFIG", str(path))
    settings = load_settings(path)
    store = RepoPolicyStore()
    return path, settings, store


def build_task_description(
    *,
    repo_key: str,
    base_branch: str,
    goal: str,
    constraints: str | None,
) -> str:
    lines = [
        "## Execution",
        f"repo: {repo_key}",
        f"base_branch: {base_branch}",
        "mode: goal",
        "",
        "## Goal",
        goal.strip(),
    ]
    if constraints and constraints.strip():
        lines.extend(["", "## Constraints", constraints.strip()])
    return "\n".join(lines).strip()


def validate_repo_branch(settings, repo_key: str, base_branch: str) -> None:
    if repo_key not in settings.repos:
        raise HTTPException(status_code=400, detail=f"Unknown repo: {repo_key}")
    repo_cfg = settings.repos[repo_key]
    allowed = [item.strip() for item in repo_cfg.allowed_base_branches if item.strip()]
    if allowed and base_branch not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Branch '{base_branch}' is not allowed for repo '{repo_key}'. Allowed: {', '.join(allowed)}",
        )


def import_repo_into_config(*, config_path: Path, repo: dict[str, Any]) -> None:
    raw = yaml.safe_load(config_path.read_text()) or {}
    repos = raw.setdefault("repos", {})
    branch_options = [str(item).strip() for item in repo.get("branch_options", []) if str(item).strip()]
    if not branch_options:
        branch_options = [str(repo["default_branch"]).strip()]
    existing = repos.get(repo["repo_key"], {})
    repos[repo["repo_key"]] = {
        "clone_url": repo["clone_url"],
        "default_branch": existing.get("default_branch") or repo["default_branch"],
        "validation_commands": existing.get("validation_commands", []),
        "allowed_base_branches": branch_options,
        "bootstrap_enabled": existing.get("bootstrap_enabled", False),
        "python_binary": existing.get("python_binary", "python3"),
        "venv_dir": existing.get("venv_dir", ".venv"),
        "install_dev_command": existing.get("install_dev_command"),
    }
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False, default_flow_style=False))


class ParseRequest(BaseModel):
    config_path: str | None = None
    description: str


class RepoPolicyUpdateRequest(BaseModel):
    config_path: str | None = None
    policies: list[RepoPolicy]


class PlaneTaskCreateRequest(BaseModel):
    config_path: str | None = None
    name: str
    repo_key: str
    base_branch: str
    goal: str
    constraints: str | None = None
    state: str = "Ready for AI"
    label_names: list[str] = Field(default_factory=lambda: ["task-kind: goal"])


class RepoImportRequest(BaseModel):
    config_path: str | None = None
    repo_key: str


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/repos")
def list_repos(config_path: str | None = None) -> dict[str, Any]:
    resolved_config_path, settings, store = load_runtime_config(config_path)
    return {
        "config_path": str(resolved_config_path),
        "policy_path": str(store.path),
        "repos": [row.model_dump() for row in store.describe_repos(settings)],
    }


@app.get("/repo-policies")
def get_repo_policies(config_path: str | None = None) -> dict[str, Any]:
    resolved_config_path, settings, store = load_runtime_config(config_path)
    return {
        "config_path": str(resolved_config_path),
        "policy_path": str(store.path),
        "policies": [row.model_dump() for row in store.describe_repos(settings)],
    }


@app.put("/repo-policies")
def update_repo_policies(payload: RepoPolicyUpdateRequest) -> dict[str, Any]:
    resolved_config_path, settings, store = load_runtime_config(payload.config_path)
    known_repo_keys = set(settings.repos.keys())
    normalized: list[RepoPolicy] = []
    for item in payload.policies:
        if item.repo_key not in known_repo_keys:
            raise HTTPException(status_code=400, detail=f"Unknown repo in policy update: {item.repo_key}")
        normalized.append(item)
    store.save(RepoPolicyDocument(policies=normalized))
    return {
        "config_path": str(resolved_config_path),
        "policy_path": str(store.path),
        "policies": [row.model_dump() for row in store.describe_repos(settings)],
    }


@app.post("/dry-run/parse")
def dry_run_parse(payload: ParseRequest) -> dict[str, object]:
    load_runtime_config(payload.config_path)
    try:
        data = TaskParser().parse(payload.description)
        return {"parsed": data.model_dump()}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/plane/tasks")
def create_plane_task(payload: PlaneTaskCreateRequest) -> dict[str, Any]:
    _, settings, _ = load_runtime_config(payload.config_path)
    validate_repo_branch(settings, payload.repo_key, payload.base_branch)
    description = build_task_description(
        repo_key=payload.repo_key,
        base_branch=payload.base_branch,
        goal=payload.goal,
        constraints=payload.constraints,
    )
    client = PlaneClient(
        settings.plane.base_url,
        settings.plane_token(),
        settings.plane.workspace_slug,
        settings.plane.project_id,
    )
    try:
        issue = client.create_issue(
            name=payload.name,
            description=description,
            state=payload.state,
            label_names=payload.label_names,
        )
        return {"issue": issue, "description": description}
    finally:
        client.close()


@app.post("/repos/import")
def import_repo(payload: RepoImportRequest) -> dict[str, Any]:
    resolved_config_path, settings, store = load_runtime_config(payload.config_path)
    repo = next((item for item in store.describe_repos(settings) if item.repo_key == payload.repo_key), None)
    if repo is None:
        raise HTTPException(status_code=404, detail=f"Repo not found: {payload.repo_key}")
    import_repo_into_config(config_path=resolved_config_path, repo=repo.model_dump())
    updated_settings = load_settings(resolved_config_path)
    return {
        "config_path": str(resolved_config_path),
        "repo": payload.repo_key,
        "repos": [row.model_dump() for row in store.describe_repos(updated_settings)],
    }


@app.get("/plane/live-work-items")
def live_work_items(config_path: str | None = None) -> dict[str, Any]:
    _, settings, _ = load_runtime_config(config_path)
    client = PlaneClient(
        settings.plane.base_url,
        settings.plane_token(),
        settings.plane.workspace_slug,
        settings.plane.project_id,
    )
    try:
        issues = client.list_issues()
        rows: list[dict[str, Any]] = []
        for issue in issues:
            state = issue.get("state")
            state_name = state.get("name", "Unknown") if isinstance(state, dict) else str(state or "Unknown")
            rows.append(
                {
                    "id": str(issue.get("id")),
                    "name": str(issue.get("name", "Untitled")),
                    "state": state_name,
                    "updated_at": issue.get("updated_at"),
                    "created_at": issue.get("created_at"),
                }
            )
        rows.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        return {"count": len(rows), "items": rows}
    finally:
        client.close()


@app.get("/", response_class=HTMLResponse)
def repo_control_page() -> str:
    return """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Control Plane Repo Control</title>
  <style>
    :root {
      --bg: #f4efe6;
      --panel: #fff9f0;
      --ink: #1e1b16;
      --muted: #6b6257;
      --line: #d7c8b3;
      --accent: #0f766e;
      --accent-2: #c2410c;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(15,118,110,.12), transparent 28%),
        radial-gradient(circle at bottom right, rgba(194,65,12,.10), transparent 26%),
        var(--bg);
    }
    main { max-width: 1100px; margin: 0 auto; padding: 32px 20px 48px; }
    h1, h2 { margin: 0 0 12px; font-family: "Space Grotesk", "Segoe UI", sans-serif; }
    p { color: var(--muted); }
    .grid {
      display: grid;
      grid-template-columns: 1.1fr .9fr;
      gap: 20px;
      align-items: start;
    }
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 18px;
      box-shadow: 0 10px 30px rgba(30, 27, 22, 0.06);
    }
    table { width: 100%; border-collapse: collapse; }
    th, td { text-align: left; padding: 10px 8px; border-bottom: 1px solid var(--line); vertical-align: top; }
    th { font-size: 12px; text-transform: uppercase; letter-spacing: .08em; color: var(--muted); }
    .mono { font-family: "IBM Plex Mono", monospace; font-size: 12px; }
    label { display: block; margin: 12px 0 6px; font-weight: 600; }
    input[type=text], textarea, select {
      width: 100%;
      padding: 10px 12px;
      border-radius: 12px;
      border: 1px solid var(--line);
      background: #fffdf9;
      color: var(--ink);
    }
    textarea { min-height: 110px; resize: vertical; }
    .row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
    button {
      margin-top: 14px;
      border: 0;
      border-radius: 999px;
      padding: 10px 16px;
      background: var(--accent);
      color: white;
      font-weight: 700;
      cursor: pointer;
    }
    button.secondary { background: var(--accent-2); }
    .status { margin-top: 12px; font-size: 14px; color: var(--muted); white-space: pre-wrap; }
    .hint { font-size: 12px; color: var(--muted); }
    @media (max-width: 880px) {
      .grid, .row { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <main>
    <h1>Repo Control</h1>
    <p>Manage which repos the propose watcher is allowed to poll, and create Plane tasks with validated repo and branch selection.</p>
    <div class="grid">
      <section class="panel">
        <h2>Propose Scope</h2>
        <p class="hint">Unchecked configured repos are excluded from autonomous propose polling. Discovered-only repos can be imported into config directly from here.</p>
        <table>
          <thead>
            <tr><th>Repo</th><th>Default Branch</th><th>Branches</th><th>Actions</th></tr>
          </thead>
          <tbody id="repoRows"></tbody>
        </table>
        <button class="secondary" id="savePolicies">Save Repo Policies</button>
        <div class="status" id="policyStatus"></div>
      </section>
      <section class="panel">
        <h2>Create Plane Task</h2>
        <div class="row">
          <div>
            <label for="repoKey">Repo</label>
            <select id="repoKey"></select>
          </div>
          <div>
            <label for="baseBranch">Branch</label>
            <select id="baseBranch"></select>
          </div>
        </div>
        <label for="taskName">Title</label>
        <input id="taskName" type="text" placeholder="Bounded task title" />
        <label for="taskGoal">Goal</label>
        <textarea id="taskGoal" placeholder="Describe the code change to make."></textarea>
        <label for="taskConstraints">Constraints</label>
        <textarea id="taskConstraints" placeholder="- Keep scope bounded"></textarea>
        <button id="createTask">Create Plane Task</button>
        <div class="status" id="taskStatus"></div>
      </section>
    </div>
    <section class="panel" style="margin-top:20px;">
      <h2>Live Board</h2>
      <p class="hint">This view polls Plane every 5 seconds so you can watch issue state changes without manually refreshing the Plane UI.</p>
      <div class="status" id="liveStatus"></div>
      <table>
        <thead>
          <tr><th>Updated</th><th>State</th><th>Title</th><th>ID</th></tr>
        </thead>
        <tbody id="liveRows"></tbody>
      </table>
    </section>
  </main>
  <script>
    let repos = [];
    const repoRows = document.getElementById("repoRows");
    const repoKey = document.getElementById("repoKey");
    const baseBranch = document.getElementById("baseBranch");
    const policyStatus = document.getElementById("policyStatus");
    const taskStatus = document.getElementById("taskStatus");
    const liveRows = document.getElementById("liveRows");
    const liveStatus = document.getElementById("liveStatus");

    function branchOptionsFor(repo) {
      return repo.branch_options && repo.branch_options.length ? repo.branch_options : [repo.default_branch];
    }

    function renderBranchOptions() {
      const repo = repos.find((item) => item.repo_key === repoKey.value);
      const options = branchOptionsFor(repo || { default_branch: "", branch_options: [] });
      baseBranch.innerHTML = options.map((value) => `<option value="${value}">${value}</option>`).join("");
    }

    function renderRepos() {
      repoRows.innerHTML = repos.map((repo) => `
        <tr>
          <td>
            <div><strong>${repo.repo_key}</strong> ${repo.configured ? "" : "<span class='hint'>(discovered)</span>"}</div>
            <div class="mono">${repo.clone_url}</div>
            <div class="hint">configured: ${repo.configured ? "yes" : "no"}${repo.owner ? ` | owner: ${repo.owner}` : ""}</div>
          </td>
          <td class="mono">${repo.default_branch}</td>
          <td>
            <div class="mono">${branchOptionsFor(repo).join(", ")}</div>
            <div class="hint">source: ${repo.branch_source}</div>
          </td>
          <td>
            <input type="checkbox" data-repo="${repo.repo_key}" ${repo.propose_enabled ? "checked" : ""} ${repo.configured ? "" : "disabled"} />
            ${repo.configured ? "" : `<button data-import="${repo.repo_key}" style="margin-left:8px;padding:6px 10px;">Import</button>`}
          </td>
        </tr>
      `).join("");
      repoKey.innerHTML = repos
        .filter((repo) => repo.configured)
        .map((repo) => `<option value="${repo.repo_key}">${repo.repo_key}</option>`)
        .join("");
      renderBranchOptions();
    }

    async function loadRepos() {
      const response = await fetch("/repos");
      const payload = await response.json();
      repos = payload.repos;
      renderRepos();
      repoRows.querySelectorAll("button[data-import]").forEach((button) => {
        button.addEventListener("click", async () => {
          const response = await fetch("/repos/import", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ repo_key: button.dataset.import })
          });
          const payload = await response.json();
          if (!response.ok) {
            policyStatus.textContent = payload.detail || "Repo import failed.";
            return;
          }
          repos = payload.repos;
          renderRepos();
          policyStatus.textContent = `Imported ${button.dataset.import} into config.`;
      });
    });

    async function loadLiveBoard() {
      const response = await fetch("/plane/live-work-items");
      const payload = await response.json();
      if (!response.ok) {
        liveStatus.textContent = payload.detail || "Failed to load live work items.";
        return;
      }
      liveStatus.textContent = `Updated ${new Date().toLocaleTimeString()} | ${payload.count} work item(s)`;
      liveRows.innerHTML = payload.items.map((item) => `
        <tr>
          <td class="mono">${item.updated_at || "-"}</td>
          <td>${item.state}</td>
          <td>${item.name}</td>
          <td class="mono">${item.id}</td>
        </tr>
      `).join("");
    }
    }

    repoKey.addEventListener("change", renderBranchOptions);

    document.getElementById("savePolicies").addEventListener("click", async () => {
      const policies = repos.filter((repo) => repo.configured).map((repo) => ({
        repo_key: repo.repo_key,
        propose_enabled: repoRows.querySelector(`input[data-repo="${repo.repo_key}"]`).checked
      }));
      const response = await fetch("/repo-policies", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ policies })
      });
      const payload = await response.json();
      if (!response.ok) {
        policyStatus.textContent = payload.detail || "Failed to save repo policies.";
        return;
      }
      repos = payload.policies;
      renderRepos();
      policyStatus.textContent = "Repo policies saved.";
    });

    document.getElementById("createTask").addEventListener("click", async () => {
      const payload = {
        name: document.getElementById("taskName").value,
        repo_key: repoKey.value,
        base_branch: baseBranch.value,
        goal: document.getElementById("taskGoal").value,
        constraints: document.getElementById("taskConstraints").value,
        label_names: ["task-kind: goal", "source: repo-control-ui"]
      };
      const response = await fetch("/plane/tasks", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      const data = await response.json();
      taskStatus.textContent = response.ok
        ? `Created task ${data.issue.id}`
        : (data.detail || "Task creation failed.");
    });

    loadRepos().catch((error) => {
      policyStatus.textContent = String(error);
    });
    loadLiveBoard().catch((error) => {
      liveStatus.textContent = String(error);
    });
    setInterval(() => {
      loadLiveBoard().catch((error) => {
        liveStatus.textContent = String(error);
      });
    }, 5000);
  </script>
</body>
</html>
"""
