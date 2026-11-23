"""Database access for nimi-service: SQLAlchemy 2.0 ORM over Postgres (psycopg 3 driver).

Usage:
    db.init(settings.db_url)               # once per process
    with db.session() as s:                # one unit of work = one transaction (commit on success)
        db.add_channel(s, ...)
Sessions are not shared across threads; each worker opens its own `db.session()`.
Messages are identified internally by numeric IDs, and externally by (provider, external_id).
"""
import re
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Iterator, Optional

from sqlalchemy import and_, case, create_engine, delete, func, literal_column, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from .models import (Channel, Message, MessageParse, RcaComment, RcaCommentResult, RcaCommentVerdict, RcaFeedback,
                     RcaPost, RcaPostReuseLink, RcaRequest, RcaRequestSubject, RcaResult, Reaction, Signature,
                     ThreadMessage)

_SessionLocal: Optional[sessionmaker] = None

MESSAGE_FIELDS = ("provider", "external_id", "channel_id", "thread_external_id", "created_at", "last_update_at",
                  "sender_external_id", "sender_type", "sender_display_name", "rendered_text", "raw",
                  "reaction_summary", "is_backfill")
MESSAGE_EDITABLE = ("last_update_at", "rendered_text", "raw", "reaction_summary")
FINAL_STATUSES = ("done", "failed", "reused", "not_applicable", "skipped")


def init(url) -> None:
    """`url`: a SQLAlchemy URL (Settings.db_url)."""
    global _SessionLocal
    engine = create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5)
    _SessionLocal = sessionmaker(engine, expire_on_commit=False)


@contextmanager
def session() -> Iterator[Session]:
    if _SessionLocal is None:
        raise RuntimeError("db.init() was not called")
    with _SessionLocal() as s, s.begin():
        yield s


def now(s: Session) -> datetime:
    return s.execute(select(func.now())).scalar_one()


# --- channels -------------------------------------------------------------------

def add_channel(s: Session, provider, external_id, label, display_name=None, rca_enabled=None,
                backfill_days=None, notes=None, default_backfill_days=90) -> Channel:
    """Register a channel, or update an existing one in place (same row, watermark and messages kept).
    None means "not given": a new channel gets the default (RCA off, default_backfill_days); an existing
    channel keeps its current value. The label is always set."""
    stmt = pg_insert(Channel).values(provider=provider, external_id=external_id, label=label,
                                     display_name=display_name, rca_enabled=bool(rca_enabled),
                                     backfill_days=backfill_days or default_backfill_days, notes=notes)
    keep = lambda given, column: column if given is None else getattr(stmt.excluded, column.key)
    s.execute(stmt.on_conflict_do_update(
        constraint="channels_provider_external_id_key",
        set_={"label": stmt.excluded.label,
              "display_name": func.coalesce(stmt.excluded.display_name, Channel.display_name),
              "rca_enabled": keep(rca_enabled, Channel.rca_enabled),
              "backfill_days": keep(backfill_days, Channel.backfill_days),
              "notes": func.coalesce(stmt.excluded.notes, Channel.notes), "updated_at": func.now()}))
    return s.scalars(select(Channel).where(Channel.provider == provider, Channel.external_id == external_id)).one()


def list_channels(s: Session, ingest_only=True) -> list:
    q = select(Channel).order_by(Channel.label)
    if ingest_only:
        q = q.where(Channel.ingest_enabled.is_(True))
    return list(s.scalars(q))


def find_channel(s: Session, label_or_external_id: str) -> Optional[Channel]:
    return s.scalars(select(Channel).where(or_(Channel.label == label_or_external_id,
                                               Channel.external_id == label_or_external_id))).first()


def set_watermark(s: Session, channel_id, ts) -> None:
    s.execute(update(Channel).where(Channel.id == channel_id).values(
        watermark=func.greatest(func.coalesce(Channel.watermark, ts), ts), updated_at=func.now()))


def set_reactions_checked(s: Session, channel_id) -> None:
    s.execute(update(Channel).where(Channel.id == channel_id).values(reactions_checked_at=func.now()))


# --- messages -----------------------------------------------------------------

def upsert_message(s: Session, model, row: dict):
    """Insert or refresh a message in Message / ThreadMessage.
    Returns (change, id): change is "inserted", "updated" (edited / reactions changed), or None (unchanged;
    id is then None too)."""
    stmt = pg_insert(model).values({k: row[k] for k in MESSAGE_FIELDS})
    stmt = stmt.on_conflict_do_update(
        constraint=f"{model.__tablename__}_provider_external_id_key",
        set_={**{c: getattr(stmt.excluded, c) for c in MESSAGE_EDITABLE}, "updated_at": func.now()},
        where=or_(model.last_update_at.is_distinct_from(stmt.excluded.last_update_at),
                  model.reaction_summary.is_distinct_from(stmt.excluded.reaction_summary)),
    ).returning(model.id, literal_column("(xmax = 0)").label("inserted"))
    r = s.execute(stmt).first()
    if r is None:
        return None, None
    return ("inserted" if r.inserted else "updated"), r.id


def stored_reaction_summaries(s: Session, provider, external_ids: list) -> dict:
    out = {}
    if external_ids:
        for model in (Message, ThreadMessage):
            for ext, summary in s.execute(select(model.external_id, model.reaction_summary)
                                          .where(model.provider == provider, model.external_id.in_(external_ids))):
                out[ext] = summary or {}
    return out


