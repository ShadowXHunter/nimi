"""Service-wide pause of Claude calls (usage limit, credit, auth). Persisted in service_state so it survives
restarts. While paused: ingestion continues, triage/RCA wait (no attempts used), pending threads get a notice."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from .models import ServiceState

KEY = "claude_pause"


def get(s):
    """{"kind", "message", "since", "until"} with datetimes, or None."""
    row = s.get(ServiceState, KEY)
    if not row:
        return None
    v = dict(row.value)
    for k in ("since", "until"):
        v[k] = datetime.fromisoformat(v[k])
    return v


def is_active(pause) -> bool:
    return bool(pause) and datetime.now(timezone.utc) < pause["until"]


def set(s, kind, message, until) -> dict:
    existing = get(s)
    value = {"kind": kind, "message": (message or "")[:300],
             "since": (existing["since"] if existing else datetime.now(timezone.utc)).isoformat(),
             "until": until.isoformat()}
    stmt = pg_insert(ServiceState).values(key=KEY, value=value)
    s.execute(stmt.on_conflict_do_update(index_elements=[ServiceState.key], set_={"value": value}))
    return value


def clear(s) -> None:
    s.execute(delete(ServiceState).where(ServiceState.key == KEY))


def until_for(settings, reset_at) -> datetime:
    """Resume at Claude's reset time if known, else after the cool-down."""
    now = datetime.now(timezone.utc)
    return reset_at if reset_at and reset_at > now else now + timedelta(minutes=settings.limit_cooldown_minutes)
