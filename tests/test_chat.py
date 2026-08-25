from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from commit_pulse.config import Settings
from commit_pulse.llm_router import RouteDecision, route_question
from commit_pulse.query_executors import execute


def make_settings() -> Settings:
    return Settings(
        kafka_bootstrap_servers="localhost:9092",
        kafka_topic="raw-commits",
        minio_endpoint="http://localhost:9000",
        minio_root_user="u",
        minio_root_password="p",
        minio_bucket="raw-commits",
        postgres_pooled_url="postgresql://u:p@host/db",
        clickhouse_host="localhost",
        clickhouse_port=8123,
        clickhouse_user="u",
        clickhouse_password="p",
        clickhouse_db="commitpulse",
        chroma_host="localhost",
        chroma_port=8001,
        chroma_collection="commits",
        github_token="",
        github_webhook_secret="",
        github_webhook_url="",
        llm_base_url="https://llm.example/v1",
        llm_api_key="sk-test",
        llm_model="test-model",
    )


def mock_llm_response(payload: dict):
    msg = MagicMock()
    msg.content = json.dumps(payload)
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


# --- Router -----------------------------------------------------------------

def test_route_question_relational():
    payload = {"route": "relational", "reasoning": "file lookup",
               "params": {"intent": "commits_by_file", "file_path": "app.py"}}
    with patch("commit_pulse.llm_router._client") as mk:
        mk.return_value.chat.completions.create.return_value = mock_llm_response(payload)
        d = route_question("who changed app.py?", make_settings())
    assert d.route == "relational"
    assert d.params["intent"] == "commits_by_file"
    assert d.params["query_text"] == "who changed app.py?"


def test_route_question_rejects_intent_outside_whitelist():
    payload = {"route": "relational", "reasoning": "x",
               "params": {"intent": "commits_per_day"}}  # analytical intent in relational route
    with patch("commit_pulse.llm_router._client") as mk:
        mk.return_value.chat.completions.create.return_value = mock_llm_response(payload)
        d = route_question("q", make_settings())
    assert d.params["intent"] == "commits_by_file"  # forced back to whitelist default


def test_route_question_fallback_on_bad_json():
    with patch("commit_pulse.llm_router._client") as mk:
        resp = MagicMock()
        resp.choices = [MagicMock(message=MagicMock(content="not json at all"))]
        mk.return_value.chat.completions.create.return_value = resp
        d = route_question("q", make_settings())
    assert d.route == "semantic"


def test_route_question_strips_markdown_code_fence():
    fenced = '```json\n{"route": "analytical", "reasoning": "aggregate", "params": {"intent": "commits_per_day"}}\n```'
    with patch("commit_pulse.llm_router._client") as mk:
        resp = MagicMock()
        resp.choices = [MagicMock(message=MagicMock(content=fenced))]
        mk.return_value.chat.completions.create.return_value = resp
        d = route_question("commits per day?", make_settings())
    assert d.route == "analytical"
    assert d.params["intent"] == "commits_per_day"


def test_route_question_fallback_on_unknown_route():
    payload = {"route": "DROP TABLE commits", "reasoning": "x", "params": {}}
    with patch("commit_pulse.llm_router._client") as mk:
        mk.return_value.chat.completions.create.return_value = mock_llm_response(payload)
        d = route_question("q", make_settings())
    assert d.route == "semantic"


def test_route_question_content_lookup_uses_semantic_search():
    from commit_pulse.pre_router import pre_route

    d = pre_route("apa isi hello-world")
    assert d is not None
    assert d.route == "semantic"
    assert d.params["intent"] == "semantic_search"
    assert "file_path" not in d.params


# --- Executors (1 per route + chained) --------------------------------------

def _pg_rows(rows):
    cursor = MagicMock()
    cursor.fetchall.return_value = rows
    cursor.__enter__ = lambda s: s
    cursor.__exit__ = MagicMock(return_value=False)
    conn = MagicMock()
    conn.cursor.return_value = cursor
    conn.__enter__ = lambda s: s
    conn.__exit__ = MagicMock(return_value=False)
    return conn


