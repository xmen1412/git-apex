# commit-pulse — infra

Local infrastructure for the GitHub commit analytics pipeline described in
`ADR-001`. This is **infra only** — no application services yet.

## Why this stack

A commit event fans out to four stores, each answering a different kind of question:

| Service | Holds | Answers |
|---|---|---|
| Kafka | `raw-commits` topic | decouples ingest from processing; same topic for webhook + backfill |
| MinIO | raw webhook JSON | source of truth for reprocessing if a schema changes |
| PostgreSQL (Neon) | `commits`, `authors`, `files_changed` (incl. diff patch text) | "who changed X", "show commits in repo Y" |
| ClickHouse | `commit_metrics` | "commits per day", "most active author", churn trends |
| Chroma | commit embeddings | "commits related to auth refactoring" (semantic) |

This fan-out is deliberately broader than 1–3 personal repos require — see the
trade-off section of the ADR.

**Scope note:** commits only, no PRs (for now). Diff content is included —
the `push` webhook payload only lists changed file paths, not diff text, so
the stream processor makes one follow-up call per commit to
`GET /repos/{owner}/{repo}/commits/{sha}` to fetch `patch` text and
per-file `additions`/`deletions`. This is what feeds Chroma's semantic
search over actual code changes, not just commit messages.

## Setup

Postgres is **not** run locally — this project uses [Neon](https://neon.com)
(serverless Postgres). Everything else runs in Docker.

```bash
cp .env.example .env     # fill in POSTGRES_POOLED_URL and POSTGRES_DIRECT_URL from Neon
make up                  # starts Kafka, MinIO, ClickHouse, Chroma
make schema              # applies infra/postgres/01-schema.sql to Neon
make verify              # confirms topics, bucket, and all schemas exist
```

`make verify` should show: the `raw-commits` topic, the `raw-commits` bucket,
three Neon Postgres tables, one ClickHouse table, and a Chroma heartbeat.

`make schema` requires `psql` installed on your machine.

## Neon: pooled vs direct connection

Neon exposes two connection strings per branch, differing by a `-pooler`
segment in the hostname. Using the wrong one causes confusing failures:

| Use case | String | Why |
|---|---|---|
| Stream processor, AI chat (runtime) | `POSTGRES_POOLED_URL` | PgBouncer transaction-mode pooling; handles many short-lived connections |
| `make schema`, migrations, `psql` | `POSTGRES_DIRECT_URL` | Transaction-mode pooling breaks prepared statements and can time out on large transactions |

Also note Neon's free tier **auto-suspends on idle** — expect a cold-start
delay of up to a few seconds on the first query after a pause. Worth knowing
before you demo this live.

## Ports (local services only)

| Service | Host port | Notes |
|---|---|---|
| Kafka | 9092 | from host; containers use `kafka:29092` |
| MinIO API | 9000 | |
| MinIO console | 9001 | log in with `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` |
| ClickHouse HTTP | 8123 | |
| ClickHouse native | 9010 | remapped — 9000 is MinIO's |
| Chroma | 8001 | remapped — container listens on 8000 |

Postgres has no local port — it lives in Neon.

## Schema init caveat

**ClickHouse** init scripts under `infra/clickhouse/` run only on first start,
when the data volume is empty. After editing that schema:

```bash
make reset    # DESTRUCTIVE — drops LOCAL volumes only, re-runs ClickHouse init
```

`make reset` does **not** touch Neon. To change the Postgres schema, edit
`infra/postgres/01-schema.sql` and re-run `make schema` — note the file uses
`CREATE TABLE IF NOT EXISTS`, so altering an existing table needs an explicit
`ALTER` (or drop the tables in Neon first).

## Idempotency

Both schemas are designed so re-ingesting the same commit is safe — important
because backfill and webhook feed the same Kafka topic:

- Postgres: `commits.sha` is the primary key → use `ON CONFLICT (sha) DO NOTHING`
- ClickHouse: `ReplacingMergeTree` ordered by `sha` → duplicates collapse on merge
- Chroma: use `upsert()` with `sha` as the document id

## Next

Application services (webhook receiver, stream processor, AI chat, dashboard)
are not scaffolded yet. See the ADR action items.
