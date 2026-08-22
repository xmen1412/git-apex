from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from .config import Settings

logger = logging.getLogger(__name__)


@dataclass
class RouteDecision:
    route: str
    reasoning: str
    params: dict[str, Any]


def route_question(question: str, settings: Settings) -> RouteDecision:
    """Classify a user question into one of four backend routes using LLM."""
    system = """You are a query router. Classify the user question into exactly one route:
- relational: specific file/commit/author lookup, e.g. "who changed X", "commits in repo Y"
- analytical: aggregates, trends, counts, e.g. "commits per day", "most active author"
- semantic: meaning-based, fuzzy, e.g. "commits related to auth refactoring"
- chained: needs semantic search first, then relational detail

Respond ONLY with valid JSON: {"route": "...", "reasoning": "...", "params": {...}}
Extract params: repo (if mentioned), file_path (if mentioned), days (if time-based), limit (default 10)."""

    with httpx.Client(timeout=30.0) as client:
        resp = client.post(
            f"{settings.llm_base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.llm_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.llm_model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": question},
                ],
                "temperature": 0.0,
                "response_format": {"type": "json_object"},
            },
        )
        resp.raise_for_status()
        data = resp.json()

    content = data["choices"][0]["message"]["content"]
    parsed = json.loads(content)
    return RouteDecision(
        route=parsed.get("route", "relational"),
        reasoning=parsed.get("reasoning", ""),
        params=parsed.get("params", {}),
    )


def summarize(question: str, route: str, results: Any, settings: Settings) -> str:
    """Generate a natural-language answer from query results."""
    system = "You are a helpful assistant. Answer the user question based on the provided data. Be concise."

    with httpx.Client(timeout=30.0) as client:
        resp = client.post(
            f"{settings.llm_base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.llm_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.llm_model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": f"Question: {question}\nRoute: {route}\nData: {json.dumps(results, default=str)[:4000]}"},
                ],
                "temperature": 0.3,
            },
        )
        resp.raise_for_status()
        data = resp.json()

    return data["choices"][0]["message"]["content"]
