"""The one chat message per RCA: the skill's RCA_CHAT JSON, rendered by NIMI in a fixed layout, plus header,
status line, source line and footer."""
import json

from nimi_service import chat_message
from nimi_service.config import Settings
from nimi_service.providers.gchat.provider import GChatProvider
from nimi_service.rca import (FEEDBACK_LINE, format_limit_notice, format_rca, format_reuse, looks_like_nimi_post,
                              parse_output, tldr_of)

GCHAT = GChatProvider(Settings())
CHAT = {
    "title": "Nightly order load failed – warehouse (prod)",
    "context": "`orders` | load job: order-loader, prod",
    "tldr": "The 02:00 UTC load failed because the warehouse API returned errors for 4 minutes. "
            "The scheduler retries at 02:30, so it should fix itself.",
    "cause": {"certainty": "likely", "points": ["All 37 errors are `503 Service Unavailable` between 02:10 and 02:14 UTC."]},
    "verify": [
        {"where": None, "text": "Look at the 02:30 attempt: succeeded → nothing else to do.", "command": None},
        {"where": "Loader DB (read replica)", "text": "Check the last load.",
         "command": "SELECT status FROM loads ORDER BY started_at DESC LIMIT 1;",
         "outcomes": ["done → nothing to do", "failed → the outage is ongoing"]}],
    "now": ["Nothing if the retry succeeded."],
    "do_not": ["Re-run the full backfill: it duplicates rows."],
    "fixes": [{"owner": "order-loader (owning team)", "items": ["Retry 5xx responses with backoff."]}],
    "also": ["Add a runbook section for this alert."],
}


def report_with(chat, fence=False, end=True) -> str:
    body = chat if isinstance(chat, str) else json.dumps(chat, ensure_ascii=False, indent=2)
    if fence:
        body = "```json\n" + body + "\n```"
    return (f"# RCA: Nightly order load failed\n## Summary\nThe load failed.\n\nRCA_CHAT\n{body}\n"
            + ("END_RCA_CHAT\n" if end else "")
            + "\nRCA_FIELDS\norigin: upstream\nconfidence: medium\nself_healing: yes\nrepo: shop\n"
              "commit: 3f2a9c1d0\nsensitive_data: none\n")


REPORT = report_with(CHAT)


def test_fields_and_tldr_parse():
    f = parse_output(REPORT)
    assert (f["outcome"], f["origin"], f["confidence"], f["self_healing"]) == ("rca", "upstream", "medium", "yes")
    assert tldr_of(REPORT).startswith("The 02:00 UTC load failed") and "\n" not in tldr_of(REPORT)


# TODO(old chat format): remove with the Markdown fallbacks in rca.tldr_of / rca.title_of.
def test_tldr_of_older_markdown_reports_still_works():
    old = "RCA_CHAT\n**Title**\n\n**TL;DR:** Old style summary.\nmore\n\n**Cause** x\nEND_RCA_CHAT\nRCA_FIELDS\nconfidence: low\n"
    assert tldr_of(old) == "Old style summary. more"


def test_fixed_layout_and_markup():
    problems = []
    text = format_rca(GCHAT, REPORT, [], {}, 4000, problems=problems)
    lines = text.splitlines()
    assert lines[0] == "👁️ *NIMI* — _Automated RCA, not verified. Based on code and docs that may differ from production._"
    assert lines[1] == ""
    assert lines[2] == "*RCA: Nightly order load failed – warehouse (prod)*"
    assert lines[3] == "*Confidence:* medium · *Origin:* upstream · *Self-healing:* yes"
    assert lines[4] == "`orders` | load job: order-loader, prod"                    # context under the status line
    assert lines[6].startswith("*TL;DR:* The 02:00 UTC load failed")              # TL;DR always inline
    order = [lines.index(h) for h in ("*Cause* (likely, to be confirmed)", "*Steps to verify*", "*Now*", "*Do NOT*",
                                      "*Permanent fixes*", "*Also*")]
    assert order == sorted(order)                                                    # fixed order, headings alone
    assert "• Re-run the full backfill: it duplicates rows." in text and "Do not" not in text
    assert "1. Look at the 02:30 attempt" in text and "2. _Loader DB (read replica):_ Check the last load." in text
    assert "```\nSELECT status FROM loads ORDER BY started_at DESC LIMIT 1;\n```\n   • done → nothing to do\n   • failed → the outage is ongoing" in text
    assert "_order-loader (owning team)_" in text and "**" not in text
    assert "RCA_FIELDS" not in text and "## Summary" not in text and "Related" not in text   # empty section left out
    assert lines[-1] == FEEDBACK_LINE and looks_like_nimi_post(GCHAT, text)
    assert problems == []


