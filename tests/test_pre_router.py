from __future__ import annotations

from commit_pulse.pre_router import pre_route


def test_list_tables_rule():
    d = pre_route("what tables are in Neon?")
    assert d is not None
    assert d.route == "relational"
    assert d.params["intent"] == "list_tables"


def test_who_changed_file_rule():
    d = pre_route("who changed auth.py?")
    assert d is not None
    assert d.route == "relational"
    assert d.params["intent"] == "commits_by_file"
    assert d.params["file_path"] == "auth.py"


def test_indonesian_who_changed_file_rule():
    d = pre_route("siapa yang mengubah README.md?")
    assert d is not None
    assert d.params["intent"] == "commits_by_file"
    assert d.params["file_path"] == "README.md"


def test_content_question_routes_semantic():
    d = pre_route("commits related to rate limiting")
    assert d is not None
    assert d.route == "semantic"
    assert d.params["intent"] == "semantic_search"
    assert d.params["query_text"] == "commits related to rate limiting"


def test_rule_without_param_declines():
    # "who changed" matches the file rule but there's no filename — must decline.
    assert pre_route("who changed the codebase?") is None


def test_unmatched_question_declines():
    assert pre_route("what's the weather?") is None
