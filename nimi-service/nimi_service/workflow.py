"""Request workflow: triage -> (reused | not_applicable | queued -> running -> done | failed) -> post.

Transactions are kept short: nothing holds a DB transaction open while a model call or the skill runs.
Replies go through the source message's provider (providers/).
"""
import logging

from datetime import datetime, timezone

from . import db, exclusions, pause, projects, redflags, scanner
from .claude_cli import NEEDS_ADMIN, ClaudeUnavailable
from .guardlog import denials_for_session
from .parse import looks_resolved
from .providers import get_provider
from .rca import (SOURCE_LINE_MAX, earlier_label, find_refs, format_limit_notice, format_rca, format_reuse, run_rca,
                  source_line, with_source)
from .triage import triage

log = logging.getLogger(__name__)

RELEASED = "released by admin"


def _flag_ids(req: dict) -> dict:
    return {"message_id": req.get("source_message_id"), "request_id": req["id"], "channel_id": req.get("channel_id")}


_dry_run_noticed = set()     # dry run: log the would-be notice once per request per process


def enter_pause(settings, exc: ClaudeUnavailable) -> None:
    """Pause all Claude calls (usage limit, credit, auth) and raise one red flag for the episode."""
    with db.session() as s:
        state = pause.set(s, exc.kind, exc.message, pause.until_for(settings, exc.reset_at))
    until = state["until"]
    needs_admin = exc.kind in NEEDS_ADMIN
    redflags.record(settings, "error", "critical" if needs_admin else "high", "service",
                    f"Claude unavailable ({exc.kind}) — RCAs paused until {until[:16].replace('T', ' ')} UTC"
                    + (" — needs admin action" if needs_admin else " — will resume automatically"),
                    details={"error": exc.message[:300]}, dedupe_key=f"claude-unavailable:{exc.kind}")
    log.warning("Claude unavailable (%s): pausing RCAs until %s", exc.kind, until)


def handle_pause(settings) -> bool:
    """True if Claude calls are paused right now (and notices were posted). Clears an expired pause."""
    with db.session() as s:
        state = pause.get(s)
        if state and not pause.is_active(state):
            pause.clear(s)
            n = redflags.resolve_by_prefix(s, "claude-unavailable:", "auto-resolved: retrying Claude calls")
            log.info("pause expired (%s); retrying Claude calls", state["kind"])
            if n:
                redflags.notify_text(settings, f"👁️ *NIMI*: retrying Claude calls after a pause ({state['kind']}).")
            return False
    if not state:
        return False
    post_notices(settings, state["until"])
    return True


def post_notices(settings, until) -> None:
    """One notice per pending request (per destination) while Claude is unavailable."""
    for dest in destinations(settings):
        with db.session() as s:
            pending = [r for r in db.pending_requests(s) if not db.has_notice(s, r["id"], dest)]
        for r in pending:
            if looks_resolved(r.get("parser_status")) or not r.get("provider"):
                continue                        # an OK/resolved message (it won't need an RCA), or no source
            with db.session() as s:
                req = db.get_request(s, r["id"])
            if req.get("requested_by") == db.NO_POST:
                continue
            provider = get_provider(req["provider"], settings)
            text = format_limit_notice(provider, settings.reply_disclaimer,
                                       source_line(provider, req) if dest == "webhook" else "")
            if not settings.post_enabled:
                if (r["id"], dest) not in _dry_run_noticed:
                    _dry_run_noticed.add((r["id"], dest))
                    log.info("dry run: not posting delay notice for request %s (%s)", r["id"], dest)
                continue
            _post_one(settings, req, None, "notice", dest, text, [])


