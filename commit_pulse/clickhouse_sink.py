from __future__ import annotations

import logging

import clickhouse_connect

from .config import Settings
from .models import CommitEvent

logger = logging.getLogger(__name__)


class ClickHouseSink:
    def __init__(self, settings: Settings):
        self.client = clickhouse_connect.get_client(
            host=settings.clickhouse_host,
            port=settings.clickhouse_port,
            username=settings.clickhouse_user,
            password=settings.clickhouse_password,
            database=settings.clickhouse_db,
        )

    def upsert_commit(self, event: CommitEvent) -> None:
        self.client.insert(
            "commit_metrics",
            [[
                event.repo,
                event.sha,
                event.author_username or event.author_email or "unknown",
                event.author_email,
                event.committed_at.replace(tzinfo=None),
                event.message,
                event.additions,
                event.deletions,
                len(event.files),
            ]],
            column_names=[
                "repo", "sha", "author", "author_email", "committed_at", "message",
                "additions", "deletions", "files_count",
            ],
        )
        logger.info("inserted commit %s into clickhouse", event.sha)