def update_reaction_summary(s: Session, provider, external_id, summary: dict) -> None:
    for model in (Message, ThreadMessage):
        s.execute(update(model).where(model.provider == provider, model.external_id == external_id)
                  .values(reaction_summary=summary, updated_at=func.now()))


def messages_by_external_id(s: Session, external_id, provider=None) -> list:
    q = select(Message).where(Message.external_id == external_id)
    if provider:
        q = q.where(Message.provider == provider)
    return list(s.scalars(q))


def all_main_messages(s: Session) -> list:
    """(id, provider, raw, created_at) for every top-level message; loaded at once (a few thousand rows)."""
    return s.execute(select(Message.id, Message.provider, Message.raw, Message.created_at)
                     .order_by(Message.created_at)).all()


def main_messages_for_parse_test(s: Session, channel=None, sender=None) -> list:
    """Top-level bot messages, newest first, with their current signature (for `parse-test`; read-only)."""
    q = (select(Message.id, Message.provider, Message.raw, Channel.label.label("channel"),
                Message.sender_display_name.label("sender"), Signature.signature_key)
         .join(Channel, Channel.id == Message.channel_id)
         .outerjoin(MessageParse, MessageParse.message_id == Message.id)
         .outerjoin(Signature, Signature.id == MessageParse.signature_id)
         .where(Message.sender_type == "bot").order_by(Message.created_at.desc()))
    if channel:
        q = q.where(Channel.label == channel)
    if sender:
        q = q.where(Message.sender_display_name == sender)
    return s.execute(q).all()


# --- parses / signatures --------------------------------------------------------

def get_or_create_signature(s: Session, key, kind, title, service, parser_version, seen_at) -> int:
    stmt = pg_insert(Signature).values(signature_key=key, kind=kind, sample_title=title, service=service,
                                       parser_version=parser_version, first_seen_at=seen_at)
    stmt = stmt.on_conflict_do_update(
        index_elements=[Signature.signature_key],
        set_={"first_seen_at": func.least(Signature.first_seen_at, stmt.excluded.first_seen_at)},
    ).returning(Signature.id)
    return s.execute(stmt).scalar_one()


def upsert_parse(s: Session, message_id: int, parsed: dict, signature_id: int, parser_version: str) -> None:
    values = dict(message_id=message_id, parser_version=parser_version, kind=parsed["kind"], title=parsed["title"],
                  status=parsed["status"], service=parsed["service"], signature_id=signature_id)
    stmt = pg_insert(MessageParse).values(values)
    s.execute(stmt.on_conflict_do_update(
        index_elements=[MessageParse.message_id],
        set_={**{k: getattr(stmt.excluded, k) for k in values if k != "message_id"}, "parsed_at": func.now()}))


def signature_for_message(s: Session, message_id: int) -> Optional[int]:
    return s.scalar(select(MessageParse.signature_id).where(MessageParse.message_id == message_id))


def parser_status(s: Session, message_id: int) -> Optional[str]:
    return s.scalar(select(MessageParse.status).where(MessageParse.message_id == message_id))


# --- requests -----------------------------------------------------------------

def resync_request_signatures(s: Session) -> int:
    """After a reparse: give message-sourced requests their message's current signature."""
    res = s.execute(text(
        "UPDATE rca_requests r SET signature_id = p.signature_id FROM message_parses p "
        "WHERE r.source_message_id = p.message_id AND r.signature_id IS DISTINCT FROM p.signature_id"))
    return res.rowcount


def exclusions_with_unknown_signature(s: Session) -> list:
    """(id, signature) of enabled rules whose signature no message has any more."""
    return [tuple(r) for r in s.execute(text(
        "SELECT e.id, e.match_signature_key FROM rca_exclusions e WHERE e.enabled "
        "AND e.match_signature_key IS NOT NULL AND NOT EXISTS (SELECT 1 FROM signatures sig "
        "JOIN message_parses p ON p.signature_id = sig.id WHERE sig.signature_key = e.match_signature_key) "
        "ORDER BY e.id")).all()]


def create_request(s: Session, source_type, input_text, signature_id=None, source_message_id=None,
                   requested_by="service", status="triage", skip_reason=None) -> Optional[int]:
    stmt = pg_insert(RcaRequest).values(
        source_type=source_type, source_message_id=source_message_id, input_text=input_text,
        signature_id=signature_id, requested_by=requested_by, status=status, skip_reason=skip_reason,
    ).on_conflict_do_nothing().returning(RcaRequest.id)
    return s.execute(stmt).scalar()


