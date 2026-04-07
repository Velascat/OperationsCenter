from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import httpx

_logger = logging.getLogger(__name__)

_GH_RATE_LIMIT_MAX_RETRIES = 3
_GH_RATE_LIMIT_WARN_THRESHOLD = 10
_GH_RATE_LIMIT_DEFAULT_BACKOFF_SECONDS = 60


class GitHubPRClient:
    """Thin wrapper around the GitHub REST API for PR create + merge."""

    _API = "https://api.github.com"

    def __init__(self, token: str) -> None:
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """HTTP request with rate-limit retry and low-quota warning.

        On 429 responses, reads the ``Retry-After`` header (defaulting to
        ``_GH_RATE_LIMIT_DEFAULT_BACKOFF_SECONDS``) and retries up to
        ``_GH_RATE_LIMIT_MAX_RETRIES`` times before giving up and returning
        the last response.  Logs a warning whenever ``X-RateLimit-Remaining``
        drops below ``_GH_RATE_LIMIT_WARN_THRESHOLD`` so operators have
        advance notice before hard throttling kicks in.
        """
        kwargs.setdefault("timeout", 30)
        resp: httpx.Response | None = None
        for attempt in range(_GH_RATE_LIMIT_MAX_RETRIES + 1):
            resp = httpx.request(method, url, headers=self._headers, **kwargs)
            remaining_raw = resp.headers.get("X-RateLimit-Remaining")
            if remaining_raw is not None:
                try:
                    if int(remaining_raw) < _GH_RATE_LIMIT_WARN_THRESHOLD:
                        _logger.warning(json.dumps({
                            "event": "github_rate_limit_low",
                            "remaining": int(remaining_raw),
                            "reset_epoch": resp.headers.get("X-RateLimit-Reset"),
                        }))
                except (ValueError, TypeError):
                    pass
            if resp.status_code != 429 or attempt >= _GH_RATE_LIMIT_MAX_RETRIES:
                return resp
            retry_after_raw = resp.headers.get("Retry-After", "")
            try:
                retry_after = int(retry_after_raw)
            except (ValueError, TypeError):
                retry_after = _GH_RATE_LIMIT_DEFAULT_BACKOFF_SECONDS
            _logger.warning(json.dumps({
                "event": "github_rate_limited",
                "attempt": attempt + 1,
                "retry_after_seconds": retry_after,
                "url": url,
            }))
            time.sleep(retry_after)
        assert resp is not None
        return resp

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
        resp = self._request(
            "POST",
            f"{self._API}/repos/{owner}/{repo}/pulls",
            json={"title": title, "head": head, "base": base, "body": body},
        )
        resp.raise_for_status()
        return resp.json()

    def get_pr(self, owner: str, repo: str, pr_number: int) -> dict:
        """Fetch a single pull request by number from the GitHub REST API.

        Returns the full PR resource as a dict (see GitHub docs for schema).
        Raises ``httpx.HTTPStatusError`` on non-2xx responses.
        """
        resp = self._request("GET", f"{self._API}/repos/{owner}/{repo}/pulls/{pr_number}")
        resp.raise_for_status()
        return resp.json()

    def merge_pr(self, owner: str, repo: str, pr_number: int, *, merge_method: str = "squash") -> dict:
        resp = self._request(
            "PUT",
            f"{self._API}/repos/{owner}/{repo}/pulls/{pr_number}/merge",
            json={"merge_method": merge_method},
        )
        resp.raise_for_status()
        return resp.json()

    def delete_branch(self, owner: str, repo: str, branch: str) -> None:
        resp = self._request(
            "DELETE",
            f"{self._API}/repos/{owner}/{repo}/git/refs/heads/{branch}",
        )
        resp.raise_for_status()

    def get_pr_reactions(self, owner: str, repo: str, pr_number: int) -> list[dict]:
        resp = self._request(
            "GET",
            f"{self._API}/repos/{owner}/{repo}/issues/{pr_number}/reactions",
        )
        resp.raise_for_status()
        return resp.json()

    def list_pr_comments(self, owner: str, repo: str, pr_number: int) -> list[dict]:
        resp = self._request(
            "GET",
            f"{self._API}/repos/{owner}/{repo}/issues/{pr_number}/comments",
        )
        resp.raise_for_status()
        return resp.json()

    def list_pr_review_comments(self, owner: str, repo: str, pr_number: int) -> list[dict]:
        """Fetch inline/line-level review comments from the PR review comments API."""
        resp = self._request(
            "GET",
            f"{self._API}/repos/{owner}/{repo}/pulls/{pr_number}/comments",
        )
        resp.raise_for_status()
        return resp.json()

    def get_comment_reactions(self, owner: str, repo: str, comment_id: int) -> list[dict]:
        resp = self._request(
            "GET",
            f"{self._API}/repos/{owner}/{repo}/issues/comments/{comment_id}/reactions",
        )
        resp.raise_for_status()
        return resp.json()

    def post_comment(self, owner: str, repo: str, pr_number: int, body: str) -> dict:
        resp = self._request(
            "POST",
            f"{self._API}/repos/{owner}/{repo}/issues/{pr_number}/comments",
            json={"body": body},
        )
        resp.raise_for_status()
        return resp.json()

    def get_check_runs(self, owner: str, repo: str, ref: str) -> list[dict]:
        """Return all check-runs for a given commit SHA or ref."""
        resp = self._request(
            "GET",
            f"{self._API}/repos/{owner}/{repo}/commits/{ref}/check-runs",
            params={"per_page": 100},
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
        resp = self._request(
            "GET",
            f"{self._API}/repos/{owner}/{repo}/pulls",
            params={"state": "open", "per_page": 100},
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
            resp = self._request(
                "GET",
                f"{self._API}/repos/{owner}/{repo}/pulls/{pr_number}/files",
                params={"per_page": 100},
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
        resp = self._request(
            "PATCH",
            f"{self._API}/repos/{owner}/{repo}/pulls/{pr_number}",
            json={"state": "closed"},
        )
        resp.raise_for_status()
        return resp.json()

    def list_pr_reviews(self, owner: str, repo: str, pr_number: int) -> list[dict]:
        """Return all submitted reviews for a PR."""
        resp = self._request(
            "GET",
            f"{self._API}/repos/{owner}/{repo}/pulls/{pr_number}/reviews",
            params={"per_page": 100},
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

    def get_branch_head(self, owner: str, repo: str, branch: str) -> str | None:
        """Return the HEAD commit SHA of *branch*, or None on failure."""
        try:
            resp = self._request(
                "GET",
                f"{self._API}/repos/{owner}/{repo}/branches/{branch}",
            )
            resp.raise_for_status()
            return str((resp.json().get("commit") or {}).get("sha", "")) or None
        except Exception:
            return None

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
