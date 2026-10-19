"""nimi-service CLI.

  python -m nimi_service db upgrade                      create / update the schema to the latest version
  python -m nimi_service db revision "<message>"         draft a migration from model changes (review it!)
  python -m nimi_service db current                      the database's schema version vs the latest
  python -m nimi_service gchat-login --client-secret <file>   one-time Google login; saves the Chat token
  python -m nimi_service gchat-spaces [--all]            list your Google Chat spaces and their IDs
  python -m nimi_service add-channel <provider> <channel> <label> [--rca|--no-rca] [--backfill-days N] [--display-name X]
                                                         e.g. add-channel gchat AAAAxxxxxxx alerts --rca
  python -m nimi_service channels                        list registered channels
  python -m nimi_service ingest-once                     poll all channels once (first run backfills; no RCAs for backfill)
  python -m nimi_service backfill <channel> --days N     pull older history (up to N days back) for a channel
  python -m nimi_service parse-test [--text "..."] [--channel L] [--kind K] [--sender S] [--limit N]
                                                         how the parser (and platform profiles) read messages
  python -m nimi_service reparse                         rebuild parses/signatures for all stored messages
  python -m nimi_service run                             poll + triage + RCA + reactions, forever
  python -m nimi_service rca <message> [--provider P] [--fresh] [--no-post]
                                                         RCA one stored message (the provider's message ID) now
                                                         (--fresh: always a new RCA, no reuse or exclusion
                                                         rules; --no-post stores it
                                                         but never posts it)
  python -m nimi_service rca-text "<text>"               RCA arbitrary text now (manual request, no triage)
  python -m nimi_service show <message|request_id> [--provider P] [--chat | --render] [--all]
                                                         stored RCA of a message (no new run): request, triage,
                                                         report (--chat: the posted chat message; --render: the chat
                                                         message rendered now, current layout, no length limit;
                                                         --all: every attempt)
  python -m nimi_service status                          channels, message counts, request states
  python -m nimi_service config                          effective settings and where each came from (secrets masked)
  python -m nimi_service red-flags [--all]               list open red flags (sensitive data, rule violations, errors)
  python -m nimi_service red-flags resolve <id> [--ignore] [--note "..."]
  python -m nimi_service exclude add [--like <message>] [--signature K] [--channel L] [--sender S] [--title T]
                                     [--status S] [--text X] --reason "..." [--expires 7d] [--yes]
                                                         skip RCA (and triage) for matching bot messages
  python -m nimi_service exclude [list] [--all]          exclusion rules with hit counts
  python -m nimi_service exclude disable|enable <id>
  python -m nimi_service release <request_id>            send a held request (critical sensitive data) to triage anyway
  python -m nimi_service resume                          clear a Claude pause (usage limit / credit / auth) now
"""
import argparse
import logging
import signal
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import db, exclusions, pause, redflags
from .config import describe, env_files, load_settings
from .ingest import backfill_channel, ingest_all, reparse_all
from .parse import PARSER_VERSION, parse_text
from .providers import PROVIDERS, get_provider
from .reactions import reactions_all
from .workflow import RELEASED, run_request, run_triage, scan_input, schedule

log = logging.getLogger("nimi_service")
stop = threading.Event()
ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


def _alembic_config(settings):
    from alembic.config import Config
    cfg = Config(str(ALEMBIC_INI))
    cfg.attributes["url"] = settings.db_url
    return cfg


def _grant_skill_access(settings) -> None:
    from sqlalchemy import create_engine
    from .views import SKILL_VIEWS, grant_skill_access
    engine = create_engine(settings.db_url)
    with engine.begin() as conn:
        granted = grant_skill_access(conn, settings.db_skill_user)
    engine.dispose()
    if granted:
        print(f"skill reader {settings.db_skill_user}: SELECT on {', '.join(SKILL_VIEWS)}")
    else:
        print(f"skill reader role {settings.db_skill_user} not found: RCA history tools won't work until it exists "
              "(create it with db/bootstrap.sh, then re-run `db upgrade`)")