def test_fenced_json_and_missing_end_marker_are_accepted():
    for report in (report_with(CHAT, fence=True), report_with(CHAT, end=False)):
        problems = []
        text = format_rca(GCHAT, report, [], {}, 4000, problems=problems)
        assert "*Do NOT*" in text and problems == []


def test_invalid_or_missing_chat_falls_back_to_the_summary_and_is_reported():
    for report, expected in ((report_with('{"title": "x", "tldr": "y",}'), "not valid JSON"),
                             (report_with({"title": "x", "tldr": "y"}), "missing cause"),
                             ("# RCA: Disk full\n## Summary\nThe disk filled up.\n## Root cause\nlogs\n"
                              "RCA_FIELDS\nconfidence: low\n", "no RCA_CHAT block")):
        problems = []
        text = format_rca(GCHAT, report, [], {}, 4000, problems=problems)
        assert "Short message unavailable" in text and any(expected in p for p in problems), (expected, problems)
    assert format_rca(GCHAT, report, [], {}, 4000).splitlines()[2] == "*RCA: Disk full*"


def test_title_rules():
    for title, shown in (("RCA: Order export delayed [No Data]", "Order export delayed [No Data]"),
                         ("**Bold title**", "Bold title")):
        text = format_rca(GCHAT, report_with({**CHAT, "title": title}), [], {}, 4000)
        assert text.splitlines()[2] == f"*RCA: {shown}*"
    problems = []
    text = format_rca(GCHAT, report_with({**CHAT, "title": ""}), [], {}, 4000,
                      fallback_title="Unshipped orders above threshold", problems=problems)
    assert text.splitlines()[2] == "*RCA: Unshipped orders above threshold*" and "missing title" in problems
    long_title = chat_message.parse(report_with({**CHAT, "title": "word " * 60}))[0].title
    assert len(long_title) <= chat_message.TITLE_MAX + 1 and long_title.endswith("…")


def test_layout_inside_values_is_ignored():
    chat = {**CHAT, "tldr": "## Summary\nLine one.\n\nLine two.", "now": "Just one action.",
            "do_not": ["Restart it.", ""], "also": None}
    text = format_rca(GCHAT, report_with(chat), [], {}, 4000)
    assert "*TL;DR:* Line one. Line two." in text and "Summary" not in text
    assert "• Just one action." in text and "*Also*" not in text                  # a single text is a one-item list


def test_internal_chatter_is_removed_outside_related():
    chat = {**CHAT, "tldr": "The orders table has no index. This corrects RCA #2, which found nothing. Fix the index.",
            "cause": {"certainty": "confirmed", "points": ["Missing index on `created_at`.", "This agrees with RCA #3."]},
            "related": [{"ref": "rca:3", "label": "RCA #3", "why": "same cause"}]}
    text = format_rca(GCHAT, report_with(chat), [], {}, 4000)
    assert "corrects" not in text and "agrees" not in text and "*Cause* (confirmed)" in text
    assert "*TL;DR:* The orders table has no index. Fix the index." in text
    assert "• earlier RCA — same cause" in text                         # unverified: label only, never the ID


