"""Offline eval — deterministic layer only (rules, param extraction, validation).

Runs in CI, free. Does not call the LLM.
"""
from __future__ import annotations

import pytest

from commit_pulse.llm_router import ROUTE_INTENTS, RouteDecision
from commit_pulse.query_executors import NeedsClarification, REQUIRED_PARAMS, validate_params


@pytest.mark.parametrize("intent,params,missing", [
    ("commits_by_file", {"intent": "commits_by_file"}, ["file_path"]),
    ("commits_by_file", {"intent": "commits_by_file", "file_path": "  "}, ["file_path"]),
    ("commit_detail", {"intent": "commit_detail"}, ["sha"]),
    ("commits_per_day", {"intent": "commits_per_day"}, []),  # no required params
])
def test_param_validation(intent, params, missing):
    decision = RouteDecision(route="relational", params=params, reasoning="")
    if missing:
        with pytest.raises(NeedsClarification) as e:
            validate_params(decision)
        assert e.value.missing == missing
    else:
        validate_params(decision)


def test_every_intent_has_a_required_params_entry():
    """A new intent without an entry silently gets no validation."""
    for intents in ROUTE_INTENTS.values():
        for intent in intents:
            assert intent in REQUIRED_PARAMS, f"{intent} missing from REQUIRED_PARAMS"
