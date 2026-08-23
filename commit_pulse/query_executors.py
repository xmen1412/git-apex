"""Safe per-route query executors for the AI chat backend.

Safety model: the LLM router only picks a whitelisted intent and fills its
params — it never writes SQL. All statements below are read-only templates
with parameterized values; table/column names are hardcoded.
"""
from __future__ import annotations

import logging
from typing import Any

import chromadb
import clickhouse_connect
import psycopg2
import psycopg2.extras
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

from .config import Settings
from .llm_router import RouteDecision

logger = logging.getLogger(__name__)

MAX_LIMIT = 100


def _limit(params: dict[str, Any], default: int = 10) -> int:
    try:
        value = int(params.get("limit") or default)
    except (TypeError, ValueError):
        value = default
    return max(1, min(value, MAX_LIMIT))


def _days(params: dict[str, Any], default: int | None = None) -> int | None:
    """Return clamped days window, or None when no time range was specified.
    None means "no time filter" so old commits (e.g. backfilled history)
    still show up instead of silently returning empty results."""
    if "days" not in params or params.get("days") in (None, ""):
        return default
    try:
        value = int(params["days"])
    except (TypeError, ValueError):
        return default
    return max(1, min(value, 365))


# ---------------------------------------------------------------------------
# Relational -> Neon Postgres (read-only, parameterized templates)
# ---------------------------------------------------------------------------

_RELATIONAL_SQL = {
    "commits_by_file": """
        SELECT c.sha, c.repo, COALESCE(a.username, a.name, a.email) AS author,
               c.message, c.committed_at, f.change_type, f.additions, f.deletions
        FROM files_changed f
        JOIN commits c ON c.sha = f.commit_sha
        JOIN authors a ON a.id = c.author_id
        WHERE f.path = %s OR f.path LIKE %s
        ORDER BY c.committed_at DESC
        LIMIT %s
    """,
    "commits_by_repo": """
        SELECT c.sha, c.repo, COALESCE(a.username, a.name, a.email) AS author,
               c.message, c.committed_at, c.source
        FROM commits c
        JOIN authors a ON a.id = c.author_id
        WHERE c.repo = %s
        ORDER BY c.committed_at DESC
        LIMIT %s
    """,
    "commits_by_author": """
        SELECT c.sha, c.repo, COALESCE(a.username, a.name, a.email) AS author,
               c.message, c.committed_at
        FROM commits c
        JOIN authors a ON a.id = c.author_id
        WHERE a.username = %s OR a.email = %s OR a.name = %s
        ORDER BY c.committed_at DESC
        LIMIT %s
    """,
    "commit_detail": """
        SELECT c.sha, c.repo, COALESCE(a.username, a.name, a.email) AS author,
               c.message, c.committed_at, c.url, c.source,
               f.path, f.change_type, f.additions, f.deletions, f.patch
        FROM commits c
        JOIN authors a ON a.id = c.author_id
        LEFT JOIN files_changed f ON f.commit_sha = c.sha
        WHERE c.sha = %s
        LIMIT %s
    """,
    "list_tables": """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = %s
          AND table_type = %s
        ORDER BY table_name
    """,
}


def _relational_params(intent: str, params: dict[str, Any]) -> tuple:
    limit = _limit(params)
    if intent == "commits_by_file":
        path = params.get("file_path") or ""
        return (path, f"%/{path}", limit)
    if intent == "commits_by_repo":
        return (params.get("repo") or "", limit)
    if intent == "commits_by_author":
        author = params.get("author") or ""
        return (author, author, author, limit)
    if intent == "commit_detail":
        return (params.get("sha") or "", MAX_LIMIT)
    if intent == "list_tables":
        return ("public", "BASE TABLE")
    raise ValueError(f"unknown relational intent: {intent}")