def get_request(s: Session, request_id) -> Optional[dict]:
    """The request as a dict, plus its source message's provider, channel, thread, external ID, sender name,
    channel label and parsed title (if any), and the request's signature key."""
    row = s.execute(
        select(RcaRequest, Message.provider, Message.channel_id, Channel.external_id.label("channel_external_id"),
               Message.thread_external_id, Message.created_at.label("message_created_at"),
               Message.external_id.label("source_external_id"), Message.sender_display_name,
               Channel.label.label("channel_label"),
               MessageParse.title.label("source_title"), Signature.signature_key)
        .outerjoin(Message, Message.id == RcaRequest.source_message_id)
        .outerjoin(Channel, Channel.id == Message.channel_id)
        .outerjoin(MessageParse, MessageParse.message_id == Message.id)
        .outerjoin(Signature, Signature.id == RcaRequest.signature_id)
        .where(RcaRequest.id == request_id)).first()
    if row is None:
        return None
    req = row.RcaRequest
    d = {c.key: getattr(req, c.key) for c in RcaRequest.__table__.columns}
    d.update(provider=row.provider, channel_id=row.channel_id, channel_external_id=row.channel_external_id,
             thread_external_id=row.thread_external_id, message_created_at=row.message_created_at,
             source_external_id=row.source_external_id, sender_display_name=row.sender_display_name,
             channel_label=row.channel_label,
             source_title=row.source_title, signature_key=row.signature_key)
    return d


def request_for_message(s: Session, message_id: int) -> Optional[RcaRequest]:
    return s.scalars(select(RcaRequest).where(RcaRequest.source_message_id == message_id)).first()


# requested_by of a manual run whose replies must never be posted (`rca --no-post`): checked by every posting
# path, including the outbox of a running service.
NO_POST = "manual-no-post"


def reset_for_manual(s: Session, request_id, no_post: bool = False) -> None:
    s.execute(update(RcaRequest).where(RcaRequest.id == request_id).values(
        status="triage", triage_decision=None, triage_reason=None, triage_output=None, skip_reason=None,
        requested_by=NO_POST if no_post else "manual", updated_at=func.now()))


def requests_in_status(s: Session, status, limit) -> list:
    return list(s.scalars(select(RcaRequest.id).where(RcaRequest.status == status)
                          .order_by(RcaRequest.requested_at).limit(limit)))


def transition(s: Session, request_id, from_status, to_status, **fields) -> bool:
    """Atomic status change (only if the request is in from_status); extra columns are set too."""
    values = {"status": to_status, "updated_at": func.now(), **fields}
    if to_status == "running":
        values.update(attempts=RcaRequest.attempts + 1, started_at=func.now())
    if to_status in FINAL_STATUSES:
        values["finished_at"] = func.now()
    res = s.execute(update(RcaRequest).where(RcaRequest.id == request_id, RcaRequest.status == from_status)
                    .values(**values))
    return res.rowcount == 1


def requeue_without_attempt(s: Session, request_id) -> bool:
    """running -> queued, giving back the attempt (the run never really happened, e.g. a usage limit)."""
    res = s.execute(update(RcaRequest).where(RcaRequest.id == request_id, RcaRequest.status == "running")
                    .values(status="queued", attempts=func.greatest(RcaRequest.attempts - 1, 0),
                            started_at=None, updated_at=func.now()))
    return res.rowcount == 1


def pending_requests(s: Session) -> list:
    """Requests waiting for Claude (triage/queued) that came from a message, with what's needed to notify."""
    rows = s.execute(
        select(RcaRequest.id, RcaRequest.status, RcaRequest.requested_at, Message.created_at.label("message_created_at"),
               Message.provider, Message.channel_id, Channel.external_id.label("channel_external_id"),
               Message.thread_external_id, MessageParse.status.label("parser_status"))
        .join(Message, Message.id == RcaRequest.source_message_id)
        .join(Channel, Channel.id == Message.channel_id)
        .outerjoin(MessageParse, MessageParse.message_id == Message.id)
        .where(RcaRequest.status.in_(("triage", "queued")))
        .order_by(RcaRequest.requested_at)).mappings().all()
    return [dict(r) for r in rows]


def has_notice(s: Session, request_id, destination="thread") -> bool:
    return s.scalar(select(func.count()).select_from(RcaPost)
                    .where(RcaPost.request_id == request_id, RcaPost.kind == "notice",
                           RcaPost.destination == destination)) > 0


def is_nimi_post(s: Session, external_id) -> bool:
    """True if NIMI posted this message itself (e.g. a webhook post into a space that is also registered)."""
    return s.scalar(select(func.count()).select_from(RcaPost).where(RcaPost.reply_external_id == external_id)) > 0


def skip_stale(s: Session, older_than_hours) -> list:
    """Pending requests whose message is older than the limit -> skipped. Returns their ids."""
    cutoff = func.now() - func.make_interval(0, 0, 0, 0, older_than_hours)
    msg_time = select(Message.created_at).where(Message.id == RcaRequest.source_message_id).scalar_subquery()
    res = s.execute(update(RcaRequest)
                    .where(RcaRequest.status.in_(("triage", "queued")),
                           func.coalesce(msg_time, RcaRequest.requested_at) < cutoff)
                    .values(status="skipped", finished_at=func.now(), updated_at=func.now(),
                            skip_reason=f"stale: alert older than {older_than_hours}h when NIMI could process it")
                    .returning(RcaRequest.id))
    return list(res.scalars())


