# ADR-001: Commit Analytics Pipeline with Hybrid AI Chat

**Status:** Accepted
**Date:** 2026-08-20 (supersedes 2026-08-17 draft)
**Deciders:** Solo (portfolio project, ~5 YoE)

## Context

Portfolio project demonstrating end-to-end event-streaming and AI-augmented analytics (Kafka, MinIO, Neon Postgres, ClickHouse, ChromaDB) to a technical audience (recruiters / interviewers). GitHub push events flow through Kafka, fan out to four stores, and are queryable through an AI chat interface that routes each question to the right backend — relational, analytical, or semantic — rather than relying on a single query strategy.

Constraints:
- Infrastructure runs locally via Docker Compose, **except Postgres**, which is hosted on Neon (serverless)
- Scale: 1–3 personal repos, low volume (portfolio project, not production traffic)
- Primary goal is a defensible, explainable architecture — every component needs a reason that holds up under technical interview questioning, not just breadth for its own sake
- All application services written in Python
- AI Chat uses hybrid routing: structured queries for facts/aggregates, semantic search for meaning-based questions
- Infra is provisioned fully upfront (all services configured before building app logic)

**Scope:** commits/pushes only. Pull request events (`pull_request`, `pull_request_review`) are explicitly **deferred** — they are a separate webhook event type requiring their own topic and tables, and are not needed to prove the pattern.

**Diff content is in scope.** The `push` webhook payload lists changed file paths but carries no diff text, so full diff content requires a follow-up API call (see Decision).

## Decision

Build an event-driven pipeline:

```
GitHub ──webhook (push)──┐
                         ├──► Webhook Receiver (FastAPI) ──► Kafka (raw-commits)
Backfill Script ─────────┘                                          │
(GitHub REST API,                                                   │
 historical commits)                                       Stream Processor
                                                        (+ GET /commits/{sha}
                                                          for diff/patch text)
                                                                    │
              ┌───────────┬──────────────────┬─────────────────────┬┘
              ▼           ▼                  ▼                     ▼
           MinIO    Neon Postgres        ClickHouse             Chroma
        (raw JSON    (commits, authors,   (commit_metrics:    (embeddings of
         archive)     files_changed        time-series         commit msg +
                      incl. patch text)    aggregates)          diff content)
              │           │                  │                     │
              └───────────┴──────────────────┴─────────────────────┘
                                    │
                       AI Chat (router agent — picks
                       relational / analytical / semantic
                              path per question)
                                    │
                                    ▼
                             Dashboard (Streamlit)
```

Since GitHub cannot reach `localhost` directly, webhook delivery uses `smee.io` as a tunnel/relay during local development.

**Kafka** runs as `confluentinc/cp-kafka` in **KRaft mode** (no Zookeeper needed). This image is Apache-2.0 licensed and free — no Confluent Enterprise license required, since it contains only the community/OSS Kafka broker (Enterprise licensing applies to `cp-server` and other images bundling proprietary features like RBAC/Tiered Storage). Configured with dual listeners: `INTERNAL://kafka:29092` for container-to-container traffic and `EXTERNAL://localhost:9092` for Python services running on the host.

**PostgreSQL is hosted on Neon**, not run locally. Neon exposes two connection strings per branch: a *pooled* endpoint (PgBouncer, transaction mode) for application runtime, and a *direct* endpoint for schema application and migrations. Transaction-mode pooling breaks prepared statements and can time out on large transactions, so schema work must use the direct string. Both are configured separately in `.env`.

**Diff enrichment:** the stream processor makes one follow-up call per commit to `GET /repos/{owner}/{repo}/commits/{sha}` to retrieve per-file `patch` text plus `additions`/`deletions`. This is required because the push webhook payload only lists changed paths. The `files_changed.patch` column is nullable so a commit can be persisted even if the enrichment call fails or is skipped, with diff text backfilled in a later pass.

