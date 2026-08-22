from __future__ import annotations

import logging

import psycopg2
from psycopg2.extras import execute_values

from .config import Settings
from .models import CommitEvent

logger = logging.getLogger(__name__)


class PostgresSink:
    def __init__(self, settings: Settings):
        self.dsn = settings.postgres_pooled_url

    def upsert_commit(self, event: CommitEvent) -> None:
        with psycopg2.connect(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO authors (username, email, name)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (email) DO UPDATE SET name = EXCLUDED.name, username = EXCLUDED.username
                    RETURNING id
                    """,
                    (event.author_username, event.author_email, event.author_name),
                )
                author_id = cur.fetchone()[0]
                cur.execute(
                    """
                    INSERT INTO commits (sha, repo, author_id, message, committed_at, url, source)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (sha) DO NOTHING
                    """,
                    (event.sha, event.repo, author_id, event.message,
                     event.committed_at, event.url, event.source),
                )
                if event.files:
                    rows = [
                        (event.sha, f.path, f.change_type, f.additions, f.deletions, f.patch)
                        for f in event.files
                    ]
                    execute_values(
                        cur,
                        """
                        INSERT INTO files_changed (commit_sha, path, change_type, additions, deletions, patch)
                        VALUES %s
                        ON CONFLICT (commit_sha, path) DO NOTHING
                        """,
                        rows,
                    )
        logger.info("upserted commit %s into postgres", event.sha)
