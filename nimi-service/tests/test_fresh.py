"""`rca --fresh`: triage only names the subjects; the result is always a new RCA (database and model faked)."""
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from nimi_service import workflow
from nimi_service.config import Settings

T = datetime(2026, 3, 3, 14, 20, tzinfo=timezone.utc)


@pytest.fixture
def fake(monkeypatch):
    state = {"triage_args": None, "subjects": None, "transition": None, "excluded_checked": False}
    req = {"id": 7, "status": "triage", "source_message_id": 42, "signature_id": 5, "message_created_at": T,
           "requested_at": T, "input_text": "[Alerting] Order export delayed", "skip_reason": None}

    @contextmanager
    def session():
        yield SimpleNamespace()
    monkeypatch.setattr(workflow.db, "session", session)
    monkeypatch.setattr(workflow.db, "get_request", lambda _s, _r: dict(req))
    monkeypatch.setattr(workflow.db, "now", lambda _s: T)
    monkeypatch.setattr(workflow.db, "parser_status", lambda _s, _m: "Alerting")
    monkeypatch.setattr(workflow.db, "signature_history", lambda *_a: ["earlier message"])
    monkeypatch.setattr(workflow.db, "reuse_candidates", lambda *_a: [{"result_id": 3}])
    monkeypatch.setattr(workflow.exclusions, "message_fields", lambda _s, _m: {})

    def match(*_a):
        state["excluded_checked"] = True
        return SimpleNamespace(id=1, reason="known noise")
    monkeypatch.setattr(workflow.exclusions, "match", match)
    monkeypatch.setattr(workflow.exclusions, "skip_reason", lambda rule: f"excluded by rule #{rule.id}")
    monkeypatch.setattr(workflow, "scan_input", lambda _settings, _req: [])
    monkeypatch.setattr(workflow.db, "set_subjects", lambda _s, _r, subjects: state.update(subjects=subjects))

    def transition(_s, _r, frm, to, **fields):
        state["transition"] = (frm, to, fields)
        return True
    monkeypatch.setattr(workflow.db, "transition", transition)
    monkeypatch.setattr(workflow, "post_reuse", lambda *_a: state.update(posted_reuse=True))
    return state


def answer(monkeypatch, state, **out):
    base = {"decision": "new", "reuse": [], "new_subjects": [], "reason": "r", "model": "haiku",
            "cost_usd": 0.001, "raw": {}}

    def triage(_settings, _req, history, candidates):
        state["triage_args"] = (history, candidates)
        return {**base, **out}
    monkeypatch.setattr(workflow, "triage", triage)


def test_fresh_keeps_the_subjects_and_never_reuses_or_excludes(monkeypatch, fake):
    answer(monkeypatch, fake, decision="partial", new_subjects=["orders"],
           reuse=[{"subject": "order_items", "rca_id": 3}])
    workflow.run_triage(Settings(), 7, fresh=True)
    assert fake["triage_args"] == ([], []) and not fake["excluded_checked"]         # no history, candidates, rules
    assert fake["subjects"] == [{"subject": "orders", "decision": "new"}]
    frm, to, fields = fake["transition"]
    assert (frm, to, fields["triage_decision"]) == ("triage", "queued", "new")
    assert fields["triage_reason"].startswith("manual --fresh (triage: partial")
    assert "posted_reuse" not in fake


def test_fresh_overrides_not_applicable_and_falls_back_to_star(monkeypatch, fake):
    answer(monkeypatch, fake, decision="not_applicable", reason="informational: reminder")
    workflow.run_triage(Settings(), 7, fresh=True)
    assert fake["subjects"] == [{"subject": "*", "decision": "new"}]
    frm, to, fields = fake["transition"]
    assert to == "queued" and "not_applicable — informational: reminder" in fields["triage_reason"]


def test_normal_triage_still_checks_rules_and_offers_candidates(monkeypatch, fake):
    answer(monkeypatch, fake, new_subjects=["orders"])
    workflow.run_triage(Settings(), 7)
    assert fake["excluded_checked"] and fake["transition"][1] == "skipped"          # the rule matched first
