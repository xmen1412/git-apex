from __future__ import annotations

from commit_pulse.repo_watcher import build_hook_payload, find_matching_hook, should_skip_repo


def _repo(**overrides):
    base = {
        "full_name": "someone/repo",
        "archived": False,
        "disabled": False,
        "fork": False,
        "permissions": {"admin": True},
    }
    base.update(overrides)
    return base


def test_should_skip_repo_ok():
    assert should_skip_repo(_repo()) is None


def test_should_skip_repo_archived():
    assert should_skip_repo(_repo(archived=True)) == "archived"


def test_should_skip_repo_disabled():
    assert should_skip_repo(_repo(disabled=True)) == "disabled"


def test_should_skip_repo_fork_default():
    reason = should_skip_repo(_repo(fork=True))
    assert reason is not None
    assert "fork" in reason


def test_should_skip_repo_fork_included():
    assert should_skip_repo(_repo(fork=True), include_forks=True) is None


def test_should_skip_repo_no_admin():
    reason = should_skip_repo(_repo(permissions={"admin": False}))
    assert reason is not None
    assert "admin" in reason


def test_should_skip_repo_missing_permissions_key():
    reason = should_skip_repo(_repo(permissions={}))
    assert reason is not None
    assert "admin" in reason


def test_find_matching_hook_found():
    hooks = [
        {"config": {"url": "https://smee.io/other"}},
        {"config": {"url": "https://smee.io/mine"}},
    ]
    match = find_matching_hook(hooks, "https://smee.io/mine")
    assert match == hooks[1]


def test_find_matching_hook_not_found():
    hooks = [{"config": {"url": "https://smee.io/other"}}]
    assert find_matching_hook(hooks, "https://smee.io/mine") is None


def test_find_matching_hook_empty():
    assert find_matching_hook([], "https://smee.io/mine") is None


def test_find_matching_hook_missing_config():
    hooks = [{"name": "web"}]
    assert find_matching_hook(hooks, "https://smee.io/mine") is None


def test_build_hook_payload():
    payload = build_hook_payload("https://smee.io/mine", "s3cr3t")
    assert payload["events"] == ["push"]
    assert payload["active"] is True
    assert payload["config"]["url"] == "https://smee.io/mine"
    assert payload["config"]["secret"] == "s3cr3t"
    assert payload["config"]["content_type"] == "json"
