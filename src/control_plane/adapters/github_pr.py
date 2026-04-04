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

    def list_open_prs(self, owner: str, repo: str) -> list[dict]:
        resp = httpx.get(
            f"{self._API}/repos/{owner}/{repo}/pulls",
            headers=self._headers,
            params={"state": "open", "per_page": 100},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

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