def cmd_run(settings) -> None:
    from .workflow import check_post_settings
    check_post_settings(settings)
    running: set = set()
    with db.session() as s:
        channels = [(c.label, c.provider, c.rca_enabled) for c in db.list_channels(s)]
    if not channels:
        sys.exit("no channels registered; use add-channel first")
    log.info("nimi-service started: channels=%s post_enabled=%s post_target=%s parallel=%s triage_model=%s",
             channels, settings.post_enabled, settings.post_target, settings.max_parallel, settings.triage_model)
    with ThreadPoolExecutor(max_workers=settings.max_parallel) as pool:
        while not stop.is_set():
            try:
                with db.session() as s:
                    n = db.recover_stuck(s, settings.rca_timeout_seconds + 60, settings.max_attempts)
                if n:
                    log.warning("recovered %s stuck request(s)", n)
                ingest_all(settings)
                schedule(settings, pool, running)
                reactions_all(settings)
                redflags.retry_unnotified(settings)
            except Exception as e:
                log.exception("poll cycle failed")
                redflags.record(settings, "error", "high", "service", "A poll cycle failed",
                                details={"error": f"{type(e).__name__}: {str(e)[:200]}"},
                                dedupe_key=f"poll-failed:{type(e).__name__}")
            stop.wait(settings.poll_seconds)
        log.info("stopping; waiting for %s running RCA(s)", len(running))


def _print_latest_result(request_id) -> None:
    with db.session() as s:
        req = db.get_request(s, request_id)
        res = db.latest_result(s, request_id)
    print(f"--- request {request_id}: status={req['status']} triage={req['triage_decision']} "
          f"({(req['triage_reason'] or '')[:200]})")
    if res:
        print(f"--- result {res.id}: outcome={res.outcome} cost=${res.cost_usd} {res.duration_s}s "
              f"model={res.model} origin={res.origin} confidence={res.confidence} self_healing={res.self_healing}\n")
        print(res.report or res.error)


def _result_line(res) -> str:
    return (f"--- result {res.id} ({res.created_at:%Y-%m-%d %H:%M}Z): outcome={res.outcome} cost=${res.cost_usd} "
            f"{res.duration_s}s model={res.model} origin={res.origin} confidence={res.confidence} "
            f"self_healing={res.self_healing} commit={res.commit_sha}")


def _result_text(res, chat: bool) -> str:
    if chat:
        return res.chat_text or "(no chat message: the run produced none)"
    return res.report or res.error or "(empty)"


def _render_chat(settings, s, req: dict, res, subjects) -> str:
    """The chat message re-rendered from the stored report with no length limit (nothing left out), using the
    current layout and links. Read-only: secrets are masked like in posts, but no red flag is raised."""
    from . import projects, scanner
    from .providers import get_provider
    from .rca import format_rca, parse_output
    from .workflow import citation_links, input_sensitive_types
    if res.outcome != "rca" or not res.report or not req.get("provider"):
        return "(no chat message: the run produced none)"
    provider = get_provider(req["provider"], settings)
    reused = [{"subject": x.subject, "rca_id": x.reused_result_id} for x in subjects if x.reused_result_id]
    ids = {r["rca_id"] for r in reused}
    text = format_rca(provider, res.report, reused, db.reply_ids_for_results(s, ids, "thread"), 10 ** 6,
                      input_sensitive_types(req, parse_output(res.report).get("sensitive_data") or []),
                      settings.reply_disclaimer, "", db.earlier_rcas(s, ids),
                      citation_links(s, provider, res.report, "thread"), projects.load(settings),
                      req.get("source_title") or "")
    return ("(rendered now with the current layout and links, no length limit; not the posted copy)\n\n"
            + scanner.mask(text)[0])


