-- Time-series / aggregate commit metrics. Answers analytical questions:
--   "how many commits this week", "most active author", churn trends.
-- ReplacingMergeTree dedups on ORDER BY (repo, sha) so backfill + webhook
-- re-ingestion of the same commit collapses to one row on merge.
-- ingested_at doubles as the version column: on collision, the latest
-- ingestion wins.

USE commitpulse;

CREATE TABLE IF NOT EXISTS commit_metrics
(
    repo         String,
    sha          String,
    author       String,
    committed_at DateTime,
    message      String,
    additions    UInt32,
    deletions    UInt32,
    files_count  UInt32,
    ingested_at  DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(ingested_at)
ORDER BY (repo, sha)
