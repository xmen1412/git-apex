"""Live routing eval — calls the real LLM, so it's skipped unless opted in.

Run: RUN_LIVE_EVAL=1 docker compose run --rm --no-deps webhook python -m pytest tests/test_eval_routing.py -q
"""
from __future__ import annotations

import os

import pytest

from commit_pulse.config import get_settings
from commit_pulse.llm_router import route_question

pytestmark = pytest.mark.skipif(
    not os.getenv("RUN_LIVE_EVAL"), reason="set RUN_LIVE_EVAL=1 (costs LLM calls)"
)

CASES = [
    # (question, expected_route, expected_intent or None)
    ("who changed auth.py?",                    "relational",  "commits_by_file"),
    ("siapa yang mengubah README?",             "relational",  "commits_by_file"),
    ("show commits in xmen1412/git-apex",       "relational",  "commits_by_repo"),
    ("commit detail for a1b2c3d",               "relational",  "commit_detail"),
    ("what tables are in Neon?",                "relational",  "list_tables"),
    ("tabel apa saja di database?",             "relational",  "list_tables"),
    ("commits per day",                         "analytical",  "commits_per_day"),
    ("berapa commit per hari?",                 "analytical",  "commits_per_day"),
    ("most active author",                      "analytical",  "most_active_authors"),
    ("author paling aktif minggu ini",          "analytical",  "most_active_authors"),
    ("which repo has the most churn?",          "analytical",  "churn_by_repo"),
    ("commits related to rate limiting",        "semantic",    None),
    ("apa isi commit terkait autentikasi?",     "semantic",    None),
    ("commit yang terkait caching",             "semantic",    None),
    ("explain what changed in commits about auth", "chained",  None),
    ("jelaskan perubahan pada commit soal database", "chained", None),
]


@pytest.mark.parametrize("question,route,intent", CASES)
def test_routing(question, route, intent):
    d = route_question(question, get_settings())
    assert d.route == route, f"{question!r} -> {d.route} ({d.reasoning})"
    if intent:
        assert d.params.get("intent") == intent


def test_days_extracted_when_mentioned():
    d = route_question("author paling aktif 7 hari terakhir", get_settings())
    assert d.params.get("days")  # must not be silently dropped
