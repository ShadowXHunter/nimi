from datetime import datetime, timedelta, timezone

import pytest

from nimi_service.config import Settings
from nimi_service.ingest import should_request_rca
from nimi_service.parse import looks_resolved, parse_message as _parse_message, parse_text
from nimi_service.providers.gchat.provider import GChatProvider, emoji_key, reaction_summary
from nimi_service.providers.gchat.render import render_message
from nimi_service.rca import build_input, format_reuse, parse_output
from nimi_service.triage import validate

GCHAT = GChatProvider(Settings())


def parse_message(raw: dict) -> dict:
    """Tests use raw Google Chat payloads: normalize through the provider, then parse."""
    return _parse_message(GCHAT.normalize(raw))

LOG_TEXT = """```
[2026-01-15 03:12:44+0000] [order-worker-deployment-7d9f8c6b5d-x2k4p] [loader-1] [shop/payments/card_charges] [0f1e2d3c-4b5a-4c6d-8e7f-9a0b1c2d3e4f] [ERROR] - com.example.shop.loader.LoadRunner
 Loading shop/payments/card_charges failed com.example.shop.loader.LoadException: the warehouse rejected the batch
    at com.example.shop.loader.MergeStage.run(MergeStage.java:88)
```"""

API_LOG_TEXT = """```
[2026-01-15 11:42:30+0000] [catalog-api-deployment-6c8b9f7d4e-q7w2z] [req-77] [ERROR] - com.example.shop.api.ErrorHandler
 Request failed
  com.example.shop.catalog.DuplicateItemException: item sku-9 is listed twice
```"""


def msg(text=None, cards=None, name="spaces/S/messages/A.A"):
    m = {"name": name, "createTime": "2026-01-15T10:04:31.123456Z", "thread": {"name": "spaces/S/threads/A"},
         "sender": {"name": "users/123", "type": "BOT", "displayName": "Monitoring"}}
    if text is not None:
        m["text"] = text
    if cards is not None:
        m["cardsV2"] = cards
    return m


# --- parsing / signatures -------------------------------------------------------

def test_alerting_and_ok_share_signature():
    a = parse_message(msg("[Alerting] order export delayed alert"))
    ok = parse_message(msg("[OK] order export delayed alert"))
    assert a["status"] == "Alerting" and ok["status"] == "Ok" and looks_resolved(ok["status"])
    assert a["signature_key"] == ok["signature_key"] == "alert|order export delayed alert"


def test_grafana_service_bracket():
    r = parse_message(msg("[Alerting] [shop order-worker prod] High CPU alert"))
    assert r["service"] == "shop order-worker prod"
    assert r["title"] == "High CPU alert"
    other = parse_message(msg("[Alerting] [shop catalog-api prod] High CPU alert"))
    assert r["signature_key"] != other["signature_key"]          # different services stay separate


def test_quoted_title_and_unknown_status():
    r = parse_message(msg('🚨 [Unknown] "Unshipped orders above threshold" changed status to UNKNOWN'))
    assert r["status"] == "Unknown" and r["title"] == "Unshipped orders above threshold"


def test_log_signature_masks_subjects():
    a = LOG_TEXT
    b = LOG_TEXT.replace("shop/payments/card_charges", "shop/catalog/products")
    assert parse_message(msg(a))["signature_key"] == parse_message(msg(b))["signature_key"]
    assert "card_charges" in parse_message(msg(a))["title"]          # title keeps the item


def test_triggered_with_changed_status():
    r = parse_message(msg('🚨 [Triggered] "Orders missing invoices." changed status to TRIGGERED'))
    assert r["status"] == "Triggered" and "Orders missing invoices" in r["title"] and "changed status" not in r["title"]


def test_pipeline_log():
    r = parse_message(msg(LOG_TEXT))
    assert r["kind"] == "log" and r["service"] == "order-worker"
    assert "LoadException" in r["title"]


def test_api_log_picks_concrete_exception():
    r = parse_message(msg(API_LOG_TEXT))
    assert r["service"] == "catalog-api" and "DuplicateItemException" in r["title"]