def execute_relational(settings: Settings, decision: RouteDecision) -> list[dict[str, Any]]:
    intent = decision.params["intent"]
    sql = _RELATIONAL_SQL[intent]
    with psycopg2.connect(settings.postgres_pooled_url) as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, _relational_params(intent, decision.params))
            return [dict(r) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# Analytical -> ClickHouse (read-only, parameterized templates)
# ---------------------------------------------------------------------------

_ANALYTICAL_SQL = {
    "commits_per_day": """
        SELECT toDate(committed_at) AS day, repo, count() AS commits
        FROM commit_metrics
        WHERE 1=1
          {days_filter}
          {repo_filter}
        GROUP BY day, repo
        ORDER BY day DESC
        LIMIT {limit:UInt32}
    """,
    "most_active_authors": """
        SELECT author, author_email, count() AS commits,
               sum(additions) AS total_additions, sum(deletions) AS total_deletions
        FROM commit_metrics
        WHERE 1=1
          {repo_filter}
        GROUP BY author, author_email
        ORDER BY commits DESC
        LIMIT {limit:UInt32}
    """,
    "churn_by_repo": """
        SELECT repo, count() AS commits,
               sum(additions) AS total_additions, sum(deletions) AS total_deletions,
               sum(files_count) AS total_files
        FROM commit_metrics
        WHERE 1=1
          {repo_filter}
        GROUP BY repo
        ORDER BY commits DESC
        LIMIT {limit:UInt32}
    """,
}


def execute_analytical(settings: Settings, decision: RouteDecision) -> list[dict[str, Any]]:
    intent = decision.params["intent"]
    repo = decision.params.get("repo")
    days = _days(decision.params)
    sql = _ANALYTICAL_SQL[intent]
    sql = sql.replace("{repo_filter}", "AND repo = {repo:String}" if repo else "")
    if intent == "commits_per_day":
        sql = sql.replace(
            "{days_filter}",
            "AND committed_at >= now() - INTERVAL {days:UInt32} DAY" if days else "",
        )
    parameters: dict[str, Any] = {"limit": _limit(decision.params)}
    if repo:
        parameters["repo"] = repo
    if days is not None:
        parameters["days"] = days
    client = clickhouse_connect.get_client(
        host=settings.clickhouse_host,
        port=settings.clickhouse_port,
        username=settings.clickhouse_user,
        password=settings.clickhouse_password,
        database=settings.clickhouse_db,
    )
    result = client.query(sql, parameters=parameters)
    return [dict(zip(result.column_names, row)) for row in result.result_rows]


# ---------------------------------------------------------------------------
# Semantic -> Chroma (top-k vector search + optional metadata filter)
# ---------------------------------------------------------------------------

def _chroma_collection(settings: Settings):
    client = chromadb.HttpClient(host=settings.chroma_host, port=settings.chroma_port)
    return client.get_or_create_collection(
        name=settings.chroma_collection,
        embedding_function=SentenceTransformerEmbeddingFunction(
            model_name="sentence-transformers/all-MiniLM-L6-v2"
        ),
    )


def _semantic_query(settings: Settings, query_text: str, repo: str | None, limit: int) -> list[dict[str, Any]]:
    collection = _chroma_collection(settings)
    result = collection.query(
        query_texts=[query_text],
        n_results=limit,
        where={"repo": repo} if repo else None,
    )
    hits = []
    for sha, meta, dist, doc in zip(
        result["ids"][0], result["metadatas"][0], result["distances"][0], result["documents"][0]
    ):
        hits.append({
            "sha": sha,
            "repo": meta.get("repo"),
            "author": meta.get("author"),
            "committed_at": meta.get("committed_at"),
            "distance": round(dist, 4),
            "excerpt": doc[:500],
        })
    return hits


def execute_semantic(settings: Settings, decision: RouteDecision) -> list[dict[str, Any]]:
    return _semantic_query(
        settings,
        query_text=decision.params.get("query_text") or "",
        repo=decision.params.get("repo"),
        limit=_limit(decision.params),
    )


# ---------------------------------------------------------------------------
# Chained -> Chroma (candidate shas) then Postgres (full detail)
# ---------------------------------------------------------------------------

def execute_chained(settings: Settings, decision: RouteDecision) -> dict[str, Any]:
    candidates = _semantic_query(
        settings,
        query_text=decision.params.get("query_text") or "",
        repo=decision.params.get("repo"),
        limit=min(_limit(decision.params), 10),
    )
    shas = [c["sha"] for c in candidates]
    if not shas:
        return {"candidates": [], "details": []}

    with psycopg2.connect(settings.postgres_pooled_url) as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT c.sha, c.repo, COALESCE(a.username, a.name, a.email) AS author,
                       c.message, c.committed_at, c.url,
                       f.path, f.change_type, f.additions, f.deletions
                FROM commits c
                JOIN authors a ON a.id = c.author_id
                LEFT JOIN files_changed f ON f.commit_sha = c.sha
                WHERE c.sha = ANY(%s)
                ORDER BY c.committed_at DESC
                """,
                (shas,),
            )
            details = [dict(r) for r in cur.fetchall()]
    return {"candidates": candidates, "details": details}


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

EXECUTORS = {
    "relational": execute_relational,
    "analytical": execute_analytical,
    "semantic": execute_semantic,
    "chained": execute_chained,
}


def execute(decision: RouteDecision, settings: Settings) -> Any:
    return EXECUTORS[decision.route](settings, decision)
