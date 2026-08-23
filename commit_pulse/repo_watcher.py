"""Pure decision logic for auto-registering webhooks across a user's repos.

Kept separate from commit_pulse/github_client.py (the raw HTTP calls) so the
decisions — which repos to skip, whether a hook already exists — are unit
testable without mocking the network.
"""
from __future__ import annotations

from typing import Any


def should_skip_repo(repo: dict[str, Any], include_forks: bool = False) -> str | None:
    """Return a human-readable skip reason, or None if the repo should be synced."""
    if repo.get("archived"):
        return "archived"
    if repo.get("disabled"):
        return "disabled"
    if repo.get("fork") and not include_forks:
        return "fork (use --include-forks to include)"
    if not repo.get("permissions", {}).get("admin", False):
        return "no admin permission on this repo (can't manage webhooks)"
    return None


def find_matching_hook(hooks: list[dict[str, Any]], webhook_url: str) -> dict[str, Any] | None:
    """Find an existing webhook already pointed at webhook_url, if any."""
    for hook in hooks:
        if hook.get("config", {}).get("url") == webhook_url:
            return hook
    return None


def build_hook_payload(webhook_url: str, secret: str) -> dict[str, Any]:
    """Body for POST /repos/{owner}/{repo}/hooks — push events only, HMAC secret."""
    return {
        "name": "web",
        "active": True,
        "events": ["push"],
        "config": {
            "url": webhook_url,
            "content_type": "json",
            "secret": secret,
            "insecure_ssl": "0",
        },
    }
