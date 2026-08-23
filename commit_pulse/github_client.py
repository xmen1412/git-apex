from __future__ import annotations

import logging
from typing import Any

import httpx

from .config import Settings
from .models import FileChange

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"


def fetch_commit_diff(repo: str, sha: str, token: str) -> list[FileChange]:
    """Fetch per-file patch/additions/deletions from GitHub commit detail."""
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = f"{GITHUB_API}/repos/{repo}/commits/{sha}"
    with httpx.Client(timeout=30.0) as client:
        resp = client.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    changes: list[FileChange] = []
    for f in data.get("files", []):
        changes.append(FileChange(
            path=f.get("filename", ""),
            change_type=f.get("status", "modified"),
            additions=f.get("additions", 0),
            deletions=f.get("deletions", 0),
            patch=f.get("patch"),
        ))
    return changes


def fetch_recent_commits(repo: str, token: str, limit: int = 50) -> list[dict[str, Any]]:
    """Fetch recent commits from GitHub REST API for backfill."""
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = f"{GITHUB_API}/repos/{repo}/commits"
    with httpx.Client(timeout=30.0) as client:
        resp = client.get(url, headers=headers, params={"per_page": min(limit, 100)})
        resp.raise_for_status()
        return resp.json()


def _auth_headers(token: str) -> dict[str, str]:
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def list_user_repos(token: str, affiliation: str = "owner,collaborator,organization_member") -> list[dict[str, Any]]:
    """List every repo the token can see (paginated, 100/page)."""
    repos: list[dict[str, Any]] = []
    page = 1
    with httpx.Client(timeout=30.0) as client:
        while True:
            resp = client.get(
                f"{GITHUB_API}/user/repos",
                headers=_auth_headers(token),
                params={"per_page": 100, "page": page, "affiliation": affiliation},
            )
            resp.raise_for_status()
            batch = resp.json()
            repos.extend(batch)
            if len(batch) < 100:
                break
            page += 1
    return repos


def list_repo_hooks(repo: str, token: str) -> list[dict[str, Any]]:
    """List webhooks already configured on a repo. Raises on 403/404 (no admin access)."""
    with httpx.Client(timeout=30.0) as client:
        resp = client.get(f"{GITHUB_API}/repos/{repo}/hooks", headers=_auth_headers(token))
        resp.raise_for_status()
        return resp.json()


def create_repo_hook(repo: str, token: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Register a new webhook on a repo."""
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(f"{GITHUB_API}/repos/{repo}/hooks", headers=_auth_headers(token), json=payload)
        resp.raise_for_status()
        return resp.json()