def test_related_entries_are_verified_and_linked():
    chat = {**CHAT, "related": [{"ref": "msg:7", "label": "503 log, 02:10 UTC", "why": "first error"},
                                {"ref": "http://evil.example", "label": "phish"},
                                {"ref": "msg:8", "label": "made-up [log]"}]}
    report = report_with(chat)
    from nimi_service.rca import find_refs
    assert find_refs(report) == ({7, 8}, set())
    problems = []
    text = format_rca(GCHAT, report, [], {}, 4000, links={("msg", 7): "https://chat.google.com/room/A/T/M7?cls=10"},
                      problems=problems)
    assert "• <https://chat.google.com/room/A/T/M7?cls=10|503 log, 02:10 UTC> — first error" in text
    assert "evil.example" not in text and "• made-up (log)" in text and any("related" in p for p in problems)


def test_too_long_drops_whole_optional_sections_never_text():
    chat = {**CHAT, "also": ["x" * 900], "related": [{"ref": "msg:7", "label": "y" * 90, "why": "z" * 800}]}
    full = format_rca(GCHAT, report_with(chat), [], {}, 10000)
    problems = []
    text = format_rca(GCHAT, report_with(chat), [], {}, len(full) - 1200, problems=problems)
    assert "*Related*" not in text and "*Also*" not in text and "x" * 900 not in text
    assert "_(shortened; full report stored)_" in text and "*Permanent fixes*" in text
    assert problems == ["too long for chat: left out related, also"]
    assert CHAT["tldr"].split(".")[0] in text and "… (shortened" not in text     # no mid-text cut


def test_reuse_and_mixed_name_earlier_rcas_by_title_and_time_not_id():
    from datetime import datetime, timezone
    from nimi_service.rca import title_of
    when = datetime(2026, 3, 3, 14, 20, tzinfo=timezone.utc)
    earlier = {12: {"title": "Warehouse API outage", "tldr": "Warehouse API outage.", "created_at": when}}
    reuse = format_reuse(GCHAT, [{"subject": "orders", "rca_id": 12}], {12: "spaces/A/messages/T.R"}, 4000,
                         earlier=earlier)
    assert "• `orders` → <https://chat.google.com/room/A/T/R?cls=10|Warehouse API outage, Mar 03 14:20 UTC>" in reuse
    assert "*TL;DR (Mar 03):* Warehouse API outage." in reuse and "#12" not in reuse
    assert "*Same as an earlier RCA*" in reuse
    whole = format_reuse(GCHAT, [{"subject": "*", "rca_id": 12}], {}, 4000, earlier=earlier)
    assert "• Warehouse API outage, Mar 03 14:20 UTC" in whole and "`*`" not in whole
    unposted = format_reuse(GCHAT, [{"subject": "orders", "rca_id": 12}], {}, 4000, earlier={})
    assert "• `orders` → earlier RCA" in unposted and "#12" not in unposted
    mixed = format_rca(GCHAT, REPORT, [{"subject": "order_items", "rca_id": 12}], {}, 4000, earlier=earlier)
    assert "*Already analyzed:*" in mixed and "*TL;DR (Mar 03):* Warehouse API outage." in mixed
    assert title_of(REPORT) == CHAT["title"]
    # TODO(old chat format): remove with the Markdown fallback in rca.title_of.
    assert title_of("RCA_CHAT\n**RCA: Old style title**\n\n**TL;DR:** x\nEND_RCA_CHAT\n") == "Old style title"


def test_limit_notice_is_short_and_not_an_rca():
    text = format_limit_notice(GCHAT)
    assert text.startswith("👁️ *NIMI* — ⏳ Couldn't analyze") and "Automated RCA" not in text and "Rate it" not in text


