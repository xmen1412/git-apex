"""Auto-watch: register the commit-pulse webhook on every repo the GitHub
token can administer, so pushes anywhere are ingested without manually
configuring each repo's webhook settings.

Idempotent — safe to re-run any time you create a new repo. Skips repos
where the token has no admin access (can't manage webhooks there) and forks
(unless --include-forks), and leaves repos that already have a hook pointed
at the same URL untouched.

Usage:
    python services/watch_repos.py
    python services/watch_repos.py --dry-run
    python services/watch_repos.py --affiliation owner --include-forks
"""
from __future__ import annotations

import argparse
import logging

import httpx

from commit_pulse.config import get_settings
from commit_pulse.github_client import create_repo_hook, list_repo_hooks, list_user_repos
from commit_pulse.repo_watcher import build_hook_payload, find_matching_hook, should_skip_repo

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Register webhooks across all accessible repos")
    parser.add_argument(
        "--affiliation",
        default="owner,collaborator,organization_member",
        help="GitHub /user/repos affiliation filter (default: everything the token can see)",
    )
    parser.add_argument("--include-forks", action="store_true", help="Also register hooks on forked repos")
    parser.add_argument("--dry-run", action="store_true", help="Show what would happen without calling the API")
    parser.add_argument("--webhook-url", help="Override GITHUB_WEBHOOK_URL from .env")
    args = parser.parse_args()

    settings = get_settings(require_webhook_secret=True)
    webhook_url = args.webhook_url or settings.github_webhook_url
    if not webhook_url:
        raise SystemExit(
            "GITHUB_WEBHOOK_URL is not set. Point it at a URL GitHub can reach — "
            "the smee.io channel already used for the tunnel (see `smee` service "
            "in docker-compose.yml), or a public URL if you deploy the receiver."
        )
    if not settings.github_token:
        raise SystemExit("GITHUB_TOKEN is not set.")

    repos = list_user_repos(settings.github_token, affiliation=args.affiliation)
    logger.info("found %d repo(s) for affiliation=%s", len(repos), args.affiliation)

    registered, already, skipped, failed = [], [], [], []

    for repo in repos:
        full_name = repo["full_name"]
        reason = should_skip_repo(repo, include_forks=args.include_forks)
        if reason:
            skipped.append((full_name, reason))
            continue
        try:
            hooks = list_repo_hooks(full_name, settings.github_token)
        except httpx.HTTPStatusError as exc:
            failed.append((full_name, f"list hooks failed: {exc.response.status_code}"))
            continue

        if find_matching_hook(hooks, webhook_url):
            already.append(full_name)
            continue

        if args.dry_run:
            registered.append(full_name)
            continue

        try:
            create_repo_hook(full_name, settings.github_token, build_hook_payload(webhook_url, settings.github_webhook_secret))
            registered.append(full_name)
            logger.info("registered webhook on %s", full_name)
        except httpx.HTTPStatusError as exc:
            failed.append((full_name, f"create hook failed: {exc.response.status_code}"))

    verb = "would register" if args.dry_run else "registered"
    print(f"\n{verb} on {len(registered)} repo(s):")
    for name in registered:
        print(f"  + {name}")
    print(f"\nalready watching {len(already)} repo(s):")
    for name in already:
        print(f"  = {name}")
    print(f"\nskipped {len(skipped)} repo(s):")
    for name, reason in skipped:
        print(f"  - {name} ({reason})")
    if failed:
        print(f"\nfailed on {len(failed)} repo(s):")
        for name, reason in failed:
            print(f"  ! {name} ({reason})")


if __name__ == "__main__":
    main()
