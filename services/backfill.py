from __future__ import annotations

import argparse
import logging
import time

from commit_pulse.config import get_settings
from commit_pulse.github_client import fetch_recent_commits
from commit_pulse.kafka_io import make_producer, publish_push_payload
from commit_pulse.models import to_push_payload

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill historical commits into Kafka")
    parser.add_argument("repo", help="GitHub repo in owner/name format")
    parser.add_argument("--limit", type=int, default=50, help="Max commits to fetch (default: 50)")
    parser.add_argument("--delay", type=float, default=1.0, help="Delay between requests in seconds (rate-limit awareness)")
    args = parser.parse_args()

    settings = get_settings()
    producer = make_producer(settings)
    commits = fetch_recent_commits(args.repo, settings.github_token, limit=args.limit)
    logger.info("fetched %d commits from %s", len(commits), args.repo)

    for i, commit in enumerate(commits):
        author = commit.get("commit", {}).get("author", {})
        payload = to_push_payload(
            sha=commit["sha"],
            repo_full_name=args.repo,
            message=commit["commit"]["message"],
            author={
                "name": author.get("name", ""),
                "email": author.get("email", ""),
                "username": commit.get("author", {}).get("login", ""),
            },
            committed_at=author.get("date", ""),
            url=commit.get("html_url", ""),
            added=[],
            modified=[],
            removed=[],
        )
        publish_push_payload(producer, settings, payload)
        logger.info("backfilled %s (%d/%d)", commit["sha"], i + 1, len(commits))
        if i < len(commits) - 1:
            time.sleep(args.delay)


if __name__ == "__main__":
    main()
