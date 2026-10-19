"""Poll registered channels (any provider), store messages (pure ingested data), parse them, and open RCA
requests. Provider specifics live in nimi_service/providers/."""
import logging
from datetime import datetime, timedelta, timezone

from . import db, exclusions, redflags
from .models import Channel, Message as MessageRow, ThreadMessage
from .parse import PARSER_VERSION, parse_message
from .providers import Message, get_provider

log = logging.getLogger(__name__)


def message_row(msg: Message, channel: Channel, is_backfill: bool) -> dict:
    return {
        "provider": channel.provider,
        "external_id": msg.external_id,
        "channel_id": channel.id,
        "thread_external_id": msg.thread_external_id,
        "created_at": msg.created_at,
        "last_update_at": msg.last_update_at,
        "sender_external_id": msg.sender_external_id,
        "sender_type": msg.sender_type,
        "sender_display_name": msg.sender_display_name,
        "rendered_text": msg.rendered_text,
        "raw": msg.raw,
        "reaction_summary": msg.reaction_summary,
        "is_backfill": is_backfill,
    }


def store_parse(s, message_id: int, msg: Message) -> tuple:
    parsed = parse_message(msg)
    sig_id = db.get_or_create_signature(s, parsed["signature_key"], parsed["kind"], parsed["title"],
                                        parsed["service"], PARSER_VERSION, msg.created_at)
    db.upsert_parse(s, message_id, parsed, sig_id, PARSER_VERSION)
    return parsed, sig_id


def should_request_rca(settings, channel: Channel, row: dict, now: datetime):
    """None if no request should be opened; otherwise (status, skip_reason)."""
    if row["is_backfill"] or not channel.rca_enabled or row["sender_type"] != "bot":
        return None
    if now - row["created_at"] > timedelta(hours=settings.stale_hours):
        return "skipped", f"stale: alert older than {settings.stale_hours}h when NIMI first saw it"
    if settings.alert_senders and row["sender_external_id"] not in settings.alert_senders:
        return "skipped", f"sender {row['sender_external_id']} not in NIMI_ALERT_SENDERS"
    return "triage", None


def _store(s, settings, channel, msg: Message, is_backfill: bool, open_requests: bool, now, counts) -> None:
    row = message_row(msg, channel, is_backfill)
    if msg.is_thread_reply:
        change, _ = db.upsert_message(s, ThreadMessage, row)
        if change:
            counts["thread_new" if change == "inserted" else "updated"] += 1
        else:
            counts["unchanged"] += 1
        return
    change, message_id = db.upsert_message(s, MessageRow, row)
    if change != "inserted":
        counts["updated" if change == "updated" else "unchanged"] += 1
        return
    counts["main_new"] += 1
    parsed, sig_id = store_parse(s, message_id, msg)
    if open_requests:
        decision = should_request_rca(settings, channel, row, now)
        if decision and _is_own_post(s, settings, channel, msg):
            decision = None                     # NIMI's own webhook post in a registered space: never analyze it
        if decision and decision[0] == "triage":
            rule = exclusions.match(s, {"signature_key": parsed["signature_key"], "channel_id": channel.id,
                                        "sender_external_id": row["sender_external_id"], "title": parsed["title"],
                                        "status": parsed["status"], "text": row["rendered_text"]}, now)
            if rule:
                decision = ("skipped", exclusions.skip_reason(rule))
                counts["excluded"] += 1
        if decision:
            status, reason = decision
            rid = db.create_request(s, "message", row["rendered_text"][: settings.max_input_chars],
                                    signature_id=sig_id, source_message_id=message_id,
                                    status=status, skip_reason=reason)
            counts["requests"] += rid is not None


def _is_own_post(s, settings, channel, msg: Message) -> bool:
    from .rca import looks_like_nimi_post
    return db.is_nimi_post(s, msg.external_id) or \
        looks_like_nimi_post(get_provider(channel.provider, settings), msg.parse_text)


def ingest_channel(settings, provider, channel: Channel, backfill_days=None) -> dict:
    """One poll of a channel, in one transaction. The first poll (no watermark) is a backfill."""
    now = datetime.now(timezone.utc)
    is_backfill = channel.watermark is None
    since = (now - timedelta(days=backfill_days or channel.backfill_days)) if is_backfill \
        else channel.watermark - timedelta(seconds=settings.overlap_seconds)

    counts = {"main_new": 0, "thread_new": 0, "updated": 0, "unchanged": 0, "requests": 0, "excluded": 0}
    newest = None
    with db.session() as s:
        for msg in provider.iter_messages(channel.external_id, since):
            newest = max(newest, msg.created_at) if newest else msg.created_at
            _store(s, settings, channel, msg, is_backfill, open_requests=True, now=now, counts=counts)
        if newest:
            db.set_watermark(s, channel.id, newest)
    return counts


def backfill_channel(settings, provider, channel: Channel, days: int) -> dict:
    """Pull older history for an already-ingested channel without affecting live processing.

    Range: (now - days, watermark). Messages are stored with is_backfill = true and parsed; existing rows are
    left as they are; no RCA requests are created; the watermark is not moved. Messages newer than the
    watermark are left to the normal ingest so they can still trigger RCAs.
    For a channel that was never ingested, this is the same as its first ingest with `days` of history.
    """
    if channel.watermark is None:
        return ingest_channel(settings, provider, channel, backfill_days=days)
    now = datetime.now(timezone.utc)
    start, end = now - timedelta(days=days), channel.watermark
    counts = {"main_new": 0, "thread_new": 0, "updated": 0, "unchanged": 0, "requests": 0, "excluded": 0}
    if start >= end:
        return counts
    with db.session() as s:
        for msg in provider.iter_messages(channel.external_id, start, end):
            _store(s, settings, channel, msg, True, open_requests=False, now=now, counts=counts)
    return counts


def ingest_all(settings) -> None:
    with db.session() as s:
        channels = db.list_channels(s)
    for channel in channels:
        try:
            counts = ingest_channel(settings, get_provider(channel.provider, settings), channel)
            if any(v for k, v in counts.items() if k != "unchanged"):
                log.info("ingest %s (%s): %s", channel.label, channel.provider,
                         {k: v for k, v in counts.items() if k != "unchanged"})
        except BaseException as e:   # includes SystemExit from an unusable provider token
            if isinstance(e, KeyboardInterrupt):
                raise
            log.exception("ingest failed for %s (%s %s)", channel.label, channel.provider, channel.external_id)
            redflags.record(settings, "error", "high", "service",
                            f"Ingest failed for channel {channel.label} ({channel.provider})",
                            details={"error": f"{type(e).__name__}: {str(e)[:200]}"},
                            dedupe_key=f"ingest-failed:{channel.id}:{type(e).__name__}", channel_id=channel.id)


def reparse_all(settings) -> dict:
    """Re-parse every stored top-level message with the current parser, then point RCA requests at their
    message's new signature (reuse candidates are found through it). Returns counts and the exclusion rules
    whose signature no longer matches any message (they would silently stop matching)."""
    providers, n = {}, 0
    with db.session() as s:
        for row in db.all_main_messages(s):
            provider = providers.setdefault(row.provider, get_provider(row.provider, settings))
            store_parse(s, row.id, provider.normalize(row.raw))
            n += 1
        requests = db.resync_request_signatures(s)
        orphaned = db.exclusions_with_unknown_signature(s)
    return {"messages": n, "requests": requests, "orphaned_rules": orphaned}