def cmd_show(settings, ref: str, provider, chat: bool, show_all: bool, render: bool = False) -> None:
    """A stored request and its results, read-only: never starts a run. `render`: render the chat message from
    the stored report now (current layout and links, no length limit) instead of printing the posted copy;
    useful to read messages that were shortened, and to preview layout changes on past RCAs."""
    if render and chat:
        sys.exit("use either --chat (the posted copy) or --render (rendered now)")
    from .models import RcaResult
    with db.session() as s:
        if ref.isdigit():
            rid = int(ref)
        else:
            found = db.messages_by_external_id(s, ref, provider)
            if not found:
                sys.exit(f"{ref} not found")
            if len(found) > 1:
                sys.exit(f"{ref} exists for several providers; pass --provider")
            request = db.request_for_message(s, found[0].id)
            if request is None:
                sys.exit(f"{ref} has no RCA request (backfilled, a thread reply, a human message, "
                         f"or its channel has RCA off)")
            rid = request.id
        req = db.get_request(s, rid)
        if req is None:
            sys.exit(f"no request {rid}")
        print(f"--- request {rid}: status={req['status']} by={req['requested_by']} "
              f"requested={req['requested_at']:%Y-%m-%d %H:%M}Z attempts={req['attempts']}")
        if req.get("source_external_id"):
            print(f"    message: {req['source_external_id']} in {req['channel_label']}: {req.get('source_title') or ''}")
        if req["triage_decision"]:
            print(f"    triage: {req['triage_decision']} ({req['triage_model']}, ${req['triage_cost_usd']}): "
                  f"{req['triage_reason'] or ''}")
        if req["skip_reason"]:
            print(f"    skipped: {req['skip_reason']}")
        subjects = db.subjects_for(s, rid)
        if subjects:
            print("    subjects: " + ", ".join(
                x.subject + (f" (reused result {x.reused_result_id})" if x.reused_result_id else "") for x in subjects))
        for p in db.posts_for(s, rid):
            print(f"    posted: {p.kind} to {p.destination} at {p.posted_at:%Y-%m-%d %H:%M}Z"
                  + (f" (result {p.result_id})" if p.result_id else ""))
        results = db.results_for(s, rid)
        for res in (results if show_all else results[:1]):
            print(_result_line(res) + "\n")
            print((_render_chat(settings, s, req, res, subjects) if render else _result_text(res, chat)) + "\n")
        if len(results) > 1 and not show_all:
            print(f"({len(results) - 1} earlier attempt(s); --all to show them)")
        reused = sorted({x.reused_result_id for x in subjects if x.reused_result_id})
        for res_id in ([] if results else reused):     # a reused request: show what it pointed to
            res = s.get(RcaResult, res_id)
            print(_result_line(res).replace("--- result", f"--- reused result (request {res.request_id})") + "\n")
            print(_result_text(res, chat) + "\n")
        if not results and not reused:
            print("(no result yet)")


def _run_now(settings, rid) -> None:
    with db.session() as s:
        claimed = db.transition(s, rid, "queued", "running")
    if claimed:
        run_request(settings, rid)


def cmd_rca_message(settings, external_id: str, provider, fresh: bool, no_post: bool = False) -> None:
    """RCA one stored message now. `no_post`: store the result but never post it (not now, and not later by
    the outbox of a running service); read it with `show`."""
    with db.session() as s:
        found = db.messages_by_external_id(s, external_id, provider)
        if not found:
            sys.exit(f"{external_id} not found")
        if len(found) > 1:
            sys.exit(f"{external_id} exists for several providers; pass --provider")
        msg = found[0]
        sig_id = db.signature_for_message(s, msg.id)
        existing = db.request_for_message(s, msg.id)
        if existing and existing.status == "running":
            sys.exit(f"request {existing.id} is already running")
        if existing:
            rid = existing.id
            db.reset_for_manual(s, rid, no_post)
        else:
            rid = db.create_request(s, "message", msg.rendered_text[: settings.max_input_chars],
                                    signature_id=sig_id, source_message_id=msg.id,
                                    requested_by=db.NO_POST if no_post else "manual")
    run_triage(settings, rid, fresh=fresh)      # --fresh: triage only names the subjects; always a new RCA
    _run_now(settings, rid)
    _print_latest_result(rid)


def cmd_rca_text(settings, text: str) -> None:
    parsed = parse_text(text)
    with db.session() as s:
        sig = db.get_or_create_signature(s, parsed["signature_key"], parsed["kind"], parsed["title"],
                                         parsed["service"], PARSER_VERSION, db.now(s))
        rid = db.create_request(s, "manual", text[: settings.max_input_chars], signature_id=sig,
                                requested_by="manual", status="queued")
        db.set_subjects(s, rid, [{"subject": "*", "decision": "new"}])
        req = db.get_request(s, rid)
    scan_input(settings, req)          # manual request: flag sensitive data, but don't hold
    _run_now(settings, rid)
    _print_latest_result(rid)


def _status() -> None:
    with db.session() as s:
        for r in db.status_summary(s):
            print(f"{r['label']:<12} {r['provider']:<6} messages={r['messages']:<6} replies={r['replies']:<6} "
                  f"latest={r['latest']}")
        for r in db.request_summary(s):
            print(f"requests {r['status']:<15} {r['n']}")
        state = pause.get(s)
        if state:
            active = "ACTIVE" if pause.is_active(state) else "expired (clears on next poll)"
            print(f"Claude calls paused ({active}): {state['kind']} until {state['until']:%Y-%m-%d %H:%M} UTC — "
                  f"{state['message'][:120]}")