def scan_input(settings, req: dict) -> list:
    """Code scan of the request's text before any model sees it. Records a red flag for findings."""
    findings = scanner.scan(req["input_text"])
    if findings:
        types = sorted({f.type for f in findings})
        redflags.record(settings, "sensitive_data", scanner.worst_severity(findings), "scanner",
                        f"Message appears to contain sensitive data ({', '.join(types)}) — request {req['id']}",
                        details={"types": types, "findings": [f.describe() for f in findings][:20]},
                        dedupe_key=f"scanner-input:{req.get('source_message_id') or req['id']}", **_flag_ids(req))
    return findings


def input_sensitive_types(req: dict, skill_items=()) -> list:
    """Types of sensitive data detected in the *input* by the scanner, triage, or the skill (no values)."""
    types = {f.type for f in scanner.scan(req["input_text"])}
    types |= set(((req.get("triage_output") or {}).get("sensitive_types")) or [])
    types |= {i["type"] for i in skill_items if i.get("where") == "input"}
    return sorted(types)


def safe_reply(settings, req: dict, text: str) -> str:
    """Backstop: mask high-confidence secret formats in an outgoing reply, and flag it if anything was masked."""
    masked, findings = scanner.mask(text)
    if findings:
        types = sorted({f.type for f in findings})
        redflags.record(settings, "sensitive_data", scanner.worst_severity(findings), "scanner",
                        f"Masked {len(findings)} sensitive item(s) ({', '.join(types)}) in the reply for request "
                        f"{req['id']}", details={"types": types, "where": "outgoing reply"},
                        dedupe_key=f"reply-masked:{req['id']}", **_flag_ids(req))
    return masked


def run_triage(settings, request_id: int, candidates_as_of=None, fresh: bool = False) -> None:
    """History = same-signature messages before this message; candidates = RCAs up to `candidates_as_of`
    (default now). Replays of past messages pass the message time so they only see RCAs that existed then.
    `fresh` (manual `rca --fresh`): triage only names the subjects. No exclusion rules, no history or reuse
    candidates, no hold on secrets (flagged only), and the decision is always a new RCA for every subject."""
    with db.session() as s:
        req = db.get_request(s, request_id)
        if not req or req["status"] != "triage":
            return
        if req["source_message_id"] and not fresh:
            # Rules added after the message arrived (e.g. while requests waited out a usage-limit pause).
            rule = exclusions.match(s, exclusions.message_fields(s, req["source_message_id"]), db.now(s))
            if rule:
                db.transition(s, request_id, "triage", "skipped", skip_reason=exclusions.skip_reason(rule))
                log.info("request %s %s", request_id, exclusions.skip_reason(rule))
                return
        msg_time = req["message_created_at"] or req["requested_at"]
        as_of = candidates_as_of or db.now(s)
        history, candidates = [], []
        if req["signature_id"] and not fresh:
            history = db.signature_history(s, req["signature_id"], msg_time, settings.reuse_days,
                                           settings.history_limit)
            candidates = db.reuse_candidates(s, req["signature_id"], as_of, settings.reuse_days, req["id"])
        req["parser_status"] = db.parser_status(s, req["source_message_id"]) if req["source_message_id"] else None

    # Code scan before any model call; critical findings hold the request until an admin releases it.
    findings = scan_input(settings, req)
    if scanner.worst_severity(findings) == "critical" and req.get("skip_reason") != RELEASED and not fresh:
        types = ", ".join(sorted({f.type for f in findings if f.severity == "critical"}))
        with db.session() as s:
            db.transition(s, request_id, "triage", "held", skip_reason=f"held: critical sensitive data ({types})")
        log.warning("request %s held: critical sensitive data (%s)", request_id, types)
        return

    try:
        t = triage(settings, req, history, candidates)      # model call: outside any transaction
    except ClaudeUnavailable as e:
        enter_pause(settings, e)                            # request stays in 'triage' and is retried later
        return
    if t.get("sensitive_types"):
        redflags.record(settings, "sensitive_data", "high", "triage",
                        f"Triage reports sensitive data in the message ({', '.join(t['sensitive_types'])}) — "
                        f"request {request_id}", details={"types": t["sensitive_types"]},
                        dedupe_key=f"triage-sensitive:{req.get('source_message_id') or request_id}", **_flag_ids(req))

    if fresh:                                               # a new RCA for every subject triage named
        reason = f"manual --fresh (triage: {t['decision']} — {t['reason'] or ''})"
        t = {**t, "decision": "new", "reason": reason[:500], "reuse": [],
             "new_subjects": t["new_subjects"] or [r["subject"] for r in t["reuse"]] or ["*"]}
    fields = dict(triage_decision=t["decision"], triage_reason=t["reason"], triage_output=t["raw"],
                  triage_model=t["model"], triage_cost_usd=t["cost_usd"])
    subjects = [{"subject": x, "decision": "new"} for x in t["new_subjects"]] + \
               [{"subject": r["subject"], "decision": "reused", "reused_result_id": r["rca_id"]} for r in t["reuse"]]
    target = {"not_applicable": "not_applicable", "reuse": "reused"}.get(t["decision"], "queued")
    with db.session() as s:
        db.set_subjects(s, request_id, subjects)
        moved = db.transition(s, request_id, "triage", target, **fields)
    log.info("triage request=%s decision=%s reason=%s", request_id, t["decision"], (t["reason"] or "")[:160])
    if moved and target == "reused":
        post_reuse(settings, request_id, t["reuse"])


