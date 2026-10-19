"""Exclusion rules: matching semantics and expiry parsing (no database), and the triage guidance."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from nimi_service import exclusions
from nimi_service.triage import PROMPT

MSG = {"signature_key": "alert|stock sync reminder", "channel_id": 1, "sender_external_id": "users/1",
       "title": "Stock Sync Reminder", "status": "Alerting", "text": "[Alerting] Stock Sync Reminder"}


def rule(**kw):
    base = dict(match_signature_key=None, match_channel_id=None, match_sender_external_id=None,
                match_title=None, match_status=None, match_text=None)
    return SimpleNamespace(**{**base, **kw})


def test_every_set_field_must_match():
    assert exclusions.matches(rule(match_signature_key="alert|stock sync reminder"), MSG)
    assert not exclusions.matches(rule(match_signature_key="alert|other"), MSG)
    assert exclusions.matches(rule(match_signature_key="alert|stock sync reminder", match_channel_id=1), MSG)
    assert not exclusions.matches(rule(match_signature_key="alert|stock sync reminder", match_channel_id=2), MSG)
    assert exclusions.matches(rule(match_title="stock sync", match_status="alerting"), MSG)   # case-insensitive
    assert not exclusions.matches(rule(match_title="stock sync", match_status="ok"), MSG)
    assert not exclusions.matches(rule(match_sender_external_id="users/2"), MSG)


def test_text_matches_anywhere_in_the_message():
    mem = {**MSG, "title": "Memory usage high",
           "text": "[Alerting] Memory usage high\nhost: batch-worker-3 (staging)\nvalue: 93%"}
    assert exclusions.matches(rule(match_text="BATCH-WORKER", match_title="memory usage"), mem)
    assert not exclusions.matches(rule(match_title="batch-worker"), mem)          # the title lacks the host
    assert not exclusions.matches(rule(match_text="api-server"), mem)
    assert not exclusions.matches(rule(match_text="batch-worker"), {**mem, "text": None})


def test_title_falls_back_to_first_line_when_unparsed():
    unparsed = {**MSG, "title": None, "text": "\n Daily report ready\nmore"}
    assert exclusions.matches(rule(match_title="daily report"), unparsed)


def test_parse_expiry():
    now = datetime(2026, 10, 19, tzinfo=timezone.utc)
    assert exclusions.parse_expiry("7d", now) == now + timedelta(days=7)
    assert exclusions.parse_expiry("12h", now) == now + timedelta(hours=12)
    assert exclusions.parse_expiry("2026-11-01T00:00", now) == datetime(2026, 11, 1, tzinfo=timezone.utc)
    assert exclusions.parse_expiry(None, now) is None


def test_triage_declines_informational_messages():
    assert "reports no failure or anomaly" in PROMPT and "ticket reminders" in PROMPT
    assert "When unsure whether it is informational, it is not." in PROMPT


def test_triage_declines_test_messages_but_not_real_failures_mentioning_test():
    assert 'A test message needs no RCA' in PROMPT and "When unsure, it is not a test." in PROMPT
    assert "The sender, webhook, bot or space name is never evidence" in PROMPT