def unposted_replies(s: Session, stale_hours, destination="thread", settle_minutes=5) -> list:
    """Finished requests whose reply was never posted (e.g. the service stopped right after the RCA finished):
    done with an RCA result, or reused — from a message with a thread, alert younger than `stale_hours`,
    finished more than `settle_minutes` ago (so replies a worker is posting right now aren't duplicated)."""
    posted = select(RcaPost.id).where(RcaPost.request_id == RcaRequest.id, RcaPost.destination == destination,
                                      RcaPost.kind.in_(("rca", "mixed", "reuse"))).exists()
    needs_thread = Message.thread_external_id.isnot(None) if destination == "thread" else Message.id.isnot(None)
    rows = s.execute(
        select(RcaRequest.id, RcaRequest.status)
        .join(Message, Message.id == RcaRequest.source_message_id)
        .where(RcaRequest.status.in_(("done", "reused")), ~posted, needs_thread,
               RcaRequest.requested_by.is_distinct_from(NO_POST),
               Message.created_at > func.now() - func.make_interval(0, 0, 0, 0, stale_hours),
               RcaRequest.finished_at < func.now() - func.make_interval(0, 0, 0, 0, 0, settle_minutes))
        .order_by(RcaRequest.finished_at)).all()
    out = []
    for r in rows:
        if r.status == "done":
            res = s.scalars(select(RcaResult).where(RcaResult.request_id == r.id, RcaResult.outcome == "rca")
                            .order_by(RcaResult.id.desc()).limit(1)).first()
            if not res or not res.chat_text:
                continue                        # e.g. not_applicable / insufficient_context: nothing to post
            out.append({"request_id": r.id, "status": "done", "result_id": res.id, "chat_text": res.chat_text})
        else:
            out.append({"request_id": r.id, "status": "reused"})
    return out


def set_subjects(s: Session, request_id, subjects: list) -> None:
    """subjects: [{"subject": str, "decision": "new"|"reused", "reused_result_id": int|None}]"""
    s.execute(delete(RcaRequestSubject).where(RcaRequestSubject.request_id == request_id))
    seen = set()
    for sub in subjects:
        if sub["subject"] in seen:
            continue
        seen.add(sub["subject"])
        s.add(RcaRequestSubject(request_id=request_id, subject=sub["subject"], decision=sub["decision"],
                                reused_result_id=sub.get("reused_result_id")))


def subjects_for(s: Session, request_id) -> list:
    return list(s.scalars(select(RcaRequestSubject).where(RcaRequestSubject.request_id == request_id)))


def runs_started_today(s: Session) -> int:
    utc_midnight = func.timezone("UTC", func.date_trunc("day", func.timezone("UTC", func.now())))
    return s.scalar(select(func.count()).select_from(RcaRequest).where(RcaRequest.started_at >= utc_midnight))


def recover_stuck(s: Session, older_than_seconds, max_attempts) -> int:
    cutoff = func.now() - func.make_interval(0, 0, 0, 0, 0, 0, older_than_seconds)
    retry = RcaRequest.attempts < max_attempts
    res = s.execute(update(RcaRequest)
                    .where(RcaRequest.status == "running", RcaRequest.started_at < cutoff)
                    .values(status=case((retry, "queued"), else_="failed"),
                            skip_reason=case((retry, RcaRequest.skip_reason), else_="stuck in running; max attempts"),
                            updated_at=func.now()))
    return res.rowcount


# --- triage context -------------------------------------------------------------

def _days_before(ts, days):
    return ts - func.make_interval(0, 0, 0, days)


def signature_history(s: Session, signature_id, before, days, limit) -> list:
    """Earlier messages with the same signature (chronological), for triage."""
    rows = s.execute(
        select(Message.id.label("message_id"), Message.created_at, MessageParse.status, MessageParse.title,
               Message.rendered_text)
        .join(MessageParse, MessageParse.message_id == Message.id)
        .where(MessageParse.signature_id == signature_id, Message.created_at < before,
               Message.created_at > _days_before(before, days))
        .order_by(Message.created_at.desc()).limit(limit)).mappings().all()
    return [dict(r) for r in reversed(rows)]


def _active_comment_on(result_id_col):
    """An active comment is linked to this result."""
    return select(RcaCommentResult.result_id).join(RcaComment, RcaComment.id == RcaCommentResult.comment_id) \
        .where(RcaCommentResult.result_id == result_id_col, RcaComment.removed_at.is_(None)).exists()


def reuse_candidates(s: Session, signature_id, before, days, exclude_request_id=None, commented=False) -> list:
    """Successful RCA results for the same signature in the window, never ones rated 👎 (active feedback),
    never the request's own earlier results (a re-run must not just return its previous answer), never
    results of `rca --no-post` runs (nobody saw or rated them, and a reuse reply couldn't link to them), and
    never results people commented on (docs/design/comments.md: the next firing gets a fresh RCA instead).
    `commented=True` returns the commented results in the window instead, for the re-analysis lines of the RCA
    input: also ones rated 👎 or from `--no-post` runs, because their comments must still reach the next RCA."""
    fb = (select(RcaFeedback.result_id,
                 func.count().filter(RcaFeedback.verdict == "correct").label("correct"),
                 func.count().filter(RcaFeedback.verdict == "partial").label("partial"),
                 func.count().filter(RcaFeedback.verdict == "wrong").label("wrong"))
          .where(RcaFeedback.removed_at.is_(None)).group_by(RcaFeedback.result_id).subquery())
    has_comment = _active_comment_on(RcaResult.id)
    rows = s.execute(
        select(RcaResult.id.label("result_id"), RcaResult.created_at, RcaResult.report, RcaRequest.input_text,
               RcaRequest.id.label("request_id"),
               func.coalesce(fb.c.correct, 0).label("correct"), func.coalesce(fb.c.partial, 0).label("partial"))
        .join(RcaRequest, RcaRequest.id == RcaResult.request_id)
        .outerjoin(fb, fb.c.result_id == RcaResult.id)
        .where(RcaRequest.signature_id == signature_id, RcaResult.outcome == "rca",
               RcaResult.created_at < before, RcaResult.created_at > _days_before(before, days),
               RcaRequest.id != (exclude_request_id or -1),
               *([has_comment] if commented else
                 [~has_comment, func.coalesce(fb.c.wrong, 0) == 0, RcaRequest.requested_by.is_distinct_from(NO_POST)]))
        .order_by(RcaResult.created_at.desc())).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        d["subjects"] = list(s.scalars(select(RcaRequestSubject.subject).where(
            RcaRequestSubject.request_id == d["request_id"], RcaRequestSubject.decision == "new")))
        out.append(d)
    return out