def run_request(settings, request_id: int) -> None:
    """Runs in a worker thread. The request is already 'running'."""
    try:
        with db.session() as s:
            req = db.get_request(s, request_id)
            subj = db.subjects_for(s, request_id)
        new_subjects = [x.subject for x in subj if x.decision == "new"] or ["*"]
        reused = [{"subject": x.subject, "rca_id": x.reused_result_id} for x in subj if x.decision == "reused"]

        log.info("RCA start request=%s new=%s reused=%s", request_id, new_subjects[:5], len(reused))
        result = run_rca(settings, req, new_subjects, reused)   # minutes: outside any transaction
        if result.get("unavailable"):                           # usage limit / credit / auth: pause, retry later
            enter_pause(settings, result["unavailable"])
            with db.session() as s:
                db.requeue_without_attempt(s, request_id)
            return
        _flag_run(settings, req, result)

        texts, chat_problems = {}, []
        with db.session() as s:
            if result.get("outcome") == "rca" and req.get("provider"):
                provider = get_provider(req["provider"], settings)
                earlier = db.earlier_rcas(s, {r["rca_id"] for r in reused})
                registry = projects.load(settings)
                for dest in destinations(settings):
                    replies = db.reply_ids_for_results(s, {r["rca_id"] for r in reused}, dest)
                    # room for a source line is kept in every text, so the outbox can add one later
                    text = format_rca(provider, result.get("report"), reused, replies,
                                      settings.post_max_chars - SOURCE_LINE_MAX,
                                      input_sensitive_types(req, result.get("sensitive_data") or []),
                                      settings.reply_disclaimer, source_line(provider, req) if dest == "webhook" else "",
                                      earlier, citation_links(s, provider, result.get("report"), dest), registry,
                                      req.get("source_title") or "", chat_problems)
                    texts[dest] = safe_reply(settings, req, text)
                result["chat_text"] = texts.get("thread") or with_source_removed(texts)
            result_id = db.insert_result(s, request_id, result)
            if result["outcome"] in ("rca", "not_applicable", "insufficient_context"):
                final = "not_applicable" if result["outcome"] == "not_applicable" else "done"
                db.transition(s, request_id, "running", final)
            else:
                nxt = "queued" if req["attempts"] < settings.max_attempts else "failed"
                db.transition(s, request_id, "running", nxt, skip_reason=result.get("error"))
        if chat_problems:
            _flag_chat(settings, req, chat_problems)
        log.info("RCA %s request=%s result=%s cost=$%s %ss", result["outcome"], request_id, result_id,
                 result.get("cost_usd"), result.get("duration_s"))

        if result["outcome"] == "rca" and texts:
            post(settings, req, result_id, "mixed" if reused else "rca", texts, [r["rca_id"] for r in reused])
    except Exception as e:
        log.exception("RCA processing failed for request %s", request_id)
        redflags.record(settings, "error", "high", "service", f"RCA processing crashed for request {request_id}",
                        details={"request_id": request_id, "error": f"{type(e).__name__}: {str(e)[:200]}"},
                        dedupe_key=f"rca-crash:{request_id}", request_id=request_id)