def test_log_signature_ignores_timestamps_and_pod_hash():
    other = LOG_TEXT.replace("03:12:44", "04:02:11").replace("7d9f8c6b5d-x2k4p", "4b8e2f6a9c-abcde")
    assert parse_message(msg(LOG_TEXT))["signature_key"] == parse_message(msg(other))["signature_key"]


def test_card_only_message():
    cards = [{"card": {"header": {"title": "[Alerting] x alert"},
                       "sections": [{"widgets": [{"textParagraph": {"text": "error_count<br>42"}}]}]}}]
    r = parse_message(msg(cards=cards))
    assert r["status"] == "Alerting" and r["title"] == "x alert"


def test_parse_any_text():
    r = parse_text("something odd happened\nmore details")
    assert r["kind"] == "other" and r["signature_key"].startswith("other|")


def test_render_layout():
    out = render_message(msg("[Alerting] x"))
    assert out.splitlines()[0] == "[2026-01-15T10:04:31.123456Z] Monitoring"
    assert "  message: spaces/S/messages/A.A" in out and "  thread:  spaces/S/threads/A" in out


# --- request eligibility --------------------------------------------------------

def _row(**kw):
    base = {"sender_type": "bot", "sender_external_id": "users/123", "is_backfill": False,
            "created_at": datetime.now(timezone.utc)}
    base.update(kw)
    return base


def test_should_request_rca():
    from types import SimpleNamespace
    s = Settings()
    now = datetime.now(timezone.utc)
    space = SimpleNamespace(rca_enabled=True)
    assert should_request_rca(s, space, _row(), now) == ("triage", None)
    assert should_request_rca(s, space, _row(is_backfill=True), now) is None
    assert should_request_rca(s, space, _row(sender_type="human"), now) is None
    assert should_request_rca(s, space, _row(sender_type=None), now) is None
    assert should_request_rca(s, SimpleNamespace(rca_enabled=False), _row(), now) is None
    assert should_request_rca(s, space, _row(created_at=now - timedelta(hours=3)), now) == ("triage", None)
    assert should_request_rca(s, space, _row(created_at=now - timedelta(hours=7)), now)[0] == "skipped"   # > 6h
    s.alert_senders = ("users/999",)
    assert should_request_rca(s, space, _row(), now)[0] == "skipped"


# --- triage validation (hard limits) -------------------------------------------

def test_triage_reuse_only_allowed_candidates():
    with pytest.raises(ValueError):
        validate({"decision": "reuse", "reuse": [{"subject": "a", "rca_id": 99}]}, {1, 2})


def test_triage_normalizes_decisions():
    out = validate({"decision": "reuse", "reuse": [{"subject": "a", "rca_id": 1}], "new_subjects": ["b"]}, {1})
    assert out["decision"] == "partial"
    out = validate({"decision": "partial", "reuse": [], "new_subjects": ["b"]}, {1})
    assert out["decision"] == "new" and out["new_subjects"] == ["b"]
    out = validate({"decision": "not_applicable", "reuse": [{"subject": "a", "rca_id": 1}]}, {1})
    assert out == {"decision": "not_applicable", "reuse": [], "new_subjects": [], "reason": "", "sensitive_types": []}
    out = validate({"decision": "new"}, set())
    assert out["new_subjects"] == ["*"]


def test_triage_rejects_garbage():
    for bad in ["text", {"decision": "maybe"}, {"decision": "reuse", "reuse": [{"subject": "", "rca_id": 1}]},
                {"decision": "reuse", "reuse": [{"subject": "a", "rca_id": True}]}]:
        with pytest.raises(ValueError):
            validate(bad, {1})


# --- skill output contract ------------------------------------------------------

def test_parse_output_fields():
    report = "# RCA\n...\nRCA_FIELDS\norigin: upstream\nconfidence: medium\nself_healing: yes\nrepo: shop-rca\ncommit: 3f2a9c1d0\n"
    f = parse_output(report)
    assert f == {"outcome": "rca", "origin": "upstream", "confidence": "medium", "self_healing": "yes",
                 "repo": "shop-rca", "commit_sha": "3f2a9c1d0"}


