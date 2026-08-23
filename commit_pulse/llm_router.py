from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from openai import OpenAI

from .config import Settings

logger = logging.getLogger(__name__)

ROUTES = ("relational", "analytical", "semantic", "chained")

# Whitelisted query templates per route. The LLM may ONLY pick an intent and
# fill its params — it never writes SQL. See query_executors.py.
ROUTE_INTENTS = {
    "relational": ("commits_by_file", "commits_by_repo", "commits_by_author", "commit_detail", "list_tables"),
    "analytical": ("commits_per_day", "most_active_authors", "churn_by_repo"),
    "semantic": ("semantic_search",),
    "chained": ("chained_search",),
}

_ROUTER_SYSTEM = """You are a query router for a commit analytics system. Classify the user question into exactly one route:

- relational: specific lookup — "who changed file X", "commits in repo Y", "commits by author Z", "show commit <sha>", "what tables exist in Neon"
- analytical: aggregates/trends/counts — "commits per day", "most active author", "code churn per repo"
- semantic: meaning-based/fuzzy — "commits related to auth refactoring", "changes about error handling"
- chained: needs semantic search FIRST to find candidate commits, then relational detail — e.g. "explain what changed in commits about rate limiting"

Respond ONLY with valid JSON:
{"route": "<route>", "reasoning": "<short>", "params": {"intent": "<intent>", ...}}

Allowed intents per route:
- relational: commits_by_file (needs file_path), commits_by_repo (needs repo), commits_by_author (needs author), commit_detail (needs sha), list_tables (no params; for database table/schema questions)
- analytical: commits_per_day (optional repo, days), most_active_authors (optional repo), churn_by_repo (optional repo)
- semantic: semantic_search (uses the question as query_text; optional repo filter)
- chained: chained_search (uses the question as query_text; optional repo filter)

Params you may extract: repo (e.g. "owner/name"), file_path, author, sha, limit (int, default 10).
days: include ONLY if the question mentions a time range (e.g. "last week", "this month", "in the last 90 days"). Otherwise OMIT days entirely — queries without days show the full history.
Always include the original question as params.query_text."""


@dataclass
class RouteDecision:
    route: str
    reasoning: str
    params: dict[str, Any] = field(default_factory=dict)


def _client(settings: Settings) -> OpenAI:
    return OpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url)


def _parse_json_object(content: str) -> dict[str, Any]:
    """Extract the first JSON object from an LLM response, tolerating
    markdown code fences and surrounding prose."""
    text = content.strip()
    if text.startswith("```"):
        # strip ```json ... ``` fences
        text = text.split("\n", 1)[-1] if "\n" in text else text
        text = text.removesuffix("```").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass
    return {}


def route_question(question: str, settings: Settings) -> RouteDecision:
    """LLM call #1: classify a user question into a backend route + params."""
    resp = _client(settings).chat.completions.create(
        model=settings.llm_model,
        messages=[
            {"role": "system", "content": _ROUTER_SYSTEM},
            {"role": "user", "content": question},
        ],
        temperature=0.0,
        response_format={"type": "json_object"},
    )
    content = resp.choices[0].message.content or "{}"
    parsed = _parse_json_object(content)
    if not parsed:
        logger.warning("router returned non-JSON, falling back to semantic: %r", content[:200])

    route = parsed.get("route", "semantic")
    if route not in ROUTES:
        logger.warning("router returned unknown route %r, falling back to semantic", route)
        route = "semantic"

    params = parsed.get("params") or {}
    params.setdefault("query_text", question)

    # Some short Indonesian content questions are consistently interpreted by
    # the model as a literal file lookup. Route them to vector search instead.
    question_lower = question.lower()
    if route == "relational" and params.get("intent") == "commits_by_file":
        content_words = ("apa isi", "isi dari", "content", "terkait", "related to")
        if any(word in question_lower for word in content_words):
            route = "semantic"
            params["intent"] = "semantic_search"
            params.pop("file_path", None)
    # Enforce whitelist: intent must belong to the chosen route.
    intent = params.get("intent")
    if intent not in ROUTE_INTENTS[route]:
        params["intent"] = ROUTE_INTENTS[route][0]

    return RouteDecision(route=route, reasoning=parsed.get("reasoning", ""), params=params)


def summarize(question: str, route: str, results: Any, settings: Settings) -> str:
    """LLM call #2: turn query results into a natural-language answer."""
    resp = _client(settings).chat.completions.create(
        model=settings.llm_model,
        messages=[
            {"role": "system", "content": "You are a helpful assistant answering questions about a git commit history dataset. Answer concisely based ONLY on the provided data; say so if the data is empty or insufficient."},
            {"role": "user", "content": f"Question: {question}\nQuery route: {route}\nData: {json.dumps(results, default=str)[:6000]}"},
        ],
        temperature=0.3,
    )
    return resp.choices[0].message.content or ""
