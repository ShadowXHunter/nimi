"""Exclusion rules: bot messages that never need an RCA (known harmless or informational alerts).

Checked in code before triage, so excluded messages cost no model call. A rule matches when every match
field it sets matches the message (unset fields match anything):
  signature   exact signature_key (the parser's coarse "kind of message"; `--like <message>` copies it)
  channel     the message's channel
  sender      exact provider sender ID, e.g. users/123
  title       case-insensitive substring of the parsed title (or the first line of the text if unparsed)
  status      case-insensitive parsed status, e.g. Alerting, INFO
  text        case-insensitive substring of the message's full text (e.g. a service name the title lacks)
Disabled and expired rules never match. Each match bumps the rule's hit_count / last_hit_at.
"""
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from .models import Channel, Message, MessageParse, RcaExclusion, Signature

PREFIX = "excluded by rule"


def _first_line(text_: str) -> str:
    return next((line.strip() for line in (text_ or "").splitlines() if line.strip()), "")


def matches(rule, f: dict) -> bool:
    """f: signature_key, channel_id, sender_external_id, title, status, text."""
    if rule.match_signature_key is not None and rule.match_signature_key != f.get("signature_key"):
        return False
    if rule.match_channel_id is not None and rule.match_channel_id != f.get("channel_id"):
        return False
    if rule.match_sender_external_id is not None and rule.match_sender_external_id != f.get("sender_external_id"):
        return False
    if rule.match_title is not None:
        title = f.get("title") or _first_line(f.get("text"))
        if rule.match_title.lower() not in title.lower():
            return False
    if rule.match_status is not None and rule.match_status.lower() != (f.get("status") or "").lower():
        return False
    if rule.match_text is not None and rule.match_text.lower() not in (f.get("text") or "").lower():
        return False
    return True


def active_rules(s, now: datetime) -> list:
    return list(s.scalars(select(RcaExclusion).where(
        RcaExclusion.enabled, (RcaExclusion.expires_at.is_(None)) | (RcaExclusion.expires_at > now))
        .order_by(RcaExclusion.id)))


def match(s, f: dict, now: datetime):
    """The first active rule matching `f` (its hit counters bumped), or None."""
    for rule in active_rules(s, now):
        if matches(rule, f):
            rule.hit_count += 1
            rule.last_hit_at = now
            return rule
    return None


def skip_reason(rule) -> str:
    return f"{PREFIX} #{rule.id}: {rule.reason}"[:500]


def message_fields(s, message_id: int) -> dict:
    row = s.execute(
        select(Message.channel_id, Message.sender_external_id, Message.rendered_text,
               MessageParse.title, MessageParse.status, Signature.signature_key)
        .outerjoin(MessageParse, MessageParse.message_id == Message.id)
        .outerjoin(Signature, Signature.id == MessageParse.signature_id)
        .where(Message.id == message_id)).one_or_none()
    if row is None:
        return {}
    return {"channel_id": row.channel_id, "sender_external_id": row.sender_external_id, "text": row.rendered_text,
            "title": row.title, "status": row.status, "signature_key": row.signature_key}


# --- managing rules (CLI) -----------------------------------------------------------------------------

def parse_expiry(value, now: datetime):
    """'7d' / '12h' / '30m' from now, or an ISO time (UTC if no offset); None/'' = never."""
    if not value:
        return None
    m = re.fullmatch(r"(\d+)\s*([dhm])", value.strip().lower())
    if m:
        n, unit = int(m.group(1)), m.group(2)
        unit_name = {"d": "days", "h": "hours", "m": "minutes"}[unit]
        return now + timedelta(**{unit_name: n})
    t = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def find_message(s, ref: str):
    """A top-level message by internal id or provider external id."""
    if ref.isdigit():
        return s.get(Message, int(ref))
    return s.scalars(select(Message).where(Message.external_id == ref)).first()


def preview(s, rule, limit: int = 5) -> tuple:
    """(count, recent examples) of stored top-level bot messages the rule would match."""
    rows = s.execute(
        select(Message.id, Message.created_at, Message.channel_id, Message.sender_external_id, Message.rendered_text,
               MessageParse.title, MessageParse.status, Signature.signature_key)
        .outerjoin(MessageParse, MessageParse.message_id == Message.id)
        .outerjoin(Signature, Signature.id == MessageParse.signature_id)
        .where(Message.sender_type == "bot")
        .where(*([Signature.signature_key == rule.match_signature_key] if rule.match_signature_key else []))
        .where(*([Message.channel_id == rule.match_channel_id] if rule.match_channel_id else []))
        .order_by(Message.created_at.desc())).all()
    hits = [r for r in rows if matches(rule, {
        "signature_key": r.signature_key, "channel_id": r.channel_id, "sender_external_id": r.sender_external_id,
        "title": r.title, "status": r.status, "text": r.rendered_text})]
    return len(hits), [(r.id, r.created_at, r.title or _first_line(r.rendered_text)) for r in hits[:limit]]


def channel_id_for(s, label: str) -> int:
    cid = s.scalar(select(Channel.id).where(Channel.label == label))
    if cid is None:
        raise SystemExit(f"no channel labelled {label!r} (see `nimi_service channels`)")
    return cid


def describe(rule, channel_labels: dict) -> str:
    parts = []
    if rule.match_signature_key:
        parts.append(f"signature={rule.match_signature_key}")
    if rule.match_channel_id:
        parts.append(f"channel={channel_labels.get(rule.match_channel_id, rule.match_channel_id)}")
    if rule.match_sender_external_id:
        parts.append(f"sender={rule.match_sender_external_id}")
    if rule.match_title:
        parts.append(f"title~{rule.match_title!r}")
    if rule.match_status:
        parts.append(f"status={rule.match_status}")
    if rule.match_text:
        parts.append(f"text~{rule.match_text!r}")
    return " ".join(parts)


def channel_labels(s) -> dict:
    return dict(s.execute(select(Channel.id, Channel.label)).all())