def cmd_exclude(args) -> None:
    import getpass
    from .models import RcaExclusion
    with db.session() as s:
        labels = exclusions.channel_labels(s)
        now = db.now(s)
        if args.action in ("disable", "enable"):
            rule = s.get(RcaExclusion, args.rule_id) if args.rule_id else None
            if not rule:
                sys.exit(f"usage: exclude {args.action} <id> (no rule {args.rule_id})")
            rule.enabled = args.action == "enable"
            print(f"rule #{rule.id} {args.action}d: {exclusions.describe(rule, labels)}")
            return
        if args.action == "list":
            rules = s.query(RcaExclusion).order_by(RcaExclusion.id).all()
            shown = 0
            for r in rules:
                expired = r.expires_at is not None and r.expires_at <= now
                if not args.all and (not r.enabled or expired):
                    continue
                state = "disabled" if not r.enabled else "expired" if expired else "active"
                until = f" until {r.expires_at:%Y-%m-%d %H:%M}Z" if r.expires_at else ""
                last = f", last {r.last_hit_at:%Y-%m-%d %H:%M}Z" if r.last_hit_at else ""
                print(f"#{r.id} [{state}{until}] {exclusions.describe(r, labels)}\n"
                      f"    reason: {r.reason}  ({r.hit_count} hits{last}; by {r.created_by or '?'})")
                shown += 1
            print(f"{shown} rule(s)" + ("" if args.all else " (active; --all for disabled/expired)"))
            return

        # add
        if not args.reason or not args.reason.strip():
            sys.exit("exclude add needs --reason \"why these messages never need an RCA\"")
        rule = RcaExclusion(reason=args.reason.strip(), created_by=getpass.getuser(),
                            expires_at=exclusions.parse_expiry(args.expires, now),
                            match_signature_key=args.signature, match_sender_external_id=args.sender,
                            match_title=args.title, match_status=args.status,
                            match_text=args.text)
        if args.channel:
            rule.match_channel_id = exclusions.channel_id_for(s, args.channel)
        if args.like:
            msg = exclusions.find_message(s, args.like)
            if msg is None:
                sys.exit(f"no stored top-level message {args.like!r}")
            fields = exclusions.message_fields(s, msg.id)
            if not fields.get("signature_key"):
                sys.exit("that message has no signature (not parsed); use --title / --text / --sender / --channel instead")
            rule.match_signature_key = fields["signature_key"]
        if not any([rule.match_signature_key, rule.match_channel_id, rule.match_sender_external_id,
                    rule.match_title, rule.match_status, rule.match_text]):
            sys.exit("give at least one of --like, --signature, --channel, --sender, --title, --status, --text")
        count, examples = exclusions.preview(s, rule)
        print(f"Rule: {exclusions.describe(rule, labels)}")
        print(f"Reason: {rule.reason}" + (f"  (expires {rule.expires_at:%Y-%m-%d %H:%M}Z)" if rule.expires_at else ""))
        print(f"Would have matched {count} stored bot message(s)" + (", most recent:" if examples else "."))
        for mid, created, title in examples:
            print(f"  #{mid}  {created:%Y-%m-%d %H:%M}Z  {title[:100]}")
        if not args.yes and input("Add this rule? [y/N] ").strip().lower() not in ("y", "yes"):
            print("not added")
            return
        s.add(rule)
        s.flush()
        print(f"added rule #{rule.id}")


