# CHECKLIST — commit-pulse roadmap

Checklist proyek `git-apex` (commit-pulse). Referensi utama: `ADR-001-commit-analytics-pipeline.md`.
Status tiap item: `[ ]` = belum, `[x]` = selesai, `[~]` = sebagian.

## Fase 0 — Infra up & verified

Docker Desktop/WSL sudah aktif dan Docker Engine dapat diakses dari distro ini.

- [x] Start Docker Desktop di Windows
- [x] Aktifkan WSL integration untuk Ubuntu (Settings → Resources → WSL Integration)
- [x] Verifikasi: `docker compose version`
- [x] `make up` — Kafka, MinIO, ClickHouse, Chroma up
- [x] `make schema` — apply `infra/postgres/01-schema.sql` ke Neon (diterapkan dengan PostgreSQL client container karena `psql` host belum tersedia)
- [x] `make verify` — topic, bucket, 3 tabel Neon, 1 tabel ClickHouse, Chroma heartbeat (diverifikasi ekuivalen; `psql` host belum tersedia)
- [x] `infra/clickhouse/01-commit-metrics.sql` ter-init (jalan otomatis saat volume kosong)

Catatan: schema dan seluruh sink sudah tervalidasi. `psql` belum tersedia di host, sehingga schema dan query tabel Neon dijalankan dengan client PostgreSQL dalam container.

## Fase 1 — Ingestion pipeline

Data commit mengalir: GitHub webhook + backfill → Kafka `raw-commits` → stream processor → 4 sink.

### 1.1 Webhook receiver — ADR item 9
- [x] FastAPI endpoint `POST /webhook` (push event)
- [x] Verifikasi HMAC-SHA256 dengan `GITHUB_WEBHOOK_SECRET`
- [x] Publish payload ke topic `raw-commits` (Kafka producer)
- [x] Response cepat ke GitHub (2xx) sebelum/terlepas dari proses async

### 1.2 Stream processor — ADR item 10
- [x] Kafka consumer `raw-commits`
- [x] Diff enrichment: `GET /repos/{owner}/{repo}/commits/{sha}` per commit (patch text, additions, deletions)
- [x] Fan-out idempotent ke 4 sink:
  - [x] MinIO — arsip raw JSON (`raw-commits` bucket)
  - [x] Neon Postgres — `commits`, `authors`, `files_changed` (ON CONFLICT DO NOTHING)
  - [x] ClickHouse — `commit_metrics` (ReplacingMergeTree, dedup `(repo, sha)`)
  - [x] Chroma — embedding message+diff (`all-MiniLM-L6-v2`), upsert key `sha`
- [x] `files_changed.patch` nullable → row tetap bisa insert walau enrichment gagal (backfill diff pass kedua)

### 1.3 Backfill script — ADR item 11
- [x] Pull historical commits dari GitHub REST API
- [x] Normalisasi ke bentuk webhook payload (marker `_source: "backfill"` agar sink bisa membedakan asal data)
- [x] Publish ke topic yang SAMA (`raw-commits`) — satu code path dengan webhook
- [x] Rate-limit aware (5.000 req/jam) — arg `--delay` antar request

Catatan: kode Fase 1 tervalidasi end-to-end via container `commit-pulse-app` (host belum punya pip/venv). Test E2E: backfill 3 commit `octocat/Hello-World` → Kafka → processor → 4 sink (Postgres `source='backfill'`, patch terisi, Chroma 3 embedding); webhook HMAC valid→200 / invalid→401 / ping→ignored; replay sha duplikat ter-dedup di semua sink. `committed_at` dinormalisasi ke UTC aware.

### 1.4 smee.io + GitHub webhook — ADR item 8
- [x] `smee` client (forward ke localhost webhook receiver)
- [x] GitHub webhook di repo `xmen1412/Hello-World` → payload URL smee, content-type json, secret `GITHUB_WEBHOOK_SECRET`
- [x] Scope: event `push` saja (PR deferred — ADR Scope)

Catatan: channel smee `https://smee.io/bQTW1EdtYfVr0cZx`; push dummy tervalidasi sampai `POST /webhook` dan mendapat response `200 OK`.

### 1.5 Auto-watch semua repo (post-Fase 2, ditambah atas permintaan user)
- [x] `commit_pulse/repo_watcher.py` — pure logic: `should_skip_repo` (skip archived/disabled/fork kecuali `--include-forks`/tanpa admin), `find_matching_hook`, `build_hook_payload`
- [x] `commit_pulse/github_client.py` +`list_user_repos` (paginated `/user/repos`), `list_repo_hooks`, `create_repo_hook`
- [x] `commit_pulse/config.py` +`github_webhook_url` (env `GITHUB_WEBHOOK_URL`)
- [x] `services/watch_repos.py` — CLI, idempotent, `--dry-run`, `--include-forks`, `--affiliation`
- [x] `smee` service baru di `docker-compose.yml` (node:20-alpine + smee-client, tunnel persisten `GITHUB_WEBHOOK_URL` → `webhook:8000/webhook`, tidak perlu `npx` manual lagi)
- [x] `make watch-repos` / `make watch-repos-dry-run`
- [x] `tests/test_repo_watcher.py` (12 test, total 32)
- [x] `pytest` dipindah dari ad-hoc `pip install` ke `requirements.txt`

