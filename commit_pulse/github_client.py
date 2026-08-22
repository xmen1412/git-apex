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