def cmd_parse_test(settings, args) -> None:
    """Parse stored messages (or --text) with the current parser and profiles; nothing is written. Shows which
    layout matched, the signature, and a summary of what a `reparse` would change."""
    from collections import Counter
    from . import profiles
    from .parse import PARSER_VERSION, parse_message, parse_text
    prof = profiles.get()
    print(f"parser {PARSER_VERSION}; profiles: {', '.join(prof.files) or '(none)'}; "
          f"layouts: {', '.join(l.label for l in prof.layouts) or '(none)'}")
    if args.text:
        r = parse_text(args.text, sender=args.sender)
        for key in ("kind", "layout", "status", "service", "title", "signature_key"):
            print(f"  {key:<14} {r.get(key)}")
        notes = prof.notes_for(args.text)
        print(f"  {'triage notes':<14} {notes or '(none)'}")
        return
    with db.session() as s:
        rows = db.main_messages_for_parse_test(s, channel=args.channel, sender=args.sender)
    layouts, kinds, changed, sigs, shown = Counter(), Counter(), 0, {}, 0
    for row in rows:
        r = parse_message(get_provider(row.provider, settings).normalize(row.raw))
        if args.kind and r["kind"] != args.kind:
            continue
        layouts[r["layout"] or f"(no layout: {r['kind']})"] += 1
        kinds[r["kind"]] += 1
        sigs.setdefault(r["signature_key"], 0)
        sigs[r["signature_key"]] += 1
        if r["signature_key"] != row.signature_key:
            changed += 1
        if shown < args.limit:
            shown += 1
            print(f"#{row.id} [{row.channel}] {row.sender or '?'}: {r['kind']} via {r['layout'] or '-'}")
            print(f"    title: {r['title'][:120]}")
            print(f"    sig:   {r['signature_key'][:160]}" + ("" if r["signature_key"] == row.signature_key
                                                              else f"\n    was:   {(row.signature_key or '')[:160]}"))
    total = sum(kinds.values())
    print(f"\n{total} message(s); kinds: {dict(kinds)}; layouts: {dict(layouts)}")
    print(f"{len(sigs)} distinct signature(s) with the current parser; {changed} message(s) would change "
          f"signature on `reparse`.")