def test_parse_output_repo_is_generic():
    assert parse_output("RCA_FIELDS\nrepo: some-new-repo\n")["repo"] == "some-new-repo"
    assert "repo" not in parse_output("RCA_FIELDS\nrepo: none\n")
    assert "repo" not in parse_output("RCA_FIELDS\nrepo: ../etc/passwd\n")


def test_parse_output_not_applicable_and_bad_values():
    assert parse_output("RCA_OUTCOME: not_applicable - OK message")["outcome"] == "not_applicable"
    assert parse_output("RCA_FIELDS\norigin: whatever\n").get("origin") is None


def test_build_input_scope_block():
    text = build_input({"input_text": "ALERT"}, ["t2"], [{"subject": "t1", "rca_id": 7}])
    assert text.startswith("RCA context") and "Analyze only these subjects: t2" in text and "#7" in text
    assert build_input({"input_text": "ALERT"}, ["*"], []) == "ALERT"


# --- reactions ----------------------------------------------------------------

def test_emoji_keys():
    assert emoji_key({"unicode": "\U0001F44D\U0001F3FD"}) == ("\U0001F44D", None)   # skin tone removed
    assert emoji_key({"customEmoji": {"name": "customEmojis/abc", "emojiName": ":party-parrot:"}}) == ("customEmojis/abc", ":party-parrot:")
    a = [{"emoji": {"unicode": "\U0001F44D"}, "reactionCount": 1}]
    b = [{"emoji": {"unicode": "\U0001F44E"}, "reactionCount": 1}]
    assert reaction_summary(a) == {"\U0001F44D": 1} and reaction_summary(a) != reaction_summary(b)
    assert reaction_summary(None) == {}


def test_message_link_matches_chat_copy_link():
    message_link = GCHAT.message_link
    assert message_link("spaces/AAAAbCdEfGh/messages/Th7eAdId123.Th7eAdId123") == \
        "https://chat.google.com/room/AAAAbCdEfGh/Th7eAdId123/Th7eAdId123?cls=10"
    assert message_link("spaces/AAAAbCdEfGh/messages/Th7eAdId123.MsgId456xy") == \
        "https://chat.google.com/room/AAAAbCdEfGh/Th7eAdId123/MsgId456xy?cls=10"
    assert message_link("not-a-message-id") == "not-a-message-id"


# --- rate limiting / retries --------------------------------------------------

from googleapiclient.errors import HttpError

from nimi_service.providers.gchat import client as gchat


class _Resp(dict):
    def __init__(self, status):
        super().__init__(status=str(status))
        self.status = status
        self.reason = "x"


class _FakeRequest:
    def __init__(self, statuses):
        self.statuses, self.calls = list(statuses), 0

    def execute(self, num_retries=0):
        self.calls += 1
        status = self.statuses.pop(0)
        if status != 200:
            raise HttpError(_Resp(status), b"{}")
        return {"name": "ok"}


def test_sliding_window():
    w = gchat._SlidingWindow(2, 1.0)
    w.record(0.0); w.record(0.1)
    assert w.wait_time(0.2) == pytest.approx(0.8)
    assert w.wait_time(1.05) == 0.0


@pytest.fixture(autouse=True)
def _no_rate_limit(monkeypatch):
    monkeypatch.setattr(gchat.LIMITER, "acquire", lambda kind, space: None)


