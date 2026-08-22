from datetime import timezone

from commit_pulse.models import _parse_ts, parse_push_event, to_push_payload


def test_parse_push_event():
    payload = {
        "repository": {"full_name": "owner/repo"},
        "commits": [{
            "id": "abc123",
            "message": "fix: thing",
            "timestamp": "2026-08-22T13:00:00Z",
            "url": "https://github.com/owner/repo/commit/abc123",
            "author": {"name": "Dev", "email": "dev@example.com", "username": "dev"},
            "added": ["src/new.py"],
            "modified": ["src/old.py"],
            "removed": [],
        }],
    }
    events = parse_push_event(payload)
    assert len(events) == 1
    ev = events[0]
    assert ev.sha == "abc123"
    assert ev.repo == "owner/repo"
    assert ev.source == "webhook"
    assert len(ev.files) == 2
    assert ev.files[0].change_type == "added"
    assert ev.files[1].change_type == "modified"


def test_to_push_payload_roundtrip():
    payload = to_push_payload(
        sha="def456",
        repo_full_name="owner/repo",
        message="feat: new",
        author={"name": "Dev", "email": "dev@example.com", "username": "dev"},
        committed_at="2026-08-22T13:00:00Z",
        url="https://github.com/owner/repo/commit/def456",
        added=["README.md"],
        modified=[],
        removed=[],
    )
    events = parse_push_event(payload, source=payload.get("_source", "webhook"))
    assert len(events) == 1
    assert events[0].sha == "def456"
    assert events[0].source == "backfill"
    assert events[0].files[0].path == "README.md"


def test_parse_ts_normalizes_to_utc():
    # "Z" suffix
    assert _parse_ts("2026-08-22T13:00:00Z").tzinfo == timezone.utc
    # explicit offset gets converted to UTC
    dt = _parse_ts("2026-08-22T20:00:00+07:00")
    assert dt.tzinfo == timezone.utc
    assert dt.isoformat() == "2026-08-22T13:00:00+00:00"
    # naive input assumed UTC
    dt = _parse_ts("2026-08-22T13:00:00")
    assert dt.tzinfo == timezone.utc
    # empty input falls back to aware now()
    assert _parse_ts(None).tzinfo == timezone.utc
    assert _parse_ts("").tzinfo == timezone.utc
