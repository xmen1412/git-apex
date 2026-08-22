from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class FileChange:
    path: str
    change_type: str
    additions: int = 0
    deletions: int = 0
    patch: str | None = None


@dataclass
class CommitEvent:
    sha: str
    repo: str
    message: str
    author_name: str
    author_email: str
    author_username: str | None
    committed_at: datetime
    url: str
    source: str
    files: list[FileChange] = field(default_factory=list)

    @property
    def additions(self) -> int:
        return sum(f.additions for f in self.files)

    @property
    def deletions(self) -> int:
        return sum(f.deletions for f in self.files)


def parse_push_event(payload: dict[str, Any], source: str = "webhook") -> list[CommitEvent]:
    """Normalize a GitHub push webhook payload (or backfill equivalent) to commit events."""
    repo = payload.get("repository", {}).get("full_name", "")
    events: list[CommitEvent] = []
    for commit in payload.get("commits", []):
        author = commit.get("author", {})
        committed_at = commit.get("timestamp")
        files: list[FileChange] = []
        for status, key in (("added", "added"), ("modified", "modified"), ("removed", "removed")):
            for path in commit.get(key, []) or []:
                files.append(FileChange(path=path, change_type=status))
        events.append(
            CommitEvent(
                sha=commit["id"],
                repo=repo,
                message=commit.get("message", ""),
                author_name=author.get("name", ""),
                author_email=author.get("email", ""),
                author_username=author.get("username"),
                committed_at=_parse_ts(committed_at),
                url=commit.get("url", ""),
                source=source,
                files=files,
            )
        )
    return events


def _parse_ts(value: str | None) -> datetime:
    """Parse an ISO-8601 timestamp and normalize to timezone-aware UTC."""
    if not value:
        return datetime.now(timezone.utc)
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def to_push_payload(sha: str, repo_full_name: str, message: str,
                    author: dict[str, str], committed_at: str, url: str,
                    added: list[str], modified: list[str], removed: list[str]) -> dict[str, Any]:
    """Build a webhook-shaped push payload for backfill ingestion."""
    return {
        "_source": "backfill",
        "repository": {"full_name": repo_full_name},
        "commits": [{
            "id": sha,
            "message": message,
            "timestamp": committed_at,
            "url": url,
            "author": author,
            "added": added,
            "modified": modified,
            "removed": removed,
        }],
    }
