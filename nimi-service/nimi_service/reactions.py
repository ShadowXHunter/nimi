"""Reaction pass: refresh reaction summaries for recent messages and turn reactions on RCA replies into feedback.

Reactions typically don't change a message's create/update time, so the incremental ingest doesn't see them.
Every `reaction_interval_minutes`, this re-lists the last `reaction_days` of each channel (providers return a
normalized reaction summary with each message), and fetches individual reactions only for RCA replies whose
summary changed.
"""
import logging
from datetime import datetime, timedelta, timezone

from . import db, redflags
from .providers import get_provider

log = logging.getLogger(__name__)

# 👍 correct, 🤔 partially right, 👎 wrong (providers normalize skin-tone variants away)
VERDICTS = {"\U0001F44D": "correct", "\U0001F914": "partial", "\U0001F44E": "wrong"}


def due(channel, minutes: int) -> bool:
    last = channel.reactions_checked_at
    return last is None or datetime.now(timezone.utc) - last >= timedelta(minutes=minutes)


def reaction_pass(settings, provider, channel) -> dict:
    counts = {"summaries_updated": 0, "replies_checked": 0, "feedback_added": 0, "feedback_removed": 0}
    since = datetime.now(timezone.utc) - timedelta(days=settings.reaction_days)
    fresh = {m.external_id: m.reaction_summary for m in provider.iter_messages(channel.external_id, since)}

    with db.session() as s:
        posts = {p["reply_external_id"]: p for p in db.recent_posts(s, channel.id, settings.reaction_days)}
        stored = db.stored_reaction_summaries(s, channel.provider, list(fresh))
        for external_id, summary in fresh.items():
            if external_id not in stored or (summary or {}) == stored[external_id]:
                continue                       # not ingested yet (normal ingest will) or unchanged
            db.update_reaction_summary(s, channel.provider, external_id, summary or {})
            counts["summaries_updated"] += 1
            post = posts.get(external_id)
            if post:
                counts["replies_checked"] += 1
                added, removed = _sync_post_reactions(provider, s, channel.provider, post)
                counts["feedback_added"] += added
                counts["feedback_removed"] += removed
        db.set_reactions_checked(s, channel.id)
    return counts


def _sync_post_reactions(provider, s, provider_name, post: dict) -> tuple:
    reactions = provider.list_reactions(post["reply_external_id"])
    added, removed = db.sync_reactions(s, provider_name, post["reply_external_id"], reactions)

    # Feedback goes to the RCA result the reply showed; for pure reuse replies, to the reused result.
    target = post["result_id"] or (post["reused_result_ids"][0] if post["reused_result_ids"] else None)
    n_added = 0
    for r in added:
        verdict = VERDICTS.get(r["emoji_key"])
        if target is None or not verdict or r.get("user_external_id") is None:
            continue
        note = None if post["result_id"] else f"via reuse reply {post['reply_external_id']}"
        db.add_feedback(s, target, verdict, r["user_external_id"], r["id"], note)
        n_added += 1
    db.remove_feedback_for_reactions(s, [r["id"] for r in removed])
    return n_added, len(removed)


_webhook_checked_at = None


def webhook_reaction_pass(settings) -> dict:
    """Feedback on posts in the NIMI_POST_WEBHOOK space. The space isn't a registered channel, so its messages
    are listed here directly (one listing per interval); reactions are fetched only for posts that have or had
    any. Needs the token's account to be able to read that space."""
    from .providers.gchat.client import webhook_space
    space = webhook_space(settings.post_webhook)
    counts = {"replies_checked": 0, "feedback_added": 0, "feedback_removed": 0}
    with db.session() as s:
        posts = db.recent_webhook_posts(s, settings.reaction_days)
    since = datetime.now(timezone.utc) - timedelta(days=settings.reaction_days)
    for provider_name in {p["provider"] for p in posts}:
        provider = get_provider(provider_name, settings)
        summaries = {m.external_id: m.reaction_summary for m in provider.iter_messages(space, since)}
        with db.session() as s:
            for post in (p for p in posts if p["provider"] == provider_name):
                if summaries.get(post["reply_external_id"]) or db.has_reactions(s, provider_name,
                                                                                post["reply_external_id"]):
                    counts["replies_checked"] += 1
                    added, removed = _sync_post_reactions(provider, s, provider_name, post)
                    counts["feedback_added"] += added
                    counts["feedback_removed"] += removed
    return counts


def reactions_all(settings) -> None:
    global _webhook_checked_at
    from .workflow import destinations
    now = datetime.now(timezone.utc)
    if (settings.post_enabled and settings.post_webhook and "webhook" in destinations(settings)
            and (_webhook_checked_at is None
                 or now - _webhook_checked_at >= timedelta(minutes=settings.reaction_interval_minutes))):
        _webhook_checked_at = now
        try:
            counts = webhook_reaction_pass(settings)
            if any(counts.values()):
                log.info("reactions (webhook space): %s", counts)
        except Exception as e:
            log.warning("reaction pass for the webhook space failed: %s", type(e).__name__)
            redflags.record(settings, "error", "low", "service",
                            "Can't read reactions in the NIMI_POST_WEBHOOK space (does the token's account have "
                            "access to it?); feedback on webhook posts is unavailable",
                            details={"error": f"{type(e).__name__}: {str(e)[:200]}"},
                            dedupe_key=f"webhook-reactions-failed:{type(e).__name__}")
    with db.session() as s:
        channels = db.list_channels(s)
    for channel in channels:
        if not due(channel, settings.reaction_interval_minutes):
            continue
        try:
            counts = reaction_pass(settings, get_provider(channel.provider, settings), channel)
            if any(counts.values()):
                log.info("reactions %s: %s", channel.label, counts)
        except Exception as e:
            log.exception("reaction pass failed for %s", channel.label)
            redflags.record(settings, "error", "medium", "service",
                            f"Reaction pass failed for channel {channel.label}",
                            details={"error": f"{type(e).__name__}: {str(e)[:200]}"},
                            dedupe_key=f"reactions-failed:{channel.id}:{type(e).__name__}", channel_id=channel.id)