**Backfill path:** a separate one-off script pulls historical commits from the GitHub REST API, normalizes them to match the webhook payload shape, and publishes to the **same** `raw-commits` topic. This gives a single processing code path for both live and historical ingestion, and makes reprocessing straightforward. Idempotency is enforced at each sink: `commits.sha` primary key in Postgres (`ON CONFLICT DO NOTHING`), `ReplacingMergeTree` ordered by `sha` in ClickHouse, and `upsert()` keyed on `sha` in Chroma.

**Embedding pipeline:** the stream processor generates an embedding from each commit's message + diff content using a small local model (e.g. `sentence-transformers/all-MiniLM-L6-v2` — CPU-only, no external API call, no cost) and upserts it into Chroma with metadata (repo, sha, author, timestamp) so semantic results can still be filtered structurally.

**AI Chat routing:** an LLM-based router classifies each incoming question and dispatches it to the appropriate backend before generating a final natural-language answer:
- Factual/relational lookups ("who changed file X", "show commits in repo Y") → **Neon Postgres**
- Aggregate/trend questions ("how many commits this week", "most active author") → **ClickHouse**
- Meaning-based / fuzzy questions ("commits related to auth refactoring") → **Chroma** semantic search
- Chained cases (semantic search in Chroma to find candidate commits, then Postgres for full detail) — the router supports a two-step chain, not just single-hop dispatch.

**LLM provider:** OpenCode Zen, an OpenAI-compatible gateway (`https://opencode.ai/zen/v1`). Accessed via the `openai` Python SDK with `base_url` overridden. Configuration uses provider-neutral env vars (`LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`) so the provider can be swapped without code changes.

## Options Considered

### Kafka (confluentinc/cp-kafka, KRaft) — chosen
| Dimension | Assessment |
|-----------|------------|
| Complexity | Medium — broker concepts (topics, partitions, consumer groups), dual-listener config |
| Cost | Free (Apache 2.0, community image) |
| Scalability | Massive overkill for this project's volume — intentional |

**Pros:** Decouples ingest from processing; gives webhook and backfill a shared entry point; replay/restart safe.
**Cons:** Heavier resource footprint than a simple queue; listener/advertised-listener config is a known footgun on Docker for Mac/Windows.

### Direct writes from webhook receiver to storage — rejected
**Pros:** Much simpler, fewer services.
**Cons:** Removes the event-streaming layer entirely — no decoupling, no replay, and backfill would need a duplicate write path instead of reusing the same topic.

### pgvector (Postgres extension) for embeddings — rejected
**Pros:** Simpler ops, one less container, transactional consistency with relational data.
**Cons:** Weaker portfolio signal — doesn't demonstrate a purpose-built vector store or the reasoning behind dedicated infrastructure for a distinct access pattern.

### Standalone ChromaDB for embeddings — chosen
**Pros:** Purpose-built similarity search API; clean separation from transactional/analytical stores; gives a concrete answer to "why not just use Postgres for everything."
**Cons:** Extra service; embedding generation adds a step that must stay in sync with the other sinks.

### Local Postgres container — rejected
**Pros:** Fully self-contained stack; schema auto-applies via `docker-entrypoint-initdb.d`; no network dependency or cold starts.
**Cons:** Requires the demo machine to be running for any data to exist; nothing persists between environments.

### Neon (serverless Postgres) — chosen
| Dimension | Assessment |
|-----------|------------|
| Complexity | Low-medium — no container to run, but two connection strings to get right |
| Cost | Free tier |
| Scalability | Far beyond what this project needs |

**Pros:** Data persists independently of the local stack, so a demo doesn't require booting Docker; schema is standard Postgres, so `01-schema.sql` applies unchanged; one fewer local container.
**Cons:** Breaks the "fully local" property; free tier auto-suspends on idle, adding a cold-start delay of up to a few seconds on the first query after a pause — relevant during a live demo; schema changes require a manual `make schema` rather than automatic init; introduces real secrets into `.env`.

## Trade-off Analysis