# --- results / posts / feedback --------------------------------------------------

RESULT_FIELDS = ("outcome", "report", "chat_text", "error", "origin", "confidence", "self_healing", "repo",
                 "commit_sha", "model", "cost_usd", "duration_s", "num_turns", "session_id", "skill_version")


def insert_result(s: Session, request_id, r: dict) -> int:
    res = RcaResult(request_id=request_id, **{k: r.get(k) for k in RESULT_FIELDS})
    s.add(res)
    s.flush()
    return res.id


def latest_result(s: Session, request_id) -> Optional[RcaResult]:
    return s.scalars(select(RcaResult).where(RcaResult.request_id == request_id)
                     .order_by(RcaResult.id.desc()).limit(1)).first()


def results_for(s: Session, request_id) -> list:
    """Every result (skill run) of a request, newest first."""
    return list(s.scalars(select(RcaResult).where(RcaResult.request_id == request_id).order_by(RcaResult.id.desc())))


def posts_for(s: Session, request_id) -> list:
    return list(s.scalars(select(RcaPost).where(RcaPost.request_id == request_id).order_by(RcaPost.id)))


def insert_post(s: Session, request_id, result_id, kind, channel_id, thread_external_id, reply_external_id,
                reused_ids=(), destination="thread") -> int:
    post = RcaPost(request_id=request_id, result_id=result_id, kind=kind, channel_id=channel_id,
                   thread_external_id=thread_external_id, reply_external_id=reply_external_id,
                   destination=destination)
    s.add(post)
    s.flush()
    for rid in set(reused_ids):
        s.add(RcaPostReuseLink(post_id=post.id, reused_result_id=rid))
    return post.id


def reply_ids_for_results(s: Session, result_ids, destination="thread") -> dict:
    """result_id -> reply_external_id of the first post that showed it in `destination` (for links)."""
    out = {}
    if result_ids:
        for rid, reply in s.execute(select(RcaPost.result_id, RcaPost.reply_external_id)
                                    .where(RcaPost.result_id.in_(list(result_ids)),
                                           RcaPost.destination == destination)
                                    .order_by(RcaPost.posted_at)):
            out.setdefault(rid, reply)
    return out


NIMI_HEADER_LIKE = "%\n  \U0001F441\uFE0F *NIMI* %"     # NIMI's reply header line (as in the skill_messages view)


def bot_messages_for_citation(s: Session, message_ids) -> dict:
    """message id -> (external id, created_at, channel label), for the bot messages the skill can see in
    skill_messages: never NIMI's own posts (by their rca_posts row or NIMI header line, as in the view), so a
    verdict can't rest on an earlier, unverified RCA."""
    if not message_ids:
        return {}
    own_post = select(RcaPost.id).where(RcaPost.reply_external_id == Message.external_id).exists()
    rows = s.execute(select(Message.id, Message.external_id, Message.created_at, Channel.label)
                     .join(Channel, Channel.id == Message.channel_id)
                     .where(Message.id.in_(list(message_ids)), Message.sender_type == "bot", ~own_post,
                            Message.rendered_text.notlike(NIMI_HEADER_LIKE))).all()
    return {r[0]: (r[1], r[2], r[3]) for r in rows}


def earlier_rcas(s: Session, result_ids) -> dict:
    """result_id -> {"title", "tldr", "created_at"} of earlier RCAs, for reuse replies (title/tldr may be None)."""
    from .rca import title_of, tldr_of
    out = {}
    if result_ids:
        for rid, report, created in s.execute(select(RcaResult.id, RcaResult.report, RcaResult.created_at)
                                              .where(RcaResult.id.in_(list(result_ids)))):
            out[rid] = {"title": title_of(report), "tldr": tldr_of(report), "created_at": created}
    return out


def recent_posts(s: Session, channel_id, days) -> list:
    posts = s.scalars(select(RcaPost).where(RcaPost.channel_id == channel_id,
                                            RcaPost.posted_at > _days_before(func.now(), days))).all()
    out = []
    for p in posts:
        reused = list(s.scalars(select(RcaPostReuseLink.reused_result_id).where(RcaPostReuseLink.post_id == p.id)))
        out.append({"id": p.id, "result_id": p.result_id, "reply_external_id": p.reply_external_id,
                    "reused_result_ids": reused})
    return out