def cmd_red_flags(args) -> None:
    import getpass
    if args.action == "resolve":
        if not args.flag_id:
            sys.exit("usage: red-flags resolve <id> [--ignore] [--note ...]")
        with db.session() as s:
            ok = redflags.set_status(s, args.flag_id, "ignored" if args.ignore else "resolved",
                                     by=getpass.getuser(), note=args.note)
        print(f"red flag {args.flag_id} {'ignored' if args.ignore else 'resolved'}" if ok else "not found")
        return
    statuses = ("open", "acknowledged", "resolved", "ignored") if args.all else ("open", "acknowledged")
    with db.session() as s:
        flags = redflags.list_flags(s, statuses)
    if not flags:
        print("no red flags")
    for f in flags:
        notified = "notified" if f.notified_at else f"not notified ({f.notify_error})"
        print(f"#{f.id:<5} {f.created_at:%Y-%m-%d %H:%M} {f.severity:<8} {f.kind:<15} {f.source:<8} x{f.occurrences:<3} "
              f"{f.status:<9} {notified}\n       {f.summary}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="nimi_service")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("db", help="database schema: upgrade | revision | current")
    db_sub = p.add_subparsers(dest="db_cmd", required=True)
    db_sub.add_parser("upgrade", help="create / update the schema to the latest version")
    rp = db_sub.add_parser("revision", help="draft a migration from model changes (review it!)")
    rp.add_argument("message")
    db_sub.add_parser("current", help="the database's schema version vs the latest")
    p = sub.add_parser("add-channel")
    p.add_argument("provider", choices=sorted(PROVIDERS))
    p.add_argument("channel", help="the provider's channel ID (gchat: spaces/AAAA... or AAAA...)")
    p.add_argument("label")
    rca = p.add_mutually_exclusive_group()
    rca.add_argument("--rca", dest="rca", action="store_const", const=True,
                     help="enable RCAs for new messages in this channel")
    rca.add_argument("--no-rca", dest="rca", action="store_const", const=False,
                     help="disable RCAs (messages are still ingested as history)")
    p.add_argument("--backfill-days", type=int, help="history pulled on the first poll (default NIMI_BACKFILL_DAYS)")
    p.add_argument("--display-name")
    p.add_argument("--notes")
    p = sub.add_parser("gchat-login", help="one-time Google login; saves the Chat token (NIMI_GCHAT_TOKEN_FILE)")
    p.add_argument("--client-secret", required=True, help="OAuth client file (desktop app) from Google Cloud")
    p = sub.add_parser("gchat-spaces", help="list your Google Chat spaces and their IDs")
    p.add_argument("--all", action="store_true", help="include direct messages and unnamed group chats")
    sub.add_parser("channels")
    sub.add_parser("ingest-once")
    p = sub.add_parser("backfill")
    p.add_argument("channel", help="channel label (e.g. alerts) or the provider's channel ID")
    p.add_argument("--days", type=int, required=True)
    p = sub.add_parser("parse-test", help="how the parser and platform profiles (parsers/*.yaml) read messages")
    p.add_argument("--text", help="parse this text instead of stored messages")
    p.add_argument("--channel", help="only messages from this channel label")
    p.add_argument("--kind", help="only messages the CURRENT parser gives this kind (alert / log / other)")
    p.add_argument("--sender", help="only messages from this sender display name")
    p.add_argument("--limit", type=int, default=20, help="examples to show (default 20)")
    sub.add_parser("reparse")
    sub.add_parser("run")
    sub.add_parser("status")
    p = sub.add_parser("rca")
    p.add_argument("message", help="the provider's message ID")
    p.add_argument("--provider", choices=sorted(PROVIDERS))
    p.add_argument("--fresh", action="store_true")
    p.add_argument("--no-post", action="store_true", help="store the RCA but never post it; read it with `show`")
    p = sub.add_parser("rca-text")
    p.add_argument("text")
    p = sub.add_parser("red-flags")
    p.add_argument("action", nargs="?", choices=["list", "resolve"], default="list")
    p.add_argument("flag_id", nargs="?", type=int)
    p.add_argument("--all", action="store_true", help="include resolved/ignored")
    p.add_argument("--ignore", action="store_true", help="mark as ignored instead of resolved")
    p.add_argument("--note")
    p = sub.add_parser("exclude", help="rules that skip RCA for known harmless / informational messages")
    p.add_argument("action", nargs="?", choices=["list", "add", "disable", "enable"], default="list")
    p.add_argument("rule_id", nargs="?", type=int)
    p.add_argument("--like", help="message (internal or provider ID): match its signature")
    p.add_argument("--signature", help="exact signature key")
    p.add_argument("--channel", help="channel label")
    p.add_argument("--sender", help="provider sender ID, e.g. users/123")
    p.add_argument("--title", help="case-insensitive substring of the title")
    p.add_argument("--status", help="parsed status, e.g. Alerting")
    p.add_argument("--text", help="case-insensitive substring of the full message text")
    p.add_argument("--reason")
    p.add_argument("--expires", help="e.g. 7d, 12h, or an ISO time (default: never)")
    p.add_argument("--yes", action="store_true", help="add without confirmation")
    p.add_argument("--all", action="store_true", help="list: include disabled/expired rules")
    sub.add_parser("config")
    sub.add_parser("resume")
    p = sub.add_parser("show")
    p.add_argument("ref", help="message (the provider's message ID) or request ID")
    p.add_argument("--provider")
    p.add_argument("--chat", action="store_true", help="show the chat message instead of the full report")
    p.add_argument("--all", action="store_true", help="every attempt, not just the latest")
    p.add_argument("--render", action="store_true",
                   help="render the chat message now from the stored report (current layout, no length limit)")
    p = sub.add_parser("release")
    p.add_argument("request_id", type=int)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("googleapiclient.discovery_cache").setLevel(logging.ERROR)
    logging.getLogger("alembic.runtime.plugins").setLevel(logging.WARNING)
    logging.getLogger("alembic.ddl.postgresql").setLevel(logging.WARNING)
    settings = load_settings()
    if args.cmd == "config":
        print("env files (lowest precedence first; environment variables override all):")
        for path in env_files():
            print(f"  {'found  ' if path.exists() else 'missing'} {path}")
        print()
        for key, value, source in describe(settings):
            print(f"{key:<26} {value:<55} {source}")
        return
    if args.cmd == "gchat-login":
        from .providers.gchat.client import login
        if not Path(args.client_secret).is_file():
            sys.exit(f"no such file: {args.client_secret}")
        print("Opening a browser for the Google login (if none opens, use the URL printed below)...")
        login(args.client_secret, settings.gchat_token_file)
        print(f"Saved the Chat token to {settings.gchat_token_file} (chmod 600). The client secret file is not "
              "needed by NIMI any more.")
        return
    if args.cmd == "gchat-spaces":
        from .providers.gchat.client import ChatClient
        spaces = ChatClient(settings.gchat_token_file).list_spaces()
        if not args.all:
            spaces = [sp for sp in spaces if sp.get("displayName")]
        for sp in sorted(spaces, key=lambda x: (x.get("displayName") or "").lower()):
            kind = (sp.get("spaceType") or sp.get("type") or "").lower()
            print(f"{sp['name']:<22} {kind:<12} {sp.get('displayName') or '(direct message)'}")
        print(f"{len(spaces)} space(s){'' if args.all else ' (named; --all for direct messages)'}. Register one with: add-channel gchat <spaces/ID> <label> [--rca]")
        return
    db.init(settings.db_url)

    if args.cmd == "db":
        from alembic import command
        cfg = _alembic_config(settings)
        if args.db_cmd == "upgrade":
            command.upgrade(cfg, "head")
            command.current(cfg)
            _grant_skill_access(settings)
        elif args.db_cmd == "revision":
            command.revision(cfg, message=args.message, autogenerate=True)
            print("Review the new file in db/migrations/versions/ before running `db upgrade`.")
        else:
            from alembic.runtime.migration import MigrationContext
            from alembic.script import ScriptDirectory
            from sqlalchemy import create_engine
            latest = ScriptDirectory.from_config(cfg).get_current_head()
            engine = create_engine(settings.db_url)
            with engine.connect() as conn:
                current = MigrationContext.configure(conn).get_current_revision()
            engine.dispose()
            print(f"database: {current or '(none: run `db upgrade`)'}")
            print(f"latest:   {latest}")
            print("up to date" if current == latest else "upgrade needed: run `db upgrade`")
    elif args.cmd == "add-channel":
        ext = get_provider(args.provider, settings).normalize_channel_id(args.channel)
        with db.session() as s:
            ch = db.add_channel(s, args.provider, ext, args.label, args.display_name, args.rca,
                                args.backfill_days, args.notes, default_backfill_days=settings.backfill_days)
            print(f"{args.provider} {ext} as {ch.label}: rca_enabled={ch.rca_enabled} "
                  f"backfill_days={ch.backfill_days} watermark={ch.watermark}")
    elif args.cmd == "channels":
        with db.session() as s:
            for c in db.list_channels(s, ingest_only=False):
                print(f"{c.label:<12} {c.provider:<6} {c.external_id:<22} ingest={c.ingest_enabled} "
                      f"rca={c.rca_enabled} backfill={c.backfill_days}d watermark={c.watermark}")
    elif args.cmd == "ingest-once":
        ingest_all(settings)
        _status()
    elif args.cmd == "backfill":
        with db.session() as s:
            channel = db.find_channel(s, args.channel)
        if not channel:
            sys.exit(f"channel {args.channel!r} is not registered")
        counts = backfill_channel(settings, get_provider(channel.provider, settings), channel, args.days)
        print(f"backfill {channel.label} ({args.days} days): {counts}")
    elif args.cmd == "parse-test":
        cmd_parse_test(settings, args)
    elif args.cmd == "reparse":
        r = reparse_all(settings)
        print(f"reparsed {r['messages']} messages with {PARSER_VERSION}; "
              f"{r['requests']} RCA request(s) moved to their message's new signature")
        for rule_id, sig in r["orphaned_rules"]:
            print(f"warning: exclusion rule #{rule_id} matches signature {sig!r}, which no message has any more; "
                  f"re-create it with `exclude add --like <message>` and disable #{rule_id}")
    elif args.cmd == "status":
        _status()
    elif args.cmd == "rca":
        cmd_rca_message(settings, args.message, args.provider, args.fresh, args.no_post)
        if args.no_post:
            print(f"\nnot posted (--no-post); see it again with: show {args.message} --chat")
    elif args.cmd == "rca-text":
        cmd_rca_text(settings, args.text)
    elif args.cmd == "red-flags":
        cmd_red_flags(args)
    elif args.cmd == "exclude":
        cmd_exclude(args)
    elif args.cmd == "resume":
        with db.session() as s:
            had = pause.get(s) is not None
            pause.clear(s)
            redflags.resolve_by_prefix(s, "claude-unavailable:", "resolved by admin (resume)")
        print("pause cleared; Claude calls resume on the next poll" if had else "no pause was active")
    elif args.cmd == "show":
        cmd_show(settings, args.ref, args.provider, args.chat, args.all, args.render)
    elif args.cmd == "release":
        with db.session() as s:
            ok = db.transition(s, args.request_id, "held", "triage", skip_reason=RELEASED)
        print(f"request {args.request_id} released to triage" if ok else "request is not held")
    elif args.cmd == "run":
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        signal.signal(signal.SIGINT, lambda *_: stop.set())
        cmd_run(settings)


if __name__ == "__main__":
    main()
