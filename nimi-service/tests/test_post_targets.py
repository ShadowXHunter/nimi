"""Posting destinations: thread replies, a separate space via an incoming webhook, or both."""
import io
import json
import urllib.error
from types import SimpleNamespace

import pytest

from nimi_service.config import Settings, _target
from nimi_service.providers.gchat import client as gchat
from nimi_service.providers.gchat.provider import GChatProvider
from nimi_service.rca import (SOURCE_LINE_MAX, format_limit_notice, format_rca, format_reuse,
                              looks_like_nimi_post, source_line, with_source)
from nimi_service.workflow import check_post_settings, destinations, with_source_removed

GCHAT = GChatProvider(Settings())
HOOK = "https://chat.googleapis.com/v1/spaces/AAAAzzzzzzz/messages?key=KEY123&token=TOKEN456"
REQ = {"id": 7, "source_message_id": 42, "source_external_id": "spaces/AAAAxxxxxxx/messages/Th7eAdId123.Th7eAdId123",
       "channel_label": "team-alerts", "source_title": "Order export delayed"}
REPORT = "# RCA\nThe nightly load stopped.\nRCA_FIELDS\norigin: upstream\n"


def test_targets_and_validation():
    assert [destinations(SimpleNamespace(post_target=t)) for t in ("thread", "webhook", "both")] == \
        [["thread"], ["webhook"], ["thread", "webhook"]]
    assert _target(" Both ") == "both"
    with pytest.raises(SystemExit):
        _target("email")
    check_post_settings(Settings(post_enabled=True))                                   # thread: no webhook needed
    check_post_settings(Settings(post_enabled=False, post_target="webhook"))           # dry run: not checked
    with pytest.raises(SystemExit, match="NIMI_POST_WEBHOOK is not set"):
        check_post_settings(Settings(post_enabled=True, post_target="webhook"))
    with pytest.raises(SystemExit) as e:
        check_post_settings(Settings(post_enabled=True, post_target="both", post_webhook="https://example.invalid/x"))
    assert "example.invalid" not in str(e.value)                                        # never echo the URL


def test_webhook_url_must_be_a_chat_webhook():
    assert gchat.webhook_space(HOOK) == "spaces/AAAAzzzzzzz"
    for bad in ("", "http://chat.googleapis.com/v1/spaces/A/messages?key=k&token=t",
                "https://chat.googleapis.com.evil.example/v1/spaces/A/messages?key=k&token=t",
                "https://chat.googleapis.com/v1/spaces/A/messages?key=k"):
        with pytest.raises(ValueError):
            gchat.webhook_space(bad)


def test_webhook_text_links_back_to_the_original_message():
    line = source_line(GCHAT, REQ)
    assert line.startswith("🔔 Original message in team-alerts: <") and line.endswith("|Link>")
    assert len(line) <= SOURCE_LINE_MAX
    assert "https://chat.google.com/room/AAAAxxxxxxx/Th7eAdId123/Th7eAdId123?cls=10" in line
    hook = format_rca(GCHAT, REPORT, [], {}, 4000, source=line).splitlines()
    thread = format_rca(GCHAT, REPORT, [], {}, 4000).splitlines()
    assert hook[:3] == thread[:3] and hook[3] == line and line not in thread      # under the title line
    assert format_reuse(GCHAT, [{"subject": "*", "rca_id": 3}], {}, 4000, source=line).splitlines()[3] == line
    assert format_limit_notice(GCHAT, source=line).splitlines()[1] == line


def test_stored_text_and_outbox_round_trip():
    line = source_line(GCHAT, REQ)
    thread = format_rca(GCHAT, REPORT, [], {}, 4000)
    hook = format_rca(GCHAT, REPORT, [], {}, 4000, source=line)
    assert with_source(thread, line) == hook                      # outbox: stored thread text -> webhook text
    assert with_source_removed({"webhook": hook}) == thread       # webhook-only: store the thread form


def test_nimi_posts_are_recognised():
    assert looks_like_nimi_post(GCHAT, format_rca(GCHAT, REPORT, [], {}, 4000))
    assert not looks_like_nimi_post(GCHAT, "[Alerting] Disk usage high")


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_post_webhook_top_level_retries_429_and_hides_the_url(monkeypatch):
    calls = []

    def fake_urlopen(req, timeout):
        calls.append(req)
        if len(calls) == 1:
            raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {}, None)
        return _Resp(json.dumps({"name": "spaces/AAAAzzzzzzz/messages/T1.M1",
                                 "thread": {"name": "spaces/AAAAzzzzzzz/threads/T1"}}).encode())

    monkeypatch.setattr(gchat.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(gchat.LIMITER, "acquire", lambda kind, space: None)
    monkeypatch.setattr(gchat.time, "sleep", lambda s: None)
    reply, thread = gchat.post_webhook(HOOK, "hello")
    assert (reply, thread) == ("spaces/AAAAzzzzzzz/messages/T1.M1", "spaces/AAAAzzzzzzz/threads/T1")
    assert len(calls) == 2 and "threadKey" not in calls[-1].full_url               # a new top-level message
    gchat.post_webhook(HOOK, "hello", "k-42")                                      # threading only when asked
    assert "threadKey=k-42" in calls[-1].full_url
    assert json.loads(calls[-1].data) == {"text": "hello"}

    def failing(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {}, None)
    monkeypatch.setattr(gchat.urllib.request, "urlopen", failing)
    with pytest.raises(RuntimeError) as e:
        gchat.post_webhook(HOOK, "hello")
    assert "KEY123" not in str(e.value) and "TOKEN456" not in str(e.value) and "400" in str(e.value)


def test_no_post_requests_are_never_posted(monkeypatch):
    from nimi_service import db, workflow
    sent = []
    monkeypatch.setattr(workflow, "_post_one", lambda *a, **k: sent.append(a))
    on = Settings(post_enabled=True)
    workflow.post(on, {**REQ, "provider": "gchat", "requested_by": db.NO_POST}, 1, "rca", {"thread": "x"}, [])
    assert sent == []
    workflow.post(on, {**REQ, "provider": "gchat", "requested_by": "manual"}, 1, "rca", {"thread": "x"}, [])
    assert len(sent) == 1


def test_outbox_query_skips_no_post_requests():
    from sqlalchemy.dialects import postgresql
    from nimi_service import db
    captured = {}

    class FakeSession:
        def execute(self, stmt):
            captured["sql"] = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
            return SimpleNamespace(all=lambda: [])
    assert db.unposted_replies(FakeSession(), 6) == []
    assert "requested_by IS DISTINCT FROM 'manual-no-post'" in captured["sql"]


def test_no_post_results_are_never_reuse_candidates():
    from sqlalchemy.dialects import postgresql
    from nimi_service import db
    captured = {}

    class FakeSession:
        def execute(self, stmt):
            captured["sql"] = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
            return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: []))
    from datetime import datetime, timezone
    assert db.reuse_candidates(FakeSession(), 1, datetime(2026, 3, 3, tzinfo=timezone.utc), 7) == []
    assert "requested_by IS DISTINCT FROM 'manual-no-post'" in captured["sql"]
