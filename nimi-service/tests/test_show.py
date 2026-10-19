"""`show`: prints a stored request and its results without starting a run (database faked)."""
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

from nimi_service import __main__ as cli
from nimi_service.config import Settings

T = datetime(2026, 3, 3, 14, 20, tzinfo=timezone.utc)


def result(rid, outcome="rca", report="REPORT", chat="CHAT", request_id=7):
    return SimpleNamespace(id=rid, request_id=request_id, created_at=T, outcome=outcome, cost_usd=0.14,
                           duration_s=120, model="sonnet", origin="upstream", confidence="medium",
                           self_healing="yes", commit_sha="3f2a9c1", report=report, chat_text=chat, error=None)


def fake_db(monkeypatch, results, subjects=(), earlier=None):
    s = SimpleNamespace(get=lambda _model, i: earlier[i])

    @contextmanager
    def session():
        yield s
    req = {"status": "done", "requested_by": "service", "requested_at": T, "attempts": 1,
           "source_external_id": "spaces/A/messages/M", "channel_label": "alerts", "source_title": "Order export delayed",
           "triage_decision": "new", "triage_model": "haiku", "triage_cost_usd": 0.001, "triage_reason": "first time",
           "skip_reason": None}
    monkeypatch.setattr(cli.db, "session", session)
    monkeypatch.setattr(cli.db, "messages_by_external_id", lambda _s, ref, _p: [SimpleNamespace(id=1)])
    monkeypatch.setattr(cli.db, "request_for_message", lambda _s, _m: SimpleNamespace(id=7))
    monkeypatch.setattr(cli.db, "get_request", lambda _s, _r: req)
    monkeypatch.setattr(cli.db, "subjects_for", lambda _s, _r: list(subjects))
    monkeypatch.setattr(cli.db, "posts_for", lambda _s, _r: [])
    monkeypatch.setattr(cli.db, "results_for", lambda _s, _r: results)


def test_latest_report_by_default_and_all_attempts_on_request(monkeypatch, capsys):
    fake_db(monkeypatch, [result(9, report="NEW"), result(8, outcome="error", report=None)])
    cli.cmd_show(Settings(), "spaces/A/messages/M", None, chat=False, show_all=False)
    out = capsys.readouterr().out
    assert "request 7: status=done" in out and "NEW" in out and "result 8" not in out and "1 earlier attempt" in out
    cli.cmd_show(Settings(), "7", None, chat=True, show_all=True)
    out = capsys.readouterr().out
    assert "CHAT" in out and "result 8" in out and "outcome=error" in out


def test_reused_request_shows_the_rca_it_reused(monkeypatch, capsys):
    subj = [SimpleNamespace(subject="orders", reused_result_id=3)]
    fake_db(monkeypatch, [], subj, earlier={3: result(3, report="EARLIER", request_id=2)})
    cli.cmd_show(Settings(), "7", None, chat=False, show_all=False)
    out = capsys.readouterr().out
    assert "orders (reused result 3)" in out and "reused result (request 2)" in out and "EARLIER" in out


def test_render_builds_the_chat_message_now_without_the_length_limit(monkeypatch, capsys):
    import json
    from nimi_service import projects, workflow
    chat = {"title": "Order export delayed", "tldr": "The export ran late.", "cause": {"points": ["Queue backlog."]},
            "also": ["x" * 6000]}
    report = f"RCA_CHAT\n{json.dumps(chat)}\nEND_RCA_CHAT\nRCA_FIELDS\nconfidence: low\n"
    res = result(9, report=report, chat="POSTED (shortened)")
    fake_db(monkeypatch, [res])
    req = cli.db.get_request(None, 7)
    req.update(provider="gchat", input_text="alert")
    monkeypatch.setattr(cli.db, "reply_ids_for_results", lambda _s, _ids, _dest: {})
    monkeypatch.setattr(cli.db, "earlier_rcas", lambda _s, _ids: {})
    monkeypatch.setattr(workflow, "citation_links", lambda *_a: {})
    monkeypatch.setattr(projects, "load", lambda _settings: projects.Registry())
    cli.cmd_show(Settings(), "7", None, chat=False, show_all=False, render=True)
    out = capsys.readouterr().out
    assert "rendered now" in out and "x" * 6000 in out and "POSTED" not in out
    assert "*RCA: Order export delayed*" in out and "shortened" not in out.split("not the posted copy")[1]
