# commit-pulse

Ask plain-language questions about a GitHub repo's commit history — *"who
changed `auth.py`?"*, *"most active author this month?"*, *"commits related
to rate limiting"* — and watch an LLM router pick the right datastore to
answer each one.

Built as a portfolio piece under `xmen1412/git-apex`. See
`ADR-001-commit-analytics-pipeline.md` for the full design rationale and
`CHECKLIST.md` for the phased roadmap.

## Would I actually build it this way at this scale?

**No — and that's the point.** For 1–3 personal repos, a single Postgres
(with `pgvector` for embeddings) would answer every question above with a
fraction of the moving parts. This fan-out is deliberately broader than the
workload requires: it exists to demonstrate, end-to-end, the *patterns* a
real analytics system uses when each store answers a different kind of
question — event streaming, raw-payload archiving, relational lookups,
columnar aggregation, and vector search — glued together by an LLM router
that must justify its choice on every query. Treat it as a breadth-of-
demonstration artifact, not a scale recommendation.

## Architecture

```
            push webhook                backfill (GitHub REST API)
                  │                             │
                  ▼                             ▼
          services/webhook.py ─────────►  Kafka topic `raw-commits`
          (FastAPI, HMAC verify)                │
                                                ▼
                                      services/processor.py
                                      (diff enrichment via
                                       GET /repos/.../commits/{sha})
                                                │
              ┌───────────────┬─────────────────┼───────────────────┐
              ▼               ▼                 ▼                   ▼
            MinIO      Neon Postgres       ClickHouse            Chroma
          (raw JSON    (commits, authors,  (commit_metrics)   (embeddings:
           archive,     files_changed                          message+diff,
           replayable    + patch text)                     metadata + paths,
                                                               all-MiniLM-L6-v2)
              │               │                 │                   │
              └───────────────┴────────┬────────┴───────────────────┘
                                       ▼
                              services/chat.py
                    POST /chat: LLM call #1 classifies the question
                    → relational | analytical | semantic | chained
                    → safe whitelisted query execution (LLM never
                      writes SQL) → LLM call #2 summarizes
                                       ▼
                            services/dashboard.py
                    (Streamlit chat UI + routing transparency)
```

| Question type | Routed to | Example |
|---|---|---|
| relational | Neon Postgres | "who changed file X", "commits in repo Y" |
| analytical | ClickHouse | "commits per day", "most active author" |
| semantic | Chroma | "commits related to auth refactoring" |
| chained | Chroma → Postgres | "explain what changed in commits about rate limiting" |

**Safety:** the router LLM only picks a whitelisted *intent* and fills its
params. All SQL is hardcoded read-only templates with parameterized values
(`commit_pulse/query_executors.py`) — no raw LLM-generated SQL ever touches
a database.

**Idempotency** (backfill and webhook share one topic, so re-ingestion is
normal): Postgres `ON CONFLICT (sha) DO NOTHING` · ClickHouse
`ReplacingMergeTree` ordered by `(repo, sha)` · Chroma `upsert()` by `sha` ·
MinIO overwrites per-`sha` object.

## Running the demo

