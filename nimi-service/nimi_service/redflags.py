"""Red flags: record sensitive-data findings, rule violations, and errors, and ping the admin's Chat space.

  record(...)  -> stores a red flag (deduplicated) and notifies via the admin webhook (NIMI_ADMIN_WEBHOOK).
Never pass sensitive values here: summaries/details describe type, location, and links only.
A failed notification never breaks the caller; it is kept in red_flags.notify_error and retried later.
"""
import json
import logging
import threading
import time
import urllib.request
from collections import deque

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from . import db
from .models import Message, RedFlag

log = logging.getLogger(__name__)

SEVERITY_ICON = {"critical": "🚨", "high": "🔴", "medium": "🟠", "low": "🟡"}
KIND_LABEL = {"sensitive_data": "Sensitive data", "rule_violation": "Rule violation", "error": "Error"}

# Notification throttle (Google: incoming webhooks share the 1 write/s per-space limit).
_MIN_INTERVAL_SEC = 2.0
_MAX_LOW_PER_HOUR = 10
_lock = threading.Lock()
_last_sent = [0.0]
_low_sent = deque()


def record(settings, kind, severity, source, summary, details=None, dedupe_key=None, message_id=None,
           request_id=None, channel_id=None, notify=True) -> int:
    """Store (or bump) a red flag and notify. Returns the red flag id. Safe to call from any thread."""
    try:
        with db.session() as s:
            stmt = pg_insert(RedFlag).values(
                kind=kind, severity=severity, source=source, summary=summary[:1000], details=details,
                dedupe_key=dedupe_key, message_id=message_id, request_id=request_id, channel_id=channel_id)
            if dedupe_key:
                stmt = stmt.on_conflict_do_update(
                    index_elements=[RedFlag.dedupe_key],
                    index_where=RedFlag.status.in_(("open", "acknowledged")) & RedFlag.dedupe_key.isnot(None),
                    set_={"occurrences": RedFlag.occurrences + 1, "last_seen_at": func.now()})
            row = s.execute(stmt.returning(RedFlag.id, RedFlag.occurrences, RedFlag.notified_at)).first()
            flag_id, occurrences, notified_at = row
            link = _message_link(settings, s, message_id) if message_id else None
    except Exception:
        log.exception("could not record red flag: %s %s %s", kind, severity, summary[:200])
        return None
    log.warning("red flag #%s [%s/%s from %s] %s", flag_id, kind, severity, source, summary[:200])
    if notify and occurrences == 1 and notified_at is None:
        _notify(settings, flag_id, kind, severity, source, summary, details, link)
    return flag_id


def _message_link(settings, s, message_id):
    from .providers import get_provider
    row = s.execute(select(Message.provider, Message.external_id).where(Message.id == message_id)).first()
    if not row:
        return None
    try:
        return get_provider(row.provider, settings).message_link(row.external_id)
    except Exception:
        return row.external_id


def format_notification(flag_id, kind, severity, source, summary, details, link) -> str:
    lines = [f"{SEVERITY_ICON.get(severity, '•')} *NIMI red flag #{flag_id}* — {KIND_LABEL.get(kind, kind)} "
             f"({severity}, from {source})", summary]
    if details:
        safe = {k: v for k, v in details.items() if k in ("types", "findings", "where", "tool", "reason",
                                                         "request_id", "error", "path", "count")}
        if safe:
            lines.append("```" + json.dumps(safe, ensure_ascii=False, default=str)[:1500] + "```")
    if link:
        lines.append(f"Message: {link}")
    lines.append(f"Review: `python -m nimi_service red-flags` · resolve: `red-flags resolve {flag_id}`")
    return "\n".join(lines)


def _throttle(severity) -> bool:
    """True if this notification may be sent now (low severity is capped per hour)."""
    with _lock:
        now = time.monotonic()
        if severity == "low":
            while _low_sent and now - _low_sent[0] > 3600:
                _low_sent.popleft()
            if len(_low_sent) >= _MAX_LOW_PER_HOUR:
                return False
            _low_sent.append(now)
        wait = _MIN_INTERVAL_SEC - (now - _last_sent[0])
        if wait > 0:
            time.sleep(wait)
        _last_sent[0] = time.monotonic()
        return True


def _notify(settings, flag_id, kind, severity, source, summary, details, link) -> None:
    error = None
    if not settings.admin_webhook:
        error = "NIMI_ADMIN_WEBHOOK not set"
    elif not _throttle(severity):
        error = "throttled (low-severity hourly cap)"
    else:
        try:
            body = json.dumps({"text": format_notification(flag_id, kind, severity, source, summary, details,
                                                           link)}).encode()
            req = urllib.request.Request(settings.admin_webhook, data=body, method="POST",
                                         headers={"Content-Type": "application/json; charset=UTF-8"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp.read()
        except Exception as e:  # never break the caller; keep the error (without the webhook URL) for retry
            error = f"{type(e).__name__}: {str(e)[:200]}".replace(settings.admin_webhook, "<webhook>")
    try:
        with db.session() as s:
            s.execute(update(RedFlag).where(RedFlag.id == flag_id).values(
                notified_at=None if error else func.now(), notify_error=error))
    except Exception:
        log.exception("could not update notify status for red flag %s", flag_id)
    if error:
        log.warning("red flag #%s not notified: %s", flag_id, error)


def retry_unnotified(settings, limit=20) -> None:
    """Re-send notifications that failed earlier (e.g. webhook down)."""
    if not settings.admin_webhook:
        return
    with db.session() as s:
        rows = s.execute(select(RedFlag).where(RedFlag.notified_at.is_(None), RedFlag.notify_error.isnot(None),
                                               RedFlag.notify_error != "NIMI_ADMIN_WEBHOOK not set",
                                               RedFlag.status.in_(("open", "acknowledged")))
                         .order_by(RedFlag.created_at).limit(limit)).scalars().all()
        items = [(f.id, f.kind, f.severity, f.source, f.summary, f.details,
                  _message_link(settings, s, f.message_id) if f.message_id else None) for f in rows]
    for item in items:
        _notify(settings, *item)


def list_flags(s, status=("open", "acknowledged"), limit=50) -> list:
    return list(s.scalars(select(RedFlag).where(RedFlag.status.in_(status))
                          .order_by(RedFlag.created_at.desc()).limit(limit)))


def set_status(s, flag_id, status, by=None, note=None) -> bool:
    values = {"status": status, "note": note}
    if status in ("resolved", "ignored"):
        values.update(resolved_at=func.now(), resolved_by=by)
    return s.execute(update(RedFlag).where(RedFlag.id == flag_id).values(**values)).rowcount == 1


def notify_text(settings, text: str) -> None:
    """A plain notification to the admin space (no red flag), e.g. "RCAs resumed"."""
    if not settings.admin_webhook:
        return
    try:
        req = urllib.request.Request(settings.admin_webhook, data=json.dumps({"text": text}).encode(), method="POST",
                                     headers={"Content-Type": "application/json; charset=UTF-8"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
    except Exception as e:
        log.warning("admin notification failed: %s", type(e).__name__)


def resolve_by_prefix(s, dedupe_prefix: str, note: str) -> int:
    return s.execute(update(RedFlag).where(RedFlag.dedupe_key.like(dedupe_prefix + "%"),
                                           RedFlag.status.in_(("open", "acknowledged")))
                     .values(status="resolved", resolved_at=func.now(), resolved_by="nimi", note=note)).rowcount