def _flag_chat(settings, req: dict, problems: list) -> None:
    """A low red flag when the skill's chat message needed a fallback, had invalid parts, or didn't fit: a sign
    the chat template or its length budget needs attention (the post itself still goes out)."""
    summary = "; ".join(dict.fromkeys(problems))
    log.info("request %s: chat message problems: %s", req["id"], summary)
    redflags.record(settings, "error", "low", "skill", f"Chat message for request {req['id']}: {summary[:200]}",
                    details={"request_id": req["id"], "reason": summary[:300]},
                    dedupe_key=f"chat:{req['id']}", **_flag_ids(req))


def _flag_run(settings, req: dict, result: dict) -> None:
    """Red flags from one skill run: errors, sensitive data the skill reported, and guard denials."""
    if result.get("outcome") in ("error", "timeout"):
        redflags.record(settings, "error", "high", "service",
                        f"RCA {result['outcome']} for request {req['id']}: {(result.get('error') or '')[:200]}",
                        details={"request_id": req["id"], "error": (result.get("error") or "")[:300]},
                        dedupe_key=f"rca-{result['outcome']}:{req['id']}", **_flag_ids(req))
    items = result.get("sensitive_data") or []
    if items:
        labels = sorted({f"{i['where']}:{i['type']}" for i in items})
        redflags.record(settings, "sensitive_data", "high", "skill",
                        f"The RCA skill noticed sensitive data ({', '.join(labels)}) — request {req['id']}",
                        details={"types": labels}, dedupe_key=f"skill-sensitive:{req['id']}", **_flag_ids(req))
    denials = denials_for_session(settings.guard_denial_log, result.get("session_id"))
    serious = [d for d in denials if d.get("severity") != "low"]
    for d in denials:
        redflags.record(settings, "rule_violation", d.get("severity") or "high", "guard",
                        f"Guard blocked {d.get('tool')} during request {req['id']}: {(d.get('reason') or '')[:160]}",
                        details={"tool": d.get("tool"), "reason": (d.get("reason") or "")[:300],
                                 "path": (d.get("input") or {}).get("path") or (d.get("input") or {}).get("command"),
                                 "request_id": req["id"]},
                        dedupe_key=f"guard:{req['id']}:{d.get('tool')}:{(d.get('reason') or '')[:80]}",
                        notify=d.get("severity") != "low", **_flag_ids(req))
    if denials:
        log.info("request %s: %d guard denial(s), %d serious", req["id"], len(denials), len(serious))


def post_reuse(settings, request_id: int, reuse: list, only: str = None) -> None:
    with db.session() as s:
        req = db.get_request(s, request_id)
    if not req.get("provider"):
        return
    provider = get_provider(req["provider"], settings)
    texts = {}
    for dest in destinations(settings) if only is None else [only]:
        with db.session() as s:
            replies = db.reply_ids_for_results(s, {r["rca_id"] for r in reuse}, dest)   # links in the same space
            earlier = db.earlier_rcas(s, {r["rca_id"] for r in reuse})
        text = format_reuse(provider, reuse, replies, settings.post_max_chars, input_sensitive_types(req),
                            settings.reply_disclaimer, source_line(provider, req) if dest == "webhook" else "",
                            earlier)
        texts[dest] = safe_reply(settings, req, text)
    post(settings, req, None, "reuse", texts, [r["rca_id"] for r in reuse])


