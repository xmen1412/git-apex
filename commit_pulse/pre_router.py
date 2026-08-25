"""Rule-based routing for unambiguous questions, tried before the LLM.

Each rule is (compiled_pattern, route, intent, param_extractor). First match
wins. Anything unmatched falls through to the LLM router.

A rule that matches but can't extract its required param declines (returns
None for that rule) so the LLM gets a chance to route it instead.
"""
from __future__ import annotations

import re
from typing import Any, Callable

from .llm_router import RouteDecision

_SHA = re.compile(r"\b([0-9a-f]{7,40})\b", re.I)
_FILE = re.compile(r"\b([\w./-]+\.\w{1,6})\b")


def _file_param(m: re.Match, q: str) -> dict[str, Any] | None:
    f = _FILE.search(q)
    return {"file_path": f.group(1)} if f else None


def _sha_param(m: re.Match, q: str) -> dict[str, Any] | None:
    s = _SHA.search(q)
    return {"sha": s.group(1)} if s else None


RULES: list[tuple[re.Pattern, str, str, Callable | None]] = [
    # schema questions — no params, zero ambiguity
    (re.compile(r"\b(what|which|list)\s+tables?\b|\btabel\s+apa\b|\bdaftar\s+tabel\b", re.I),
     "relational", "list_tables", None),

    # "who changed <file>" / "siapa (yang) mengubah <file>"
    (re.compile(r"\b(who\s+(changed|modified|touched)|siapa\s+(yang\s+)?(mengubah|ubah|edit))\b", re.I),
     "relational", "commits_by_file", _file_param),

    # explicit content questions -> semantic (replaces the ID-only override)
    (re.compile(r"\b(related to|about)\b|\b(terkait|tentang|apa\s+isi|mengenai)\b", re.I),
     "semantic", "semantic_search", lambda m, q: {"query_text": q}),
]


def pre_route(question: str) -> RouteDecision | None:
    """Return a RouteDecision for unambiguous questions, else None."""
    for pattern, route, intent, extractor in RULES:
        m = pattern.search(question)
        if not m:
            continue
        params: dict[str, Any] = {"intent": intent}
        if extractor is not None:
            extra = extractor(m, question)
            if extra is None:
                # Pattern matched but the param isn't there — let the LLM try.
                continue
            params.update(extra)
        return RouteDecision(
            route=route, params=params, reasoning=f"pre-router rule: {pattern.pattern[:40]}"
        )
    return None
