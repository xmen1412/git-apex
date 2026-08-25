from __future__ import annotations

import argparse
import logging
import time
from contextlib import closing

import psycopg2
import psycopg2.extras

from commit_pulse.chroma_sink import ChromaSink
from commit_pulse.config import get_settings
from commit_pulse.github_client import fetch_recent_commits
from commit_pulse.kafka_io import make_producer, publish_push_payload
from commit_pulse.models import CommitEvent, FileChange, to_push_payload

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def rebuild_chroma(settings) -> None:
    """Re-index Chroma from Postgres (source of truth) — no GitHub API calls.

    Reads every commit + its files_changed rows and re-upserts into Chroma,
    adding the ``paths`` metadata array used by semantic file filtering.
    """
    sink = ChromaSink(settings)
    with closing(psycopg2.connect(settings.postgres_pooled_url)) as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT c.sha, c.repo, c.message, c.committed_at, c.url, c.source,
                       a.username, a.email, a.name,
                       f.path, f.change_type, f.additions, f.deletions, f.patch
                FROM commits c
                JOIN authors a ON a.id = c.author_id
                LEFT JOIN files_changed f ON f.commit_sha = c.sha
                ORDER BY c.sha
                """
            )
            rows = cur.fetchall()

    commits: dict[str, CommitEvent] = {}
    for r in rows:
        sha = r["sha"]
        event = commits.get(sha)
        if event is None:
            event = CommitEvent(
                sha=sha,
                repo=r["repo"],
                message=r["message"],
                author_name=r["name"] or "",
                author_email=r["email"] or "",
                author_username=r["username"],
                committed_at=r["committed_at"],
                url=r["url"],
                source=r["source"],
                files=[],
            )
            commits[sha] = event
        if r["path"]:
            event.files.append(FileChange(
                path=r["path"],
                change_type=r["change_type"],
                additions=r["additions"],
                deletions=r["deletions"],
                patch=r["patch"],
            ))

    for event in commits.values():
        sink.upsert_commit(event)
    logger.info("rebuilt chroma from %d commits", len(commits))


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill historical commits into Kafka")
    parser.add_argument("repo", nargs="?", help="GitHub repo in owner/name format")
    parser.add_argument("--limit", type=int, default=50, help="Max commits to fetch (default: 50)")
    parser.add_argument("--delay", type=float, default=1.0, help="Delay between requests in seconds (rate-limit awareness)")
    parser.add_argument("--rebuild-chroma", action="store_true",
                        help="Re-index Chroma from Postgres instead of backfilling from GitHub")
    args = parser.parse_args()

    settings = get_settings()

    if args.rebuild_chroma:
        rebuild_chroma(settings)
        return

    if not args.repo:
        parser.error("repo is required unless --rebuild-chroma is passed")

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