Catatan: dijalankan live dengan token `xmen1412` — 19 repo terdeteksi, 14 didaftarkan, 1 sudah ada (`Hello-World`), 4 fork di-skip, 2 repo kolaborator tanpa admin di-skip (`dwikylaga1/Tool-Store-Pintar`, `yohanesam/sentiment-analysis-project`). Ditemukan hook basi peninggalan sebelumnya di `xmen1412/oop` mengarah ke placeholder `https://example.com/webhook` (502) — dihapus manual via API. Verifikasi E2E: GitHub ping event → smee → `cp-smee` → `cp-webhook` → `200 OK`. Re-run kedua idempoten (15/15 "already watching").

## Fase 2 — AI & Demo

Prasyarat: Fase 0 + Fase 1 selesai (data sudah di 4 sink).

### 2.1 Konfirmasi model OpenCode Zen — ADR item 6
- [x] `curl -H "Authorization: Bearer $LLM_API_KEY" $LLM_BASE_URL/models` (catatan: endpoint ini publik — validasi key HARUS via chat completion)
- [x] Pilih model ID → isi `LLM_MODEL=claude-haiku-4-5` di `.env`
- [x] Test 1 chat completion sederhana → `claude-haiku-4-5` merespons OK
- [x] Update `.env.example` dengan contoh model valid

### 2.2 Chroma collection schema — ADR item 7
- [x] Collection: `commits` (dari `CHROMA_COLLECTION`)
- [x] Document = commit message + diff/patch (truncate ~8k chars) — `commit_pulse/chroma_sink.py`
- [x] Embedding: `sentence-transformers/all-MiniLM-L6-v2` (384 dim, CPU-only)
- [x] Metadata: `repo`, `sha`, `author`, `committed_at` (ISO) → semantic + structural filter
- [x] ID = `sha`, idempotency via `upsert()`

Catatan: schema sudah diimplementasikan sejak Fase 1 di `chroma_sink.py`; divalidasi live ke container `cp-chroma` (4 dokumen dari backfill+webhook, semantic query OK, filter `where={"repo": ...}` OK, upsert sha sama tidak duplikat).

### 2.3 AI Chat backend — ADR item 12
- [x] Router: LLM call #1 klasifikasi → relational | analytical | semantic | chained (output JSON terstruktur) — `commit_pulse/llm_router.py`
- [x] Relational → Neon (POOLED url): "who changed file X", "commits in repo Y" — intent: `commits_by_file`, `commits_by_repo`, `commits_by_author`, `commit_detail`
- [x] Analytical → ClickHouse: "commits per day", "most active author" — intent: `commits_per_day`, `most_active_authors`, `churn_by_repo` (termasuk `author_email`)
- [x] Semantic → Chroma top-k (k=10) + optional metadata filter (`where={"repo": ...}`)
- [x] Chained → semantic dulu (kandidat sha) → Postgres detail (`commit_pulse/query_executors.py: execute_chained`)
- [x] Summarization: LLM call #2 → jawaban natural language
- [x] Safety: SQL read-only, whitelist tabel, parameterized — bukan raw SQL bebas dari LLM (LLM hanya pilih intent + isi params; template SQL hardcoded di `query_executors.py`)
- [x] Test: minimal 1 per route type + chained case (`tests/test_chat.py`, 12 test)
- [x] FastAPI endpoint `/chat`, openai SDK + `base_url=LLM_BASE_URL` (`services/chat.py`)

Catatan: tervalidasi live via container `cp-chat` (port 8002), sekarang service `chat` di docker-compose (naik via `docker compose up -d`). Keempat route menjawab dengan data nyata. Bug fix: router men-strip markdown code fence dari output LLM (Zen tidak mematuhi `response_format` strict); `commits_by_file` mendukung suffix match (`README.md` → `README`).

### 2.4 Streamlit dashboard — ADR item 13
- [x] Chat UI (`st.chat_message` / `st.chat_input`) → chat backend via HTTP (`services/dashboard.py`, env `CHAT_API_URL`)
- [x] Tampilkan routing decision (transparency — nilai jual demo): badge route + intent + reasoning + params di sidebar
- [x] Opsional: chart ClickHouse commits per day (bar chart per repo, langsung dari `commit_metrics`)