def recent_webhook_posts(s: Session, days) -> list:
    """Webhook posts of the last `days` days (like recent_posts, plus the source channel's provider)."""
    rows = s.execute(select(RcaPost, Channel.provider).join(Channel, Channel.id == RcaPost.channel_id)
                     .where(RcaPost.destination == "webhook", RcaPost.reply_external_id != "",
                            RcaPost.posted_at > _days_before(func.now(), days))).all()
    out = []
    for p, provider in rows:
        reused = list(s.scalars(select(RcaPostReuseLink.reused_result_id).where(RcaPostReuseLink.post_id == p.id)))
        out.append({"id": p.id, "result_id": p.result_id, "reply_external_id": p.reply_external_id,
                    "thread_external_id": p.thread_external_id, "reused_result_ids": reused, "provider": provider})
    return out


# --- comments -------------------------------------------------------------------------------

ATTACHABLE_OUTCOMES = ("rca", "not_applicable", "insufficient_context")


def stored_update_times(s: Session, provider, external_ids: list) -> dict:
    """external_id -> last_update_at of stored messages (top-level and thread replies), to spot edits."""
    out = {}
    if external_ids:
        for model in (Message, ThreadMessage):
            for ext, ts in s.execute(select(model.external_id, model.last_update_at)
                                     .where(model.provider == provider, model.external_id.in_(external_ids))):
                out[ext] = ts
    return out


POST_CLOCK_SKEW = timedelta(seconds=5)    # NIMI's clock (posted_at) vs the chat's (message created_at)


def nimi_post_before(s: Session, thread_external_id, at) -> Optional[dict]:
    """The latest NIMI post in a thread at or before `at` (any destination): its request and the results it
    showed (its own and the reused ones). None if NIMI never posted in that thread before then."""
    if not thread_external_id:
        return None
    post = s.scalars(select(RcaPost).where(RcaPost.thread_external_id == thread_external_id,
                                           RcaPost.reply_external_id != "",
                                           RcaPost.posted_at <= at + POST_CLOCK_SKEW)
                     .order_by(RcaPost.posted_at.desc(), RcaPost.id.desc()).limit(1)).first()
    if post is None:
        return None
    reused = s.scalars(select(RcaPostReuseLink.reused_result_id).where(RcaPostReuseLink.post_id == post.id))
    results = ([post.result_id] if post.result_id else []) + sorted(set(reused) - {post.result_id})
    return {"request_id": post.request_id, "result_ids": results}


def request_for_thread(s: Session, channel_id, thread_external_id) -> Optional[dict]:
    """The RCA request of the message that started a thread in a registered channel, with its latest usable
    result (None if it has none yet)."""
    if not channel_id or not thread_external_id:
        return None
    req_id = s.scalar(select(RcaRequest.id).join(Message, Message.id == RcaRequest.source_message_id)
                      .where(Message.channel_id == channel_id, Message.thread_external_id == thread_external_id)
                      .order_by(Message.created_at).limit(1))
    if req_id is None:
        return None
    result_id = latest_attachable_result(s, req_id)
    return {"request_id": req_id, "result_ids": [result_id] if result_id else []}


def latest_attachable_result(s: Session, request_id) -> Optional[int]:
    """The newest result of a request that a comment can be about (not an error or timeout)."""
    return s.scalar(select(RcaResult.id).where(RcaResult.request_id == request_id,
                                               RcaResult.outcome.in_(ATTACHABLE_OUTCOMES))
                    .order_by(RcaResult.id.desc()).limit(1))


def request_signature(s: Session, request_id) -> Optional[int]:
    return s.scalar(select(RcaRequest.signature_id).where(RcaRequest.id == request_id)) if request_id else None


def comment_for_message(s: Session, provider, message_external_id) -> Optional[RcaComment]:
    return s.scalars(select(RcaComment).where(RcaComment.provider == provider,
                                              RcaComment.message_external_id == message_external_id)).first()


def add_comment(s: Session, result_ids=(), **fields) -> int:
    comment = RcaComment(**fields)
    s.add(comment)
    s.flush()
    link_comment(s, comment.id, result_ids)
    return comment.id


def link_comment(s: Session, comment_id, result_ids) -> None:
    for rid in dict.fromkeys(result_ids):
        s.execute(pg_insert(RcaCommentResult).values(comment_id=comment_id, result_id=rid).on_conflict_do_nothing())


def update_comment(s: Session, comment_id, text_, flags) -> None:
    s.execute(update(RcaComment).where(RcaComment.id == comment_id)
              .values(text=text_, flags=flags, removed_at=None, updated_at=func.now()))


def remove_comment(s: Session, comment_id) -> None:
    s.execute(update(RcaComment).where(RcaComment.id == comment_id, RcaComment.removed_at.is_(None))
              .values(removed_at=func.now(), updated_at=func.now()))


def attach_request_comments(s: Session, request_id, result_id, given=()) -> int:
    """Comments that belong to a request but to none of its results yet now belong to its new result, except
    the ones the run was given as input (`given`): the RCA took those into account, so they mustn't stop its
    reuse. What's left was written while the run was in progress. Returns how many were linked."""
    has_result = select(RcaCommentResult.comment_id).where(RcaCommentResult.comment_id == RcaComment.id).exists()
    ids = [cid for cid in s.scalars(select(RcaComment.id).where(RcaComment.request_id == request_id,
                                                                RcaComment.removed_at.is_(None), ~has_result))
           if cid not in set(given)]
    for cid in ids:
        link_comment(s, cid, [result_id])
    return len(ids)


