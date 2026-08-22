-- Structured commit metadata. Answers relational/lookup questions:
--   "who changed auth.py last month", "show all commits in repo X"

CREATE TABLE IF NOT EXISTS authors (
    id          BIGSERIAL PRIMARY KEY,
    username    TEXT,                    -- GitHub login; null for commits w/o linked account
    email       TEXT NOT NULL,
    name        TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),  -- first time this author appeared in our data
    UNIQUE (email)
);

CREATE TABLE IF NOT EXISTS commits (
    sha             TEXT PRIMARY KEY,    -- natural key: makes backfill idempotent
    repo            TEXT NOT NULL,       -- "owner/name"
    author_id       BIGINT NOT NULL REFERENCES authors(id),
    message         TEXT NOT NULL,
    committed_at    TIMESTAMPTZ NOT NULL,
    url             TEXT,
    source          TEXT NOT NULL,       -- 'webhook' | 'backfill' — useful for debugging
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_commits_repo_time ON commits (repo, committed_at DESC);
CREATE INDEX IF NOT EXISTS idx_commits_author    ON commits (author_id);

-- Per-file change within a commit. `patch` holds the actual unified diff text,
-- fetched via a follow-up call to GET /repos/{owner}/{repo}/commits/{sha}
-- (the push webhook payload only lists changed paths, not diff content).
-- patch is nullable: backfill/webhook processing can insert the row first and
-- backfill the diff text in a second pass if the extra API call fails or is skipped.
CREATE TABLE IF NOT EXISTS files_changed (
    id          BIGSERIAL PRIMARY KEY,
    commit_sha  TEXT NOT NULL REFERENCES commits(sha) ON DELETE CASCADE,
    path        TEXT NOT NULL,
    change_type TEXT NOT NULL,           -- 'added' | 'modified' | 'removed'
    additions   INTEGER NOT NULL DEFAULT 0,
    deletions   INTEGER NOT NULL DEFAULT 0,
    patch       TEXT,                    -- unified diff text; null if not fetched
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (commit_sha, path)
);

CREATE INDEX IF NOT EXISTS idx_files_path ON files_changed (path);