def with_source_removed(texts: dict) -> str:
    """The stored reply text (rca_results.chat_text) is the thread form: without the webhook source line."""
    lines = texts.get("webhook", "").split("\n")
    return "\n".join(l for i, l in enumerate(lines) if not (i in (1, 2, 3) and l.startswith("🔔 ")))


def citation_links(s, provider, report: str, dest: str) -> dict:
    """Links for the citations in a report: cited bot messages -> the original message; cited earlier RCAs ->
    their post in the same destination (else in the thread). Anything not found stays plain text."""
    msg_ids, rca_ids = find_refs(report)
    links = {}
    for mid, (ext, created, channel) in db.bot_messages_for_citation(s, msg_ids).items():
        links[("msg", mid)] = provider.message_link(ext)
        # readable link text if the skill used the ID as the label: "<channel>, Mar 3 14:20 UTC"
        links[("msg-label", mid)] = f"{channel}, {created.astimezone(timezone.utc):%b %d %H:%M} UTC"
    posts = {**db.reply_ids_for_results(s, rca_ids, "thread"), **db.reply_ids_for_results(s, rca_ids, dest)}
    links.update({("rca", rid): provider.message_link(ext) for rid, ext in posts.items() if ext})
    # readable link text if the skill used the ID as the label: the RCA's title and time, as in reuse replies
    links.update({("rca-label", rid): earlier_label(info) for rid, info in db.earlier_rcas(s, rca_ids).items()})
    return links


def destinations(settings) -> list:
    """Where replies go: the alert's thread, the NIMI_POST_WEBHOOK space, or both."""
    return {"thread": ["thread"], "webhook": ["webhook"], "both": ["thread", "webhook"]}[settings.post_target]


def check_post_settings(settings) -> None:
    """Fail fast (at `run`) on a webhook target without a usable webhook URL."""
    if settings.post_enabled and "webhook" in destinations(settings):
        from .providers.gchat.client import webhook_space
        if not settings.post_webhook:
            raise SystemExit("NIMI_POST_TARGET includes webhook but NIMI_POST_WEBHOOK is not set")
        try:
            webhook_space(settings.post_webhook)
        except ValueError as e:
            raise SystemExit(str(e)) from None


def post(settings, req: dict, result_id, kind: str, texts: dict, reused_ids: list) -> None:
    """Post `texts` ({destination: text}) for a request. Dry run unless NIMI_POST_ENABLED."""
    if not settings.post_enabled:
        log.info("dry run: not posting %s for request %s (%s)", kind, req["id"], ", ".join(texts))
        return
    if req.get("requested_by") == db.NO_POST:
        log.info("not posting %s for request %s: manual run with --no-post", kind, req["id"])
        return
    if not req.get("provider"):
        return
    for dest, text in texts.items():
        _post_one(settings, req, result_id, kind, dest, text, reused_ids)


def _post_one(settings, req: dict, result_id, kind: str, dest: str, text: str, reused_ids: list) -> None:
    provider = get_provider(req["provider"], settings)
    try:
        if dest == "thread":
            if not req.get("thread_external_id"):
                return
            thread, reply = req["thread_external_id"], provider.post_reply(
                req["channel_external_id"], req["thread_external_id"], text)
        else:
            # One thread per alert in the destination space: the first post for an alert is a new top-level
            # message; later posts for the same alert (re-runs, reuse, notices) reply in its thread.
            reply, thread = provider.post_webhook(settings.post_webhook, text,
                                                  f"nimi-{req.get('source_message_id') or req['id']}")
    except Exception as e:
        log.error("posting %s (%s) for request %s failed: %s", kind, dest, req["id"], type(e).__name__)
        redflags.record(settings, "error", "high" if kind != "notice" else "medium", "service",
                        f"Posting the {kind} reply ({dest}) for request {req['id']} failed",
                        details={"request_id": req["id"], "destination": dest,
                                 "error": f"{type(e).__name__}: {str(e)[:200]}"},
                        dedupe_key=f"post-failed:{dest}:{req['id']}", **_flag_ids(req))
        return
    with db.session() as s:
        db.insert_post(s, req["id"], result_id, kind, req["channel_id"], thread or "", reply or "",
                       reused_ids, destination=dest)
    log.info("posted %s for request %s (%s) -> %s", kind, req["id"], dest, reply)