- **Kafka's complexity is intentional.** For a real 1–3 repo project, Kafka is unjustifiable overhead on pure cost/benefit grounds — but the project's purpose is to demonstrate the pattern convincingly, so that overhead *is* the value.
- **Four storage systems instead of one** is similarly deliberate: each demonstrates a different data-access pattern (blob archive, relational, columnar/analytical, vector/semantic) rather than being justified by actual query load. State this plainly in any write-up — a senior interviewer will ask "would you actually build it this way at this scale?" and the honest answer is no, this fan-out is for breadth of demonstration.
- **Chroma over pgvector** trades operational simplicity for a stronger, more explainable architecture story — worth it since the audience is technical evaluators.
- **Neon over local Postgres** trades the clean "fully local, one command" story for demo durability: commit history survives independently of the laptop's Docker state. The cold-start delay is the price paid.
- **Hybrid routing over single-strategy chat** is the highest-leverage design choice for portfolio purposes: it shows judgment (recognizing that not all questions are equivalent) rather than just tool usage.
- **Diff enrichment costs an extra API call per commit.** At GitHub's authenticated rate limit (5,000 requests/hour) this is a non-issue for personal repos, but it does mean ingestion latency is bounded by GitHub's API rather than Kafka throughput, and a large backfill needs rate-limit awareness.
- **Backfill shares the live topic** rather than writing directly to sinks — one code path instead of two, at the cost of requiring every sink write to be idempotent.
- **No auto-generated AI summaries.** AI Chat is invoked only on user query, keeping steady-state cost low and concentrating the demo's interesting part in the routing layer.
- **PRs deferred** to keep the first working version narrow; commits alone are enough to prove the pipeline.
- **smee.io tunnel** is a dev convenience, not production ingress; acceptable since the receiver never leaves localhost.

## Consequences

- Easier: swapping/inspecting any single storage layer independently (raw MinIO payloads can be replayed into Postgres, or re-embedded into Chroma, if a schema or model changes); demoing without booting the full stack, since Neon holds the relational data; telling a coherent story about why each piece exists.
- Harder: setup spans local containers *and* a hosted service, so onboarding has two halves; the pooled-vs-direct Neon distinction is a silent failure mode if mixed up; ClickHouse schema changes still require a destructive `make reset` while Postgres changes need explicit `ALTER` statements; the router needs test coverage across all three query types plus the chained case; ingestion now depends on GitHub API availability and rate limits, not just the webhook.
- Revisit later: adding PR events as a second topic + tables; adding auto-summary generation as a second consumer group on `raw-commits`; swapping the local embedding model for a hosted one if quality lags; deploying a hosted demo instance if a live portfolio link is wanted.

## Action Items

1. [x] `docker-compose.yml`: Kafka (KRaft), MinIO, ClickHouse, ChromaDB
2. [x] Postgres schema (`commits`, `authors`, `files_changed` incl. `patch`, `additions`, `deletions`, `created_at`)
3. [x] ClickHouse table (`commit_metrics`, ReplacingMergeTree)
4. [x] MinIO bucket for raw payload archive
5. [x] Neon project + pooled/direct connection strings in `.env`; `make schema` to apply
6. [x] Confirm OpenCode Zen model ID via `GET {LLM_BASE_URL}/models`
7. [x] Define Chroma collection schema (embedding + metadata: repo, sha, author, timestamp)
8. [ ] Set up `smee.io` client + GitHub webhook pointing to it
9. [ ] Webhook receiver (FastAPI) with HMAC signature verification
10. [ ] Stream processor: Kafka consumer → diff enrichment → fan-out to 4 sinks (all idempotent)
11. [ ] Backfill script: GitHub REST API → normalize to webhook shape → publish to `raw-commits`
12. [x] AI Chat backend: router + per-backend query execution + result summarization
13. [x] Streamlit dashboard
14. [x] README framing the architecture's intent (breadth-of-demonstration, not scale necessity)
