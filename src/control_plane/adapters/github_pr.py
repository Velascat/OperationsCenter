from __future__ import annotations

import re

import httpx


class GitHubPRClient:
    """Thin wrapper around the GitHub REST API for PR create + merge."""

    _API = "https://api.github.com"

    def __init__(self, token: str) -> None:
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    @staticmethod
    def owner_repo_from_clone_url(clone_url: str) -> tuple[str, str]:
        """Parse owner/repo from an https or ssh clone URL."""
        # ssh: git@github.com:owner/repo.git
        # https: https://github.com/owner/repo.git
        m = re.search(r"[:/]([^/]+)/([^/]+?)(?:\.git)?$", clone_url)
        if not m:
            raise ValueError(f"Cannot parse owner/repo from clone URL: {clone_url!r}")
        return m.group(1), m.group(2)

    def create_pr(
        self,
        owner: str,
        repo: str,
        *,
        head: str,
        base: str,
        title: str,
        body: str = "",
    ) -> dict:
        resp = httpx.post(
            f"{self._API}/repos/{owner}/{repo}/pulls",
            headers=self._headers,
            json={"title": title, "head": head, "base": base, "body": body},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def get_pr(self, owner: str, repo: str, pr_number: int) -> dict:
        """Fetch a single pull request by number from the GitHub REST API.

        Returns the full PR resource as a dict (see GitHub docs for schema).
        Raises ``httpx.HTTPStatusError`` on non-2xx responses.
        """
        resp = httpx.get(
            f"{self._API}/repos/{owner}/{repo}/pulls/{pr_number}",
            headers=self._headers,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def merge_pr(self, owner: str, repo: str, pr_number: int, *, merge_method: str = "squash") -> dict:
        resp = httpx.put(
            f"{self._API}/repos/{owner}/{repo}/pulls/{pr_number}/merge",
            headers=self._headers,
            json={"merge_method": merge_method},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def delete_branch(self, owner: str, repo: str, branch: str) -> None:
        resp = httpx.delete(
            f"{self._API}/repos/{owner}/{repo}/git/refs/heads/{branch}",
            headers=self._headers,
            timeout=30,
        )
        resp.raise_for_status()

    def get_pr_reactions(self, owner: str, repo: str, pr_number: int) -> list[dict]:
        resp = httpx.get(
            f"{self._API}/repos/{owner}/{repo}/issues/{pr_number}/reactions",
            headers=self._headers,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def list_pr_comments(self, owner: str, repo: str, pr_number: int) -> list[dict]:
        resp = httpx.get(
            f"{self._API}/repos/{owner}/{repo}/issues/{pr_number}/comments",
            headers=self._headers,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def list_pr_review_comments(self, owner: str, repo: str, pr_number: int) -> list[dict]:
        """Fetch inline/line-level review comments from the PR review comments API."""
        resp = httpx.get(
            f"{self._API}/repos/{owner}/{repo}/pulls/{pr_number}/comments",
            headers=self._headers,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def get_comment_reactions(self, owner: str, repo: str, comment_id: int) -> list[dict]:
        resp = httpx.get(
            f"{self._API}/repos/{owner}/{repo}/issues/comments/{comment_id}/reactions",
            headers=self._headers,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def post_comment(self, owner: str, repo: str, pr_number: int, body: str) -> dict:
        resp = httpx.post(
            f"{self._API}/repos/{owner}/{repo}/issues/{pr_number}/comments",
            headers=self._headers,
            json={"body": body},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def get_check_runs(self, owner: str, repo: str, ref: str) -> list[dict]:
        """Return all check-runs for a given commit SHA or ref."""
        resp = httpx.get(
            f"{self._API}/repos/{owner}/{repo}/commits/{ref}/check-runs",
            headers=self._headers,
            params={"per_page": 100},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("check_runs", [])

    def get_failed_checks(
        self, owner: str, repo: str, pr_number: int, *, pr_data: dict | None = None
    ) -> list[str]:
        """Return human-readable descriptions of failing CI checks for the PR's head commit."""
        if pr_data is None:
            pr_data = self.get_pr(owner, repo, pr_number)
        head_sha = (pr_data.get("head") or {}).get("sha", "")
        if not head_sha:
            return []
        try:
            check_runs = self.get_check_runs(owner, repo, head_sha)
        except Exception:
            return []
        failed = []
        for cr in check_runs:
            if cr.get("conclusion") in ("failure", "timed_out", "cancelled"):
                name = cr.get("name", "unknown")
                summary = (cr.get("output") or {}).get("title") or cr.get("conclusion", "failed")
                failed.append(f"{name}: {summary}")
        return failed

    def list_open_prs(self, owner: str, repo: str) -> list[dict]:
        resp = httpx.get(
            f"{self._API}/repos/{owner}/{repo}/pulls",
            headers=self._headers,
            params={"state": "open", "per_page": 100},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def list_pr_files(self, owner: str, repo: str, pr_number: int) -> list[str]:
        """Return the list of filenames changed in a pull request.

        Uses ``GET /repos/{owner}/{repo}/pulls/{pull_number}/files``.
        Returns an empty list on any error (best-effort — callers must not rely
        on completeness for correctness).
        """
        try:
            resp = httpx.get(
                f"{self._API}/repos/{owner}/{repo}/pulls/{pr_number}/files",
                headers=self._headers,
                params={"per_page": 100},
                timeout=30,
            )
            resp.raise_for_status()
            return [item["filename"] for item in resp.json() if isinstance(item, dict) and "filename" in item]
        except Exception:
            return []

    def get_mergeable(self, owner: str, repo: str, pr_number: int) -> bool | None:
        """Return the GitHub ``mergeable`` flag, or ``None`` while GitHub is computing it."""
        try:
            pr = self.get_pr(owner, repo, pr_number)
            val = pr.get("mergeable")
            if val is None:
                return None
            return bool(val)
        except Exception:
            return None

    def close_pr(self, owner: str, repo: str, pr_number: int) -> dict:
        """Close a pull request without merging it."""
        resp = httpx.patch(
            f"{self._API}/repos/{owner}/{repo}/pulls/{pr_number}",
            headers=self._headers,
            json={"state": "closed"},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def list_pr_reviews(self, owner: str, repo: str, pr_number: int) -> list[dict]:
        """Return all submitted reviews for a PR."""
        resp = httpx.get(
            f"{self._API}/repos/{owner}/{repo}/pulls/{pr_number}/reviews",
            headers=self._headers,
            params={"per_page": 100},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def pr_has_changes_requested(self, owner: str, repo: str, pr_number: int) -> bool:
        """Return True if any reviewer submitted CHANGES_REQUESTED."""
        try:
            reviews = self.list_pr_reviews(owner, repo, pr_number)
            return any(r.get("state") == "CHANGES_REQUESTED" for r in reviews)
        except Exception:
            return False

    @staticmethod
    def has_thumbs_up(reactions: list[dict]) -> bool:
        return any(r["content"] == "+1" for r in reactions)

    def create_and_merge(
        self,
        owner: str,
        repo: str,
        *,
        head: str,
        base: str,
        title: str,
        body: str = "",
        merge_method: str = "squash",
    ) -> str:
        """Create a PR, merge it, then delete the head branch. Returns the PR html_url."""
        pr = self.create_pr(owner, repo, head=head, base=base, title=title, body=body)
        pr_number = pr["number"]
        pr_url = pr["html_url"]
        self.merge_pr(owner, repo, pr_number, merge_method=merge_method)
        self.delete_branch(owner, repo, head)
        return pr_url