def test_citations_become_links_only_when_verified():
    from nimi_service.rca import find_refs, resolve_refs
    text = ("See [503 log, 02:10 UTC](msg:1235) and [RCA #12](rca:12); "
            "[made-up log](msg:999) and [phish](https://evil.example/x) stay plain.")
    assert find_refs(text) == ({1235, 999}, {12})
    out = resolve_refs(GCHAT, text, {("msg", 1235): "https://chat.google.com/room/A/T/M?cls=10",
                                     ("rca", 12): "https://chat.google.com/room/A/T/R?cls=10"})
    assert "<https://chat.google.com/room/A/T/M?cls=10|503 log, 02:10 UTC>" in out
    assert "<https://chat.google.com/room/A/T/R?cls=10|earlier RCA>" in out          # an ID label means nothing
    assert "made-up log" in out and "msg:999" not in out
    assert "evil.example" not in out and "phish" in out


def test_inline_citations_in_sections_are_linked():
    chat = {**CHAT, "cause": {"certainty": "likely", "points": ["See [the 503 log, 02:10 UTC](msg:7)."]}}
    text = format_rca(GCHAT, report_with(chat), [], {}, 4000, links={("msg", 7): "https://chat.google.com/room/A/T/M7?cls=10"})
    assert "• See <https://chat.google.com/room/A/T/M7?cls=10|the 503 log, 02:10 UTC>." in text


def test_id_only_link_labels_get_channel_and_time():
    from nimi_service.rca import resolve_refs
    links = {("msg", 1234): "https://chat.google.com/room/A/T/M?cls=10",
             ("msg-label", 1234): "app-logs, Mar 3 14:20 UTC"}
    for text in ("for example [msg 1234](msg:1234)", "see [1234](msg:1234)", "see [message #1234](msg:1234)"):
        assert "<https://chat.google.com/room/A/T/M?cls=10|app-logs, Mar 3 14:20 UTC>" in resolve_refs(GCHAT, text, links)
    descriptive = resolve_refs(GCHAT, "[payment timeout log, Mar 3](msg:1234)", links)
    assert "|payment timeout log, Mar 3>" in descriptive                       # good labels are kept


def test_reuse_of_several_rcas_and_link_labels_with_markup():
    from datetime import datetime, timezone
    when = datetime(2026, 3, 3, 14, 20, tzinfo=timezone.utc)
    earlier = {12: {"title": "Duplicate IDs in `orders` | <x>", "tldr": "a", "created_at": when},
               13: {"title": "Order export delayed", "tldr": "b", "created_at": when}}
    reuse = format_reuse(GCHAT, [{"subject": "orders", "rca_id": 12}, {"subject": "items", "rca_id": 13}],
                         {12: "spaces/A/messages/T.R"}, 4000, earlier=earlier)
    assert "*Same as earlier RCAs*" in reuse
    # Chat drops a link whose label has formatting; | < > would break the <url|label> markup
    assert "<https://chat.google.com/room/A/T/R?cls=10|Duplicate IDs in orders / x, Mar 03 14:20 UTC>" in reuse


def test_a_sentence_citing_an_rca_keeps_its_fact_and_gets_the_rca_title():
    from datetime import datetime, timezone
    from nimi_service.rca import earlier_label
    chat = {**CHAT, "cause": {"certainty": "likely", "points": [
        "The export was retried twice. [RCA #12](rca:12) found 40 identical errors since May 2. "
        "This agrees with RCA #12."]}}
    when = datetime(2026, 3, 3, 14, 20, tzinfo=timezone.utc)
    links = {("rca", 12): "https://chat.google.com/room/A/T/R?cls=10",
             ("rca-label", 12): earlier_label({"title": "Order export file missing", "created_at": when})}
    text = format_rca(GCHAT, report_with(chat), [], {}, 4000, links=links)
    assert ("<https://chat.google.com/room/A/T/R?cls=10|Order export file missing, Mar 03 14:20 UTC> found 40 "
            "identical errors since May 2.") in text
    assert "agrees" not in text                                                   # bare mention: still removed