def post_pending_replies(settings) -> None:
    """Outbox sweep: post replies that were never posted (service stopped between finishing and posting).
    Needs no Claude, so it also runs while Claude calls are paused."""
    if not settings.post_enabled:
        return
    for dest in destinations(settings):
        with db.session() as s:
            pending = db.unposted_replies(s, settings.stale_hours, dest)
        for item in pending:
            rid = item["request_id"]
            with db.session() as s:
                req = db.get_request(s, rid)
                subj = db.subjects_for(s, rid)
            reused = [{"subject": x.subject, "rca_id": x.reused_result_id} for x in subj if x.decision == "reused"]
            log.info("posting a missed reply for request %s (%s, %s)", rid, item["status"], dest)
            if item["status"] == "reused":
                post_reuse(settings, rid, reused, only=dest)
            else:
                text = item["chat_text"]
                if dest == "webhook":
                    text = with_source(text, source_line(get_provider(req["provider"], settings), req))
                post(settings, req, item["result_id"], "mixed" if reused else "rca", {dest: text},
                     [r["rca_id"] for r in reused])


def sync_projects(settings) -> None:
    """Opt-in sync of NIMI-only project copies ("sync" in the registry), at most every NIMI_SYNC_MINUTES."""
    updated, problems = projects.sync_all(settings)
    for path in updated:
        log.info("synced project checkout %s", path)
    for problem in problems:
        log.warning("project sync: %s", problem)
        redflags.record(settings, "error", "medium", "service", f"Project sync skipped or failed: {problem}",
                        details={"problem": problem[:300]}, dedupe_key=f"project-sync:{problem[:80]}")


def schedule(settings, pool, running: set) -> None:
    post_pending_replies(settings)              # missed replies first (no Claude needed)
    if handle_pause(settings):
        return                                  # Claude unavailable: wait (ingestion keeps running)
    with db.session() as s:
        stale = db.skip_stale(s, settings.stale_hours)
    if stale:
        log.info("skipped %d stale request(s) older than %dh: %s", len(stale), settings.stale_hours, stale[:10])
    with db.session() as s:
        to_triage = db.requests_in_status(s, "triage", limit=20)
    for rid in to_triage:
        try:
            run_triage(settings, rid)
        except Exception:
            log.exception("triage crashed for request %s", rid)
        with db.session() as s:
            if pause.is_active(pause.get(s)):
                return                          # a limit was hit during this batch: stop here

    free = settings.max_parallel - len(running)
    if free <= 0:
        return
    if not running:
        sync_projects(settings)                 # only between runs: no RCA sees a checkout change under it
    with db.session() as s:
        queued = db.requests_in_status(s, "queued", limit=free)
    for rid in queued:
        with db.session() as s:
            if pause.is_active(pause.get(s)):
                return
            if db.runs_started_today(s) >= settings.max_rcas_per_day:
                db.transition(s, rid, "queued", "skipped",
                              skip_reason=f"daily cap {settings.max_rcas_per_day} reached")
                capped = True
            else:
                capped = False
        if capped:
            today = datetime.now(timezone.utc).date().isoformat()
            redflags.record(settings, "error", "medium", "service",
                            f"Daily RCA cap ({settings.max_rcas_per_day}) reached; further requests today are skipped",
                            details={"count": settings.max_rcas_per_day}, dedupe_key=f"daily-cap:{today}")
            continue
        with db.session() as s:
            claimed = db.transition(s, rid, "queued", "running")
        if claimed:
            running.add(rid)
            fut = pool.submit(run_request, settings, rid)
            fut.add_done_callback(lambda _f, r=rid: running.discard(r))