def comments_for_input(s: Session, request_id, result_ids=()) -> list:
    """Active comments for an RCA run, oldest first, as (comment, [result ids]): the request's own (written
    before the run, or on its earlier results) and those on `result_ids` (earlier RCAs being re-analyzed)."""
    linked = select(RcaCommentResult.comment_id).where(RcaCommentResult.result_id.in_(list(result_ids) or [-1]))
    rows = s.scalars(select(RcaComment).where(RcaComment.removed_at.is_(None),
                                              or_(RcaComment.request_id == request_id, RcaComment.id.in_(linked)))
                     .order_by(RcaComment.created_at, RcaComment.id))
    return [(c, comment_result_ids(s, c.id)) for c in rows]


def other_comment_count(s: Session, signature_id, exclude_ids=()) -> int:
    """Active, attached comments on RCAs of a signature, other than `exclude_ids` (for the input's pointer)."""
    if not signature_id:
        return 0
    return s.scalar(select(func.count()).select_from(RcaComment).where(
        RcaComment.signature_id == signature_id, RcaComment.removed_at.is_(None),
        RcaComment.request_id.isnot(None), RcaComment.id.notin_(list(exclude_ids) or [-1])))


def related_comments(s: Session, request_id, at, hours: int, terms=(), exclude_ids=(), limit: int = 10,
                     signature_id=None) -> list:
    """Active, attached comments of other requests that may concern this alert, newest first, as
    (comment, [result ids], signature key): on this alert's signature (`signature_id`) at any age, written
    within `hours` of `at` (the alert's time; 0 or no time = no window), or naming one of `terms` as a whole
    word at any time. `terms` are plain names (letters, digits, `_ . -`; see comments.subject_terms)."""
    conds = [RcaComment.text.op("~*")(r"\m" + re.sub(r"([.\-])", r"\\\1", t) + r"\M") for t in terms]
    if signature_id:
        conds.append(RcaComment.signature_id == signature_id)
    if hours > 0 and at is not None:
        conds.append(RcaComment.created_at.between(at - timedelta(hours=hours), at + timedelta(hours=hours)))
    if not conds:
        return []
    rows = s.execute(select(RcaComment, Signature.signature_key)
                     .outerjoin(Signature, Signature.id == RcaComment.signature_id)
                     .where(RcaComment.removed_at.is_(None), RcaComment.request_id.isnot(None),
                            RcaComment.request_id != request_id, RcaComment.id.notin_(list(exclude_ids) or [-1]),
                            or_(*conds))
                     .order_by(RcaComment.created_at.desc(), RcaComment.id.desc()).limit(limit)).all()
    return [(c, comment_result_ids(s, c.id), key) for c, key in rows]


def signature_key(s: Session, signature_id) -> Optional[str]:
    return s.scalar(select(Signature.signature_key).where(Signature.id == signature_id)) if signature_id else None


def record_verdicts(s: Session, result_id, rows: list) -> None:
    """What the run that produced `result_id` concluded about each comment (comments.check_verdicts). Every
    comment given in full has a row, which is also how linking tells them apart from later comments."""
    for r in rows:
        values = {k: r[k] for k in ("verdict", "relied_on", "evidence", "downgraded")}
        s.execute(pg_insert(RcaCommentVerdict).values(comment_id=r["comment_id"], result_id=result_id, **values)
                  .on_conflict_do_update(index_elements=["comment_id", "result_id"], set_=values))


def relies_on_unverified_comment(s: Session, result_id) -> bool:
    """The result's cause relies on a comment nobody could verify (as stored by record_verdicts)."""
    return bool(s.scalar(select(RcaCommentVerdict.comment_id).where(
        RcaCommentVerdict.result_id == result_id, RcaCommentVerdict.relied_on,
        RcaCommentVerdict.verdict == "unverifiable").limit(1)))


def verdicts_for_comment(s: Session, comment_id) -> list:
    return list(s.scalars(select(RcaCommentVerdict).where(RcaCommentVerdict.comment_id == comment_id)
                          .order_by(RcaCommentVerdict.result_id)))


def get_comment(s: Session, comment_id) -> Optional[RcaComment]:
    return s.get(RcaComment, comment_id)


def comment_messages_for_citation(s: Session, ids, request: dict) -> dict:
    """{comment id: chat message external id} for cited comments that can be linked: active, attached, and
    written in chat with this request's provider. A comment on another alert counts too: runs are given those
    (comments.input_context), and the cause may rest on one."""
    if not ids:
        return {}
    rows = s.execute(select(RcaComment.id, RcaComment.message_external_id).where(
        RcaComment.id.in_(list(ids)), RcaComment.removed_at.is_(None), RcaComment.source == "chat",
        RcaComment.request_id.isnot(None), RcaComment.provider == request.get("provider"),
        RcaComment.message_external_id.isnot(None)))
    return {r.id: r.message_external_id for r in rows}


def comment_result_ids(s: Session, comment_id) -> list:
    return sorted(s.scalars(select(RcaCommentResult.result_id).where(RcaCommentResult.comment_id == comment_id)))