Prereqs: Docker Desktop with WSL integration, a free
[Neon](https://neon.com) project, a GitHub token, and an OpenCode Zen API
key.

```bash
cp .env.example .env    # fill in: Neon URLs, GitHub token/secret, LLM_API_KEY
docker compose up -d    # EVERYTHING: infra + webhook + processor + chat + dashboard
make schema             # apply Postgres schema to Neon (first run only)
make verify             # confirm topic, bucket, tables, heartbeat
```

All application services (`webhook`, `processor`, `chat`, `dashboard`) are
compose services built from the shared `commit-pulse-app` image, with the
repo bind-mounted at `/app` — code edits apply on `docker compose restart`
without a rebuild. Rebuild only when `requirements.txt` changes:
`docker compose up -d --build`.

Feed it data, either live or historical:

```bash
# historical: backfill N commits from any public repo (one-off container)
docker compose run --rm --no-deps \
  processor python services/backfill.py octocat/Hello-World --limit 20 --delay 1
```

Re-index existing Chroma documents from Neon after enabling semantic file
filtering. This does not call the GitHub API:

```bash
docker compose run --rm --no-deps \
  chat python services/backfill.py --rebuild-chroma
```

Use `chat` or `processor` for this command because they set the compose
network values `CHROMA_HOST=chroma` and `CHROMA_PORT=8000`. The host values
(`localhost:8001`) are only for commands running outside Docker.

### Auto-watch all your repos (live ingestion)

A GitHub token can read any repo it's granted access to, but GitHub webhooks
are still per-repository — nothing "just works" across your whole account
without registering a hook on each repo. `make watch-repos` does that
registration for you, once, for every repo the token can administer:

```bash
# .env needs: GITHUB_TOKEN (repo scope), GITHUB_WEBHOOK_SECRET,
# GITHUB_WEBHOOK_URL (a public URL GitHub can POST to — see below)
make watch-repos-dry-run   # preview: what would be registered/skipped
make watch-repos           # actually register (idempotent, safe to re-run)
```

This box has no public IP, so `GITHUB_WEBHOOK_URL` points at a
[smee.io](https://smee.io/new) channel, and the `smee` compose service tunnels
it to `webhook:8000/webhook` continuously (started by `docker compose up -d`
along with everything else — no separate `npx smee-client` terminal to keep
open). Push to any repo it registered and it flows straight into Kafka →
processor → all 4 sinks, same as the manually-wired repo from Phase 1.

Behavior:
- Skips repos where the token isn't an admin (can't manage webhooks there —
  e.g. repos you collaborate on but don't own).
- Skips forks by default (`--include-forks` to opt in).
- Skips repos that already have a hook pointed at the same URL — re-running
  after creating a new repo only touches the new one.
- If you deploy the receiver somewhere with a real public URL instead of
  using smee, set `GITHUB_WEBHOOK_URL` to that and skip the `smee` tunnel.

Try it: `curl -X POST http://localhost:8002/chat -H 'Content-Type: application/json' -d '{"question": "most active author?"}'` — the response includes the
route, the router's reasoning, the raw data, and the natural-language answer.
Or open the dashboard at http://localhost:8501.

Run tests: `docker compose run --rm --no-deps webhook python -m pytest tests/ -q`
The current suite has 43 passing tests. Live routing evaluation is skipped by
default; enable it with `RUN_LIVE_EVAL=1` when LLM calls and their cost are
acceptable.

## Stack notes

Postgres is **not** run locally — it lives in Neon (serverless). Everything
else is Docker. Neon exposes two connection strings; using the wrong one
causes confusing failures:

| Use case | String | Why |
|---|---|---|
| Stream processor, AI chat (runtime) | `POSTGRES_POOLED_URL` | PgBouncer transaction-mode pooling; handles many short-lived connections |
| `make schema`, migrations, `psql` | `POSTGRES_DIRECT_URL` | Pooling breaks prepared statements and large transactions |

Neon's free tier **auto-suspends on idle** — expect a few seconds of
cold-start on the first query after a pause. Worth knowing before a live demo.

### Ports (local services)

| Service | Host port | Notes |
|---|---|---|
| Kafka | 9092 | from host; containers use `kafka:29092` |
| MinIO API / console | 9000 / 9001 | |
| ClickHouse HTTP / native | 8123 / 9010 | native remapped — 9000 is MinIO's |
| Chroma | 8001 | container listens on 8000 |
| Webhook receiver | 8000 | |
| Chat backend | 8002 | |
| Streamlit dashboard | 8501 | |
| smee tunnel | *(none)* | internal only — relays `GITHUB_WEBHOOK_URL` to `webhook:8000` |

### Schema init caveat

ClickHouse init scripts under `infra/clickhouse/` run only on first start
(empty volume). After editing that schema: `make reset` (**destructive**,
local volumes only — never touches Neon). Postgres schema changes need
explicit `ALTER` statements against Neon.

## Improvements so far

Hardening completed after the first end-to-end demo:

- **Router output parsing** — the Zen API wraps JSON in ```` ```json ````
  fences even with `response_format: json_object`; `_parse_json_object()`
  strips fences, and unknown/broken routes fall back to `semantic` instead
  of crashing.
- **Intent whitelist** — route+intent pairs are validated against
  `ROUTE_INTENTS`; an intent the model invents is forced back to the
  route's default. The LLM only ever picks a template + params, never SQL.
- **File-path matching** — `commits_by_file` matches `README` against both
  `README` and `%/README` so questions match paths as stored by GitHub.
- **No hidden time window** — `commits_per_day` used to silently default
  to the last 30 days (confusing with old backfilled commits). Now `days`
  is only applied when the user actually mentions a time range.
- **Author identity fields** — `author_email` is available in ClickHouse,
  relational query results, chained results, and Chroma metadata. Relational
  queries join Neon `authors` and return both `author_username` and
  `author_email`, so questions such as "apa email dari xmen1412" use stored
  data instead of guessing.
- **Schema questions** — new `list_tables` intent answers "what tables are
  in Neon?" from `information_schema`.
- **Deterministic pre-router** — obvious table questions, file-change
  questions, and content questions in Indonesian/English are routed before
  the LLM. A rule that cannot extract its required parameter declines safely
  to the LLM.
- **Connection lifecycle** — Postgres connections and ClickHouse clients are
  closed after each query; the Chroma collection and embedding function are
  cached instead of rebuilt for every request.
- **Analytical time filters** — explicit `days` filters now apply consistently
  to all analytical intents, while unspecified questions still query full
  history.
- **Clarification handling** — missing required parameters return a normal
  clarification response instead of an empty result or HTTP 502.
- **Semantic file filtering** — Chroma stores non-empty `paths` metadata
  arrays and supports exact file-path filtering with `$contains`. Existing
  documents can be rebuilt from Neon using `--rebuild-chroma`.
- **Routing evaluation** — offline validation tests run without LLM cost;
  optional English/Indonesian live routing cases run only with
  `RUN_LIVE_EVAL=1`.
- **Structured-output probe** — `scripts/probe_structured_outputs.py` checks
  whether the configured Zen model supports strict JSON schema before the
  router is redesigned around it.
- **One-command stack** — webhook, processor, chat, and dashboard are all
  `docker compose` services (`docker compose up -d`), replacing the manual
  `docker run` commands.
- **Auto-watch all repos** — a token can *read* any repo it's granted, but
  webhooks are still per-repo; `make watch-repos` registers the webhook on
  every repo the token administers (skips forks/no-admin/already-registered),
  and a persistent `smee` tunnel service replaces the manual `npx
  smee-client` terminal. Pushing to any of the 14 registered repos now flows
  into the pipeline without touching GitHub settings by hand.

## Remaining limitations / future improvements

Recommended next steps, ordered by value for a production-like deployment:

- **Production resilience** — add bounded retries and timeouts for Neon,
  Chroma, ClickHouse, GitHub, and Zen, plus clear upstream error responses.
- **Observability** — expose request latency, selected route, query duration,
  ingestion lag, Kafka failures, and per-sink error counters. Add a correlation
  ID so one commit can be traced across webhook, Kafka, processor, and sinks.
- **Reliable ingestion** — add a dead-letter topic and retry policy for poison
  events instead of only logging failed Kafka messages. Persist processing
  status so failed sink writes can be replayed selectively.
- **Author identity normalization** — GitHub may expose privacy-protected
  `noreply` addresses or different emails for the same person. Define a stable
  identity policy and document privacy implications before exposing emails in a
  public dashboard.
- **Explicit author lookup intent** — add a dedicated `author_profile` intent
  for username/email questions instead of relying on the broader
  `commits_by_author` query and LLM summarization.
- **Schema and contract tests** — verify result fields such as
  `author_email` whenever the database schema or query templates change.
- **Pagination and access control** — paginate large commit results, protect
  `/chat` with authentication/rate limiting, and restrict repositories if the
  dashboard is deployed publicly.