def test_rate_limiter_paces_reads(monkeypatch):
    limiter, slept = gchat.RateLimiter(), []
    clock = [0.0]
    monkeypatch.setattr(gchat.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(gchat.time, "sleep", lambda s: (slept.append(s), clock.__setitem__(0, clock[0] + s)))
    for _ in range(gchat.SPACE_READS_PER_SEC + 1):
        limiter.acquire("read", "spaces/T")
    assert len(slept) == 1 and slept[0] == pytest.approx(1.0)       # 6th read in a second waits


def test_execute_retries_429_and_5xx(monkeypatch):
    monkeypatch.setattr(gchat.time, "sleep", lambda s: None)
    req = _FakeRequest([429, 503, 200])
    assert gchat._execute(req, "read", "spaces/T") == {"name": "ok"} and req.calls == 3


def test_execute_does_not_retry_403(monkeypatch):
    monkeypatch.setattr(gchat.time, "sleep", lambda s: None)
    with pytest.raises(HttpError):
        gchat._execute(_FakeRequest([403, 200]), "read", "spaces/T")


def test_write_retries_only_429(monkeypatch):
    monkeypatch.setattr(gchat.time, "sleep", lambda s: None)
    assert gchat._execute(_FakeRequest([429, 200]), "write", "spaces/T", retry_statuses={429})["name"] == "ok"
    with pytest.raises(HttpError):
        gchat._execute(_FakeRequest([503, 200]), "write", "spaces/T", retry_statuses={429})


def test_backoff_is_capped():
    assert gchat._backoff(0) < 2 and gchat._backoff(10) == gchat.MAX_BACKOFF_SEC


# --- provider normalization / provider-neutral pieces --------------------------

def test_gchat_normalize_maps_to_neutral_message():
    raw = msg("[Alerting] x alert")
    raw["threadReply"] = True
    raw["emojiReactionSummaries"] = [{"emoji": {"unicode": "\U0001F44D"}, "reactionCount": 2}]
    m = GCHAT.normalize(raw)
    assert m.external_id == "spaces/S/messages/A.A" and m.channel_external_id == "spaces/S"
    assert m.thread_external_id == "spaces/S/threads/A" and m.is_thread_reply is True
    assert m.sender_type == "bot" and m.sender_external_id == "users/123"
    assert m.created_at.tzinfo is not None and m.reaction_summary == {"\U0001F44D": 2}
    assert m.title_hint == "[Alerting] x alert" and m.rendered_text.startswith("[2026-01-15T10:04:31.123456Z]")
    assert GCHAT.normalize({**raw, "sender": {"type": "HUMAN"}}).sender_type == "human"


def test_gchat_channel_id_normalization():
    assert GCHAT.normalize_channel_id("AAAAbCdEfGh") == "spaces/AAAAbCdEfGh"
    assert GCHAT.normalize_channel_id("spaces/AAAAbCdEfGh") == "spaces/AAAAbCdEfGh"


def test_reply_formatting_uses_provider():
    text = format_reuse(GCHAT, [{"subject": "t1", "rca_id": 3}],
                        {3: "spaces/AAAAbCdEfGh/messages/Th7eAdId123.MsgId456xy"}, 4000)
    assert text.startswith("👁️ *NIMI* — ") and text.splitlines()[2] == "*Same as an earlier RCA*"
    assert "<https://chat.google.com/room/AAAAbCdEfGh/Th7eAdId123/MsgId456xy?cls=10|earlier RCA>" in text


def test_provider_registry():
    from nimi_service.providers import PROVIDERS, get_provider
    assert "gchat" in PROVIDERS and isinstance(get_provider("gchat", Settings()), GChatProvider)
    with pytest.raises(SystemExit):
        get_provider("carrier-pigeon", Settings())


def test_models_cover_all_tables_neutral():
    from nimi_service.models import Base
    assert set(Base.metadata.tables) == {
        "channels", "messages", "thread_messages", "reactions", "signatures", "message_parses",
        "rca_requests", "rca_results", "rca_request_subjects", "rca_posts", "rca_post_reuse_links", "rca_feedback",
        "rca_exclusions", "red_flags", "service_state"}


# --- red flags: scanner, notification text, sensitive-data fields ----------------

from nimi_service import scanner
from nimi_service.rca import parse_sensitive, sensitive_note
from nimi_service.redflags import format_notification

FAKE_AWS = "AKIA" + "ABCDEFGHIJKLMNOP"
FAKE_GKEY = "AIza" + "Sy" + "A" * 33
FAKE_PEM = "-----BEGIN " + "RSA PRIVATE KEY-----"


def test_scanner_finds_high_confidence_secrets():
    text = f"line1\naws={FAKE_AWS}\nkey {FAKE_GKEY}\n{FAKE_PEM}\npassword=hunter2abc\npostgres://u:pa55word@db/x"
    found = {(f.type, f.severity, f.line) for f in scanner.scan(text)}
    assert ("aws-access-key", "critical", 2) in found and ("google-api-key", "critical", 3) in found
    assert ("private-key", "critical", 4) in found and ("password-assignment", "high", 5) in found
    assert ("url-credentials", "high", 6) in found
    assert scanner.worst_severity(scanner.scan(text)) == "critical"


def test_scanner_ignores_placeholders_and_normal_text():
    for text in ["password=${DB_PASSWORD}", "password: <set-a-strong-password>", "password=null",
                 "api_key: {{ vault.key }}", "the password field is required", "order export delayed alert",
                 "com.example.shop.catalog.DuplicateItemException: item sku-9 is listed twice"]:
        assert scanner.scan(text) == [], text


def test_mask_never_leaks_values():
    masked, findings = scanner.mask(f"use {FAKE_AWS} and Bearer {'x' * 30}")
    assert FAKE_AWS not in masked and "<redacted: aws-access-key>" in masked and "<redacted: bearer-token>" in masked
    assert all("x" * 30 not in str(f.describe()) for f in findings)


def test_notification_contains_no_values():
    text = format_notification(7, "sensitive_data", "critical", "scanner", "Message appears to contain sensitive data",
                               {"types": ["aws-access-key"], "findings": [{"type": "aws-access-key", "line": 2}],
                                "value": FAKE_AWS}, "https://chat.google.com/room/X/Y/Z?cls=10")
    assert FAKE_AWS not in text and "aws-access-key" in text and "#7" in text and "🚨" in text


def test_parse_sensitive_field():
    assert parse_sensitive("input:password, config:webhook-url") == [
        {"where": "input", "type": "password"}, {"where": "config", "type": "webhook-url"}]
    assert parse_sensitive("input:p@ss=hunter2") == []          # anything value-like is dropped
    out = parse_output("RCA_FIELDS\norigin: upstream\nsensitive_data: code:api-key\n")
    assert out["sensitive_data"] == [{"where": "code", "type": "api-key"}]
    assert "sensitive_data" not in parse_output("RCA_FIELDS\nsensitive_data: none\n")


def test_sensitive_note():
    assert sensitive_note([]) == []
    note = "\n".join(sensitive_note(["password", "email"]))
    assert "email, password" in note and "remove it from the alert" in note


def test_triage_sensitive_types_are_labels_only():
    out = validate({"decision": "new", "sensitive_data": {"found": True, "types": ["API key", "p@ss=x", 5]}}, set())
    assert out["sensitive_types"] == ["api-key"]
    out = validate({"decision": "new", "sensitive_data": {"found": True, "types": []}}, set())
    assert out["sensitive_types"] == ["unspecified"]
    assert validate({"decision": "new", "sensitive_data": {"found": False}}, set())["sensitive_types"] == []


def test_long_report_keeps_header_note_and_feedback():
    from nimi_service.rca import format_rca, FEEDBACK_LINE
    text = format_rca(GCHAT, "x" * 20000 + "\nRCA_FIELDS\norigin: upstream\n", [], {}, 4000, ["password"])
    assert len(text) <= 4000 and text.startswith("👁️ *NIMI* — ")
    assert any("appears to contain sensitive data (password)" in l for l in text.splitlines()[:7])
    assert text.endswith(FEEDBACK_LINE) and "shortened; full report stored" in text and "RCA_FIELDS" not in text


# --- settings load order (temp files with neutral names; no real env files touched) ----------

def test_settings_load_order(tmp_path, monkeypatch):
    from nimi_service import config
    user = tmp_path / "user_settings"
    repo = tmp_path / "repo_settings"
    user.write_text("NIMI_DB_NAME=user_db\nNIMI_DB_PASSWORD=pw\nNIMI_POLL_SECONDS=30\nNIMI_MAX_PER_DAY=10\n")
    repo.write_text("export NIMI_POLL_SECONDS=45\nNIMI_ADMIN_WEBHOOK='https://example.invalid/hook'\n"
                    "NIMI_GCHAT_TOKEN_FILE=~/somewhere/token.json\n")
    monkeypatch.delenv("NIMI_ENV", raising=False)
    for key in config.ENV_VARS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(config, "USER_ENV_FILE", user)
    monkeypatch.setattr(config, "REPO_ENV_FILE", repo)
    monkeypatch.setenv("NIMI_MAX_PER_DAY", "99")
    s = config.load_settings()
    assert s.db_name == "user_db"                          # only in the user file
    assert s.poll_seconds == 45                            # repo file overrides user file
    assert s.max_rcas_per_day == 99                        # environment overrides both
    assert s.admin_webhook == "https://example.invalid/hook"   # quotes stripped
    assert str(s.gchat_token_file).startswith(str(config.Path.home()))   # ~ expanded
    rows = {k: (v, src) for k, v, src in config.describe(s)}
    assert rows["NIMI_ADMIN_WEBHOOK"] == ("<set, hidden>", str(repo))
    assert rows["NIMI_DB_PASSWORD"][0] == "<set, hidden>" and rows["NIMI_MAX_PER_DAY"] == ("99", "environment")
    assert rows["NIMI_TRIAGE_MODEL"] == ("haiku", "default")


def test_settings_explicit_file_and_legacy_dsn(tmp_path, monkeypatch):
    from nimi_service import config
    only = tmp_path / "only"
    only.write_text("NIMI_DB_NAME=explicit\n")
    for key in list(config.ENV_VARS) + ["NIMI_DB_DSN"]:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("NIMI_ENV", str(only))
    assert config.load_settings().db_name == "explicit"
    only.write_text("NIMI_DB_DSN=postgresql://u:p@h/x\n")
    with pytest.raises(SystemExit):                        # old single-URL setting is rejected with a hint
        config.load_settings()


def test_db_url_from_parts_escapes_password(tmp_path):
    from nimi_service.config import Settings
    s = Settings(db_host="db.internal", db_port=6432, db_name="nimi", db_user="nimi_service",
                 db_password="p@ss:w/rd?#", db_sslmode="require")
    url = s.db_url
    assert url.password == "p@ss:w/rd?#" and url.host == "db.internal" and url.port == 6432
    assert url.query == {"sslmode": "require"} and url.drivername == "postgresql+psycopg"
    assert "p@ss:w/rd?#" not in str(url)                   # repr/str hide the password
    pw = tmp_path / "pw"
    pw.write_text("from-file-secret\n")
    assert Settings(db_password_file=pw).db_url.password == "from-file-secret"
    assert Settings().db_url.password is None              # no password: libpq uses ~/.pgpass / trust / peer


# --- Claude account / clean environment ------------------------------------------

def test_clean_env_drops_outside_claude_settings(tmp_path):
    from nimi_service.claude_cli import build_env
    from nimi_service.config import Settings
    parent = {"PATH": "/usr/bin", "HOME": "/home/x", "LC_ALL": "C.UTF-8", "PYENV_ROOT": "/home/x/.pyenv",
              "ANTHROPIC_API_KEY": "key-for-another-project", "ANTHROPIC_BASE_URL": "https://elsewhere.invalid",
              "ANTHROPIC_MODEL": "some-model", "HTTPS_PROXY": "http://proxy:3128", "AWS_SECRET_ACCESS_KEY": "x",
              "CLAUDE_CONFIG_DIR": "/home/x/.claude-other"}
    env = build_env(Settings(), parent)
    assert env == {"PATH": "/usr/bin", "HOME": "/home/x", "LC_ALL": "C.UTF-8", "PYENV_ROOT": "/home/x/.pyenv"}
    key = tmp_path / "key"
    key.write_text("sk-test-key\n")
    env = build_env(Settings(claude_config_dir=tmp_path / "cfg", anthropic_api_key_file=key,
                             claude_pass_env=("HTTPS_PROXY", "NO_PROXY")), parent)
    assert env["CLAUDE_CONFIG_DIR"] == str(tmp_path / "cfg") and env["ANTHROPIC_API_KEY"] == "sk-test-key"
    assert env["HTTPS_PROXY"] == "http://proxy:3128" and "NO_PROXY" not in env and "ANTHROPIC_MODEL" not in env
    with pytest.raises(RuntimeError):
        build_env(Settings(anthropic_api_key_file=tmp_path / "missing"), parent)


def test_setting_sources_validation():
    from nimi_service.config import Settings, _sources
    assert Settings().claude_setting_sources == "user,project"
    assert _sources("project") == "project" and _sources(" user , project ") == "user,project" and _sources("") == ""
    with pytest.raises(SystemExit):
        _sources("user,everything")


def test_cli_error_detects_failures_reported_as_success():
    from types import SimpleNamespace
    from nimi_service.claude_cli import cli_error
    ok = SimpleNamespace(returncode=0, stderr="")
    assert cli_error({"subtype": "success", "num_turns": 3, "modelUsage": {"m": {}}, "result": "report"}, ok) == ""
    # what `claude -p "/rca ..."` returns when the configured account isn't logged in
    not_logged_in = {"is_error": False, "subtype": "success", "num_turns": 0, "modelUsage": {},
                     "result": "Not logged in · Please run /login"}
    assert "Not logged in" in cli_error(not_logged_in, ok)
    assert "Not logged in" in cli_error({"is_error": True, "result": "Not logged in · Please run /login"},
                                        SimpleNamespace(returncode=1, stderr=""))
    assert "error_max_turns" in cli_error({"subtype": "error_max_turns", "num_turns": 9, "modelUsage": {"m": {}}}, ok)


# --- Claude unavailable: classification, reset time, incomplete output -----------------

def test_classify_unavailable():
    from nimi_service.claude_cli import NEEDS_ADMIN, classify_unavailable
    cases = {
        "Claude AI usage limit reached|1999999999": "usage_limit",
        "You're out of extra usage · resets 3pm": "usage_limit",
        "5-hour limit reached ∙ resets 9pm (UTC)": "usage_limit",
        "Credit balance is too low": "credit",
        "Budget limit reached ($50.00)": "budget",
        "API Error: 429 rate_limit_error: Number of request tokens has exceeded your per-minute rate limit": "rate_limit",
        "Not logged in · Please run /login": "auth",
        "OAuth token expired": "auth",
    }
    for text, kind in cases.items():
        exc = classify_unavailable(text)
        assert exc is not None and exc.kind == kind, text
    assert classify_unavailable("claude call failed (error_max_turns): stopped") is None
    assert classify_unavailable("") is None
    assert {"credit", "budget", "auth"} == NEEDS_ADMIN


def test_reset_time_parsing():
    from datetime import datetime, timezone
    from nimi_service.claude_cli import parse_reset_time
    ts = parse_reset_time("Claude AI usage limit reached|1999999999")
    assert ts == datetime.fromtimestamp(1999999999, tz=timezone.utc)
    assert parse_reset_time("Claude AI usage limit reached|1600000000") is None     # in the past
    assert parse_reset_time("resets 3pm") is None                                   # ambiguous: use cool-down


def test_incomplete_report_is_an_error():
    out = parse_output("# RCA: something\n\n## Summary\nThe pipeline failed because")   # cut off, no RCA_FIELDS
    assert out["outcome"] == "error" and "incomplete" in out["error"]
    assert parse_output("RCA_OUTCOME: not_applicable - resolved")["outcome"] == "not_applicable"


def test_limit_notice_makes_no_promises():
    from nimi_service.rca import format_limit_notice
    text = format_limit_notice(GCHAT)
    assert text.startswith("👁️ *NIMI*") and "usage limit" in text
    assert not any(w in text.lower() for w in ("will be posted", "retry", "later", "when the limit resets"))


# --- rules-v3: pod suffixes masked in signatures; Grafana "[FIRING:n]" statuses ---------------------------

LEVEL_FIRST_LOG = ("```\n[ERROR] [2026-01-15 13:20:45+0000] [stock-exporter-deployment-5f7c9d8b6a-h3j9k] "
                   "[exporter-2] com.example.shop.exporter.StockLevelExporter Failed to refresh "
                   "stock levels for warehouse north java.net.SocketTimeoutException: Read timed out\n```")


def test_same_error_from_another_pod_keeps_its_signature():
    other_pod = (LEVEL_FIRST_LOG.replace("5f7c9d8b6a-h3j9k", "6a8b7c9d5e-p2r4t")
                 .replace("[exporter-2]", "[exporter-5]").replace("13:20:45", "18:02:11"))
    a, b = parse_text(LEVEL_FIRST_LOG), parse_text(other_pod)
    assert a["signature_key"] == b["signature_key"]
    assert (a["kind"], a["service"], a["layout"]) == ("log", "stock-exporter", "sample/level-first")


def test_pod_mask_keeps_deployment_names_and_plain_words():
    from nimi_service.parse import mask_pods
    assert mask_pods("order-worker-deployment-7d9f8c6b5d-x2k4p") == "order-worker-deployment-<pod>"
    assert mask_pods("nightly-jobs-stock-sync-29865600-h8rvf") == "nightly-jobs-stock-sync-<pod>"
    assert mask_pods("foo-dashboard-alert") == "foo-dashboard-alert"          # no digit: not a pod
    a = parse_text(LEVEL_FIRST_LOG)["signature_key"]
    b = parse_text(LEVEL_FIRST_LOG.replace("stock-exporter", "catalog-api"))["signature_key"]
    assert a != b                                                            # different deployments stay apart


def test_grafana_firing_and_resolved_counts():
    firing = parse_text("[FIRING:1] HighErrorRate (shop-api, severity=page)")
    assert (firing["kind"], firing["status"]) == ("alert", "Firing")
    resolved = parse_text("[RESOLVED:2] HighErrorRate (shop-api, severity=page)")
    assert resolved["status"] == "Resolved" and looks_resolved(resolved["status"])
    assert firing["signature_key"] == resolved["signature_key"]


# --- triage: subjects only from the new message; the new message comes last in the prompt -------------

def test_triage_drops_subjects_not_in_the_message():
    msg = "[Triggered] Unshipped Orders\nstores: shop/eu/orders, shop/us/orders"
    out = validate({"decision": "new", "new_subjects": ["shop/eu/orders", "shop/apac/orders"]}, set(), msg)
    assert out["new_subjects"] == ["shop/eu/orders"] and "shop/apac/orders" in out["reason"]
    out = validate({"decision": "new", "new_subjects": ["shop/apac/orders"]}, set(), msg)       # none left
    assert out["decision"] == "new" and out["new_subjects"] == ["*"]
    out = validate({"decision": "reuse", "reuse": [{"subject": "shop/apac/orders", "rca_id": 1}]}, {1}, msg)
    assert out["decision"] == "new" and out["reuse"] == []                                    # no reuse of absent subjects
    out = validate({"decision": "new", "new_subjects": ["SHOP/EU/ORDERS"]}, set(), msg)        # case-insensitive
    assert out["new_subjects"] == ["SHOP/EU/ORDERS"]


def test_triage_prompt_puts_the_new_message_last():
    from datetime import datetime, timezone
    from nimi_service.triage import build_prompt
    now = datetime.now(timezone.utc)
    p = build_prompt({"input_text": "NEW-MESSAGE-BODY", "message_created_at": now},
                     [{"created_at": now, "status": "Triggered", "rendered_text": "OLD-FIRING-BODY"}], [])
    assert p.index("OLD-FIRING-BODY") < p.index("THE MESSAGE TO DECIDE ON") < p.index("NEW-MESSAGE-BODY")
    assert p.rstrip().endswith("</new_message>")