def comments_for_request(s: Session, request_id) -> list:
    """Comments that belong to a request or to any of its results (removed ones too), oldest first."""
    on_results = select(RcaCommentResult.comment_id).join(RcaResult, RcaResult.id == RcaCommentResult.result_id) \
        .where(RcaResult.request_id == request_id)
    return list(s.scalars(select(RcaComment).where(or_(RcaComment.request_id == request_id,
                                                       RcaComment.id.in_(on_results)))
                          .order_by(RcaComment.created_at, RcaComment.id)))


def list_comments(s: Session, signature_id=None, request_id=None, unattached=False, include_removed=False,
                  limit=50) -> list:
    """Newest first."""
    q = select(RcaComment)
    if signature_id is not None:
        q = q.where(RcaComment.signature_id == signature_id)
    if request_id is not None:
        q = q.where(RcaComment.request_id == request_id)
    if unattached:
        q = q.where(RcaComment.request_id.is_(None))
    if not include_removed:
        q = q.where(RcaComment.removed_at.is_(None))
    return list(s.scalars(q.order_by(RcaComment.created_at.desc(), RcaComment.id.desc()).limit(limit)))


def attach_comment(s: Session, comment_id, request_id, signature_id, result_ids) -> None:
    s.execute(update(RcaComment).where(RcaComment.id == comment_id)
              .values(request_id=request_id, signature_id=signature_id, updated_at=func.now()))
    link_comment(s, comment_id, result_ids)


def set_comment_flags(s: Session, comment_id, flags) -> None:
    s.execute(update(RcaComment).where(RcaComment.id == comment_id).values(flags=flags or None))


def active_chat_comments(s: Session, provider, after, before, channel_id=None, threads=None) -> list:
    """(id, message_external_id) of active chat comments created in (after, before): in a registered channel,
    or (channel_id None) in the given threads. For finding comments whose message was deleted."""
    q = select(RcaComment.id, RcaComment.message_external_id).where(
        RcaComment.source == "chat", RcaComment.provider == provider, RcaComment.removed_at.is_(None),
        RcaComment.created_at > after, RcaComment.created_at < before)
    if channel_id is not None:
        q = q.where(RcaComment.channel_id == channel_id)
    elif threads:
        q = q.where(RcaComment.thread_external_id.in_(list(threads)))
    else:
        return []
    return [tuple(r) for r in s.execute(q)]


def has_reactions(s: Session, provider, message_external_id) -> bool:
    return s.scalar(select(func.count()).select_from(Reaction).where(
        Reaction.provider == provider, Reaction.message_external_id == message_external_id)) > 0


def sync_reactions(s: Session, provider, message_external_id, reactions: list) -> tuple:
    """Upsert current reactions (providers.Reaction objects); mark missing ones removed.
    Returns (added, removed) as lists of dicts with the internal reaction `id`."""
    added = []
    for r in reactions:
        stmt = pg_insert(Reaction).values(
            provider=provider, external_id=r.external_id, message_external_id=message_external_id,
            emoji_key=r.emoji_key, emoji_label=r.emoji_label, user_external_id=r.user_external_id,
            user_type=r.user_type)
        stmt = stmt.on_conflict_do_update(constraint="reactions_provider_external_id_key",
                                          set_={"removed_at": None}
                                          ).returning(Reaction.id, literal_column("(xmax = 0)").label("inserted"))
        row = s.execute(stmt).first()
        if row.inserted:
            added.append({"id": row.id, "emoji_key": r.emoji_key, "user_external_id": r.user_external_id})
    current = [r.external_id for r in reactions]
    cond = [Reaction.provider == provider, Reaction.message_external_id == message_external_id,
            Reaction.removed_at.is_(None)]
    if current:
        cond.append(Reaction.external_id.not_in(current))
    removed = s.execute(update(Reaction).where(and_(*cond)).values(removed_at=func.now())
                        .returning(Reaction.id)).scalars().all()
    return added, [{"id": rid} for rid in removed]


def add_feedback(s: Session, result_id, verdict, user_external_id, reaction_id, note=None) -> None:
    s.execute(pg_insert(RcaFeedback).values(result_id=result_id, verdict=verdict, source="reaction",
                                            user_external_id=user_external_id, reaction_id=reaction_id, note=note)
              .on_conflict_do_nothing(index_elements=[RcaFeedback.reaction_id]))


def remove_feedback_for_reactions(s: Session, reaction_ids: list) -> None:
    if reaction_ids:
        s.execute(update(RcaFeedback).where(RcaFeedback.reaction_id.in_(reaction_ids),
                                            RcaFeedback.removed_at.is_(None)).values(removed_at=func.now()))


# --- status ---------------------------------------------------------------------

def status_summary(s: Session) -> list:
    replies = (select(func.count()).select_from(ThreadMessage)
               .where(ThreadMessage.channel_id == Channel.id).scalar_subquery())
    return s.execute(
        select(Channel.label, Channel.provider, func.count(Message.id).label("messages"),
               func.max(Message.created_at).label("latest"), replies.label("replies"))
        .outerjoin(Message, Message.channel_id == Channel.id)
        .group_by(Channel.id).order_by(Channel.label)).mappings().all()


def request_summary(s: Session) -> list:
    return s.execute(select(RcaRequest.status, func.count().label("n"))
                     .group_by(RcaRequest.status).order_by(RcaRequest.status)).mappings().all()