def test_execute_relational():
    settings = make_settings()
    decision = RouteDecision("relational", "", {"intent": "commits_by_file", "file_path": "app.py"})
    conn = _pg_rows([{"sha": "abc", "repo": "o/r", "author": "xmen"}])
    with patch("commit_pulse.query_executors.psycopg2.connect", return_value=conn) as pg:
        rows = execute(decision, settings)
    assert rows == [{"sha": "abc", "repo": "o/r", "author": "xmen"}]
    args = pg.return_value.cursor.return_value.execute.call_args[0]
    assert "files_changed" in args[0]
    assert args[1] == ("app.py", "%/app.py", 10)  # parameterized, not string-interpolated


def test_execute_analytical():
    settings = make_settings()
    decision = RouteDecision("analytical", "", {"intent": "commits_per_day", "days": 7})
    result = MagicMock()
    result.column_names = ["day", "repo", "commits"]
    result.result_rows = [("2026-08-22", "o/r", 3)]
    client = MagicMock()
    client.query.return_value = result
    with patch("commit_pulse.query_executors.clickhouse_connect.get_client", return_value=client):
        rows = execute(decision, settings)
    assert rows == [{"day": "2026-08-22", "repo": "o/r", "commits": 3}]
    params = client.query.call_args.kwargs["parameters"]
    assert params["days"] == 7 and params["limit"] == 10
    sql = client.query.call_args.args[0]
    assert "INTERVAL {days:UInt32} DAY" in sql  # time filter applied when days given


def test_execute_analytical_no_days_means_full_history():
    settings = make_settings()
    decision = RouteDecision("analytical", "", {"intent": "commits_per_day"})
    result = MagicMock()
    result.column_names = ["day", "repo", "commits"]
    result.result_rows = [("2011-01-26", "o/r", 1), ("2026-08-22", "o/r", 1)]
    client = MagicMock()
    client.query.return_value = result
    with patch("commit_pulse.query_executors.clickhouse_connect.get_client", return_value=client):
        rows = execute(decision, settings)
    assert len(rows) == 2  # old commits included, not filtered out
    params = client.query.call_args.kwargs["parameters"]
    assert "days" not in params  # no time filter sent
    sql = client.query.call_args.args[0]
    assert "INTERVAL" not in sql  # no time window in SQL


def test_execute_semantic():
    settings = make_settings()
    decision = RouteDecision("semantic", "", {"intent": "semantic_search", "query_text": "auth fix"})
    collection = MagicMock()
    collection.query.return_value = {
        "ids": [["abc123"]],
        "metadatas": [[{"repo": "o/r", "author": "xmen", "committed_at": "2026-01-01T00:00:00+00:00", "sha": "abc123"}]],
        "distances": [[0.42]],
        "documents": [["fix auth bug ..."]],
    }
    with patch("commit_pulse.query_executors._chroma_collection", return_value=collection):
        hits = execute(decision, settings)
    assert hits[0]["sha"] == "abc123"
    assert hits[0]["distance"] == 0.42
    assert collection.query.call_args.kwargs["n_results"] == 10


def test_execute_chained_semantic_then_postgres():
    settings = make_settings()
    decision = RouteDecision("chained", "", {"intent": "chained_search", "query_text": "rate limiting"})
    candidates = [{"sha": "abc123", "repo": "o/r", "distance": 0.3}]
    conn = _pg_rows([{"sha": "abc123", "message": "add rate limit", "path": "api.py"}])
    with patch("commit_pulse.query_executors._semantic_query", return_value=candidates), \
         patch("commit_pulse.query_executors.psycopg2.connect", return_value=conn) as pg:
        out = execute(decision, settings)
    assert out["candidates"] == candidates
    assert out["details"][0]["sha"] == "abc123"
    assert pg.return_value.cursor.return_value.execute.call_args[0][1] == (["abc123"],)


def test_execute_chained_empty_candidates_skips_postgres():
    settings = make_settings()
    decision = RouteDecision("chained", "", {"intent": "chained_search", "query_text": "nothing"})
    with patch("commit_pulse.query_executors._semantic_query", return_value=[]), \
         patch("commit_pulse.query_executors.psycopg2.connect") as pg:
        out = execute(decision, settings)
    assert out == {"candidates": [], "details": []}
    pg.assert_not_called()


def test_limit_is_clamped():
    from commit_pulse.query_executors import _limit
    assert _limit({"limit": 99999}) == 100
    assert _limit({"limit": -5}) == 1
    assert _limit({"limit": "junk"}) == 10