Catatan: service `dashboard` di docker-compose (port 8501), berbicara dengan `chat` via compose network (CHAT_API_URL=http://chat:8000).

### 2.5 README framing — ADR item 14
- [x] Jelaskan intent arsitektur: breadth-of-demonstration, BUKAN scale necessity
- [x] Jawaban jujur untuk "would you actually build it this way at this scale?" → tidak, fan-out untuk demonstrasi pola
- [x] Diagram arsitektur + cara menjalankan demo end-to-end

## Fase 3 — Hardening (dari IMPROVEMENTS.md)

Perbaikan bug + robustness, urut sesuai prioritas di `IMPROVEMENTS.md`.

### 3.1 BUG 1 — connection leak tiap request
- [x] `psycopg2` context manager tidak menutup socket → wrap dengan `closing()` via helper `_pg_cursor` di `commit_pulse/query_executors.py`
- [x] ClickHouse client `execute_analytical` ditutup dengan `closing()`
- [x] Chroma collection di-cache (`@lru_cache` pada `_chroma_collection_cached`) — tidak rebuild HttpClient + embedding function tiap query

### 3.2 BUG 2 — `days` diabaikan di 2 intent analytical
- [x] `{days_filter}` ditambahkan ke ketiga intent analytical (`commits_per_day`, `most_active_authors`, `churn_by_repo`)
- [x] Filter `days` diterapkan seragam di `execute_analytical` (bukan hanya `commits_per_day`)
- [x] `_days()` tetap `None` saat tak disebut (tidak ada hidden window)

### 3.3 FIX 3 — param validation (`NeedsClarification`)
- [x] `REQUIRED_PARAMS` (whitelist param wajib per intent) + `validate_params()` di `query_executors.py`
- [x] `NeedsClarification` exception → `chat.py` menangkap sebelum generic `except` dan membalas klarifikasi (200, bukan 502)
- [x] `_PARAM_PROMPTS` untuk pesan klarifikasi ramah (which file? / which repo? / ...)

### 3.4 FIX 4 — eval set
- [x] `tests/test_eval_offline.py` — deterministik, jalan di CI (param validation + self-enforcing whitelist)
- [x] `tests/test_eval_routing.py` — live routing eval, di-skip kecuali `RUN_LIVE_EVAL=1` (16 kasus + days extraction)

### 3.5 FIX 5 — deterministic pre-router
- [x] `commit_pulse/pre_router.py` — rule-based routing (`list_tables`, `who changed <file>`, content→semantic)
- [x] Rule yang match tapi param tak bisa di-extract → decline ke LLM
- [x] `chat.py`: `pre_route(req.question) or route_question(...)`
- [x] Hardcoded Indonesian override di `llm_router.py` dihapus (digantikan rule semantic bilingual)
- [x] `tests/test_pre_router.py` (6 test)

### 3.6 FIX 6 — semantic file filtering
- [x] `chroma_sink.py` menyimpan `paths` sebagai metadata array (guard empty list)
- [x] `query_executors.py` filter `where={"paths": {"$contains": file_path}}` + kombinasi `$and` dengan `repo`
- [x] `services/backfill.py --rebuild-chroma` — re-index Chroma dari Postgres (source of truth, tanpa GitHub API)

### 3.7 FIX 7 — structured outputs probe
- [x] `scripts/probe_structured_outputs.py` — probe dukungan `json_schema` strict sebelum commit desain

Catatan: 43 test lolos (17 live-eval di-skip tanpa `RUN_LIVE_EVAL`). `--rebuild-chroma` perlu dijalankan sekali setelah deploy agar koleksi lama mendapat field `paths`.

---

## Referensi cepat

| Store | Isi | Jawab pertanyaan | Idempotensi |
|---|---|---|---|
| Kafka | topic `raw-commits` | decouple ingest vs processing | — |
| MinIO | raw webhook JSON | source of truth reprocessing | overwrite per `sha` |
| Neon Postgres | `commits`, `authors`, `files_changed` (+ patch) | "who changed X", "commits in repo Y" | `ON CONFLICT (sha) DO NOTHING` |
| ClickHouse | `commit_metrics` (+ author email) | "commits per day", "most active author", churn | `ReplacingMergeTree ORDER BY (repo, sha)` |
| Chroma | embedding commit msg+diff | "commits related to auth refactoring" | `upsert()` key `sha` |

### Koneksi penting

- Neon **POOLED** (`-pooler`) → runtime aplikasi / stream processor
- Neon **DIRECT** (tanpa `-pooler`) → `make schema`, migrasi, psql
- ClickHouse host port 8123 (HTTP), 9010 (native) — 9000 dipakai MinIO
- Chroma host port 8001 — container mendengarkan 8000
- LLM provider: OpenCode Zen `https://opencode.ai/zen/v1` (OpenAI-compatible), env: `LLM_BASE_URL`/`LLM_API_KEY`/`LLM_MODEL`
