"""SQLAlchemy ORM models — the source of truth for the schema.

Change the schema by editing these models, then:
    python -m nimi_service db revision "describe the change"   # Alembic autogenerate; review the file!
    python -m nimi_service db upgrade
Views are not tracked by autogenerate; they live in nimi_service/views.py and are (re)created in migrations.

Provider-neutral: channels/messages can come from any messaging provider (gchat today; slack/teams later).
Rows have internal numeric IDs; the provider's own IDs are stored as (provider, external_id), unique together.
For Google Chat, external IDs are resource names, e.g. spaces/AAAAxxxxxxx/messages/yyyyyyyyyyy.yyyyyyyyyyy.
"""
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric,
                        PrimaryKeyConstraint, Text, UniqueConstraint, func, text)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

TS = DateTime(timezone=True)


class Base(DeclarativeBase):
    pass


def _now():
    return mapped_column(TS, nullable=False, server_default=func.now())


SENDER_TYPE_CHECK = "sender_type IS NULL OR sender_type IN ('human', 'bot')"


# ============================================================================
# 1. Ingested — what the messaging provider returned (+ a human-readable rendering)
# ============================================================================

class Channel(Base):
    """A registered channel / space / conversation on some provider. Add with `nimi_service add-channel`."""
    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    provider: Mapped[str] = mapped_column(Text, nullable=False)                  # gchat | slack | teams
    external_id: Mapped[str] = mapped_column(Text, nullable=False)               # e.g. spaces/AAAAxxxxxxx
    label: Mapped[str] = mapped_column(Text, nullable=False, unique=True)        # short name, e.g. alerts
    display_name: Mapped[Optional[str]] = mapped_column(Text)                   # provider's name (not unique)
    ingest_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    rca_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    backfill_days: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("90"))
    watermark: Mapped[Optional[datetime]] = mapped_column(TS)                   # newest message create time ingested
    reactions_checked_at: Mapped[Optional[datetime]] = mapped_column(TS)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    added_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()

    __table_args__ = (UniqueConstraint("provider", "external_id", name="channels_provider_external_id_key"),)


class _MessageColumns:
    """Columns shared by top-level messages and thread replies."""
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    external_id: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False)
    last_update_at: Mapped[Optional[datetime]] = mapped_column(TS)
    sender_external_id: Mapped[Optional[str]] = mapped_column(Text)
    sender_type: Mapped[Optional[str]] = mapped_column(Text)                     # human | bot | NULL
    sender_display_name: Mapped[Optional[str]] = mapped_column(Text)
    rendered_text: Mapped[str] = mapped_column(Text, nullable=False)            # human-readable rendering
    raw: Mapped[dict] = mapped_column(JSONB, nullable=False)                     # original payload as last fetched
    reaction_summary: Mapped[Optional[dict]] = mapped_column(JSONB)              # {emoji_key: count} as last seen
    is_backfill: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    ingested_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class Message(_MessageColumns, Base):
    """Top-level messages. Pure ingested data: no parsing or workflow state here."""
    __tablename__ = "messages"

    channel_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("channels.id"), nullable=False)
    thread_external_id: Mapped[Optional[str]] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("provider", "external_id", name="messages_provider_external_id_key"),
        CheckConstraint(SENDER_TYPE_CHECK, name="messages_sender_type_check"),
        Index("messages_channel_created_idx", "channel_id", "created_at"),
        Index("messages_sender_created_idx", "sender_external_id", "created_at"),
        Index("messages_thread_idx", "thread_external_id"),
        Index("messages_text_trgm_idx", "rendered_text", postgresql_using="gin",
              postgresql_ops={"rendered_text": "gin_trgm_ops"}),
    )


class ThreadMessage(_MessageColumns, Base):
    """Thread replies (any sender, including the service's own RCA replies). Never exposed to the skill."""
    __tablename__ = "thread_messages"

    channel_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("channels.id"), nullable=False)
    thread_external_id: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint("provider", "external_id", name="thread_messages_provider_external_id_key"),
        CheckConstraint(SENDER_TYPE_CHECK, name="thread_messages_sender_type_check"),
        Index("thread_messages_thread_idx", "thread_external_id", "created_at"),
    )


class Reaction(Base):
    """Individual reactions (fetched when a message's reaction_summary changes). The message may be in either
    message table, so it's referenced by (provider, message_external_id) rather than an FK."""
    __tablename__ = "reactions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    external_id: Mapped[str] = mapped_column(Text, nullable=False)
    message_external_id: Mapped[str] = mapped_column(Text, nullable=False)
    emoji_key: Mapped[str] = mapped_column(Text, nullable=False)                # unicode emoji or custom-emoji ID
    emoji_label: Mapped[Optional[str]] = mapped_column(Text)
    user_external_id: Mapped[Optional[str]] = mapped_column(Text)
    user_type: Mapped[Optional[str]] = mapped_column(Text)
    first_seen_at: Mapped[datetime] = _now()
    removed_at: Mapped[Optional[datetime]] = mapped_column(TS)

    __table_args__ = (
        UniqueConstraint("provider", "external_id", name="reactions_provider_external_id_key"),
        Index("reactions_message_idx", "provider", "message_external_id"),
    )


# ============================================================================
# 2. Derived — parser output, rebuildable with `nimi_service reparse`
# ============================================================================

class Signature(Base):
    """Coarse signature: the kind of alert/error (item names masked). Groups history for triage."""
    __tablename__ = "signatures"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    signature_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)                     # alert | log | other
    sample_title: Mapped[Optional[str]] = mapped_column(Text)
    service: Mapped[Optional[str]] = mapped_column(Text)
    parser_version: Mapped[str] = mapped_column(Text, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(TS, nullable=False)


class MessageParse(Base):
    """Parser output per top-level message."""
    __tablename__ = "message_parses"

    message_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("messages.id", ondelete="CASCADE"),
                                            primary_key=True)
    parser_version: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[Optional[str]] = mapped_column(Text)                         # Alerting | Ok | Triggered | ...
    service: Mapped[Optional[str]] = mapped_column(Text)
    signature_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("signatures.id"), index=True)
    parsed_at: Mapped[datetime] = _now()


# ============================================================================
# 3. Workflow — RCA requests, triage decisions, results, posts, feedback
# ============================================================================

REQUEST_STATUSES = ("triage", "held", "queued", "running", "done", "reused", "not_applicable", "skipped", "failed")
TRIAGE_DECISIONS = ("reuse", "new", "partial", "not_applicable")
OUTCOMES = ("rca", "not_applicable", "insufficient_context", "error", "timeout")


def _in(col: str, values) -> str:
    return f"{col} IN ({', '.join(repr(v) for v in values)})"


class RcaRequest(Base):
    """One per "please RCA this". Source can be a message or (later) any text.
    status: triage -> reused | not_applicable | queued -> running -> done | failed; skipped.
            held: not sent to any model (e.g. a critical secret was detected) until an admin releases it."""
    __tablename__ = "rca_requests"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)              # message | manual | api
    source_message_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("messages.id"))
    input_text: Mapped[str] = mapped_column(Text, nullable=False)              # exactly what the skill/triage got
    signature_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("signatures.id"))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'triage'"))
    skip_reason: Mapped[Optional[str]] = mapped_column(Text)
    requested_by: Mapped[Optional[str]] = mapped_column(Text)                   # service | manual | manual-no-post
    triage_decision: Mapped[Optional[str]] = mapped_column(Text)
    triage_reason: Mapped[Optional[str]] = mapped_column(Text)
    triage_output: Mapped[Optional[dict]] = mapped_column(JSONB)               # validated model output
    triage_model: Mapped[Optional[str]] = mapped_column(Text)
    triage_cost_usd: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    requested_at: Mapped[datetime] = _now()
    started_at: Mapped[Optional[datetime]] = mapped_column(TS)
    finished_at: Mapped[Optional[datetime]] = mapped_column(TS)
    updated_at: Mapped[datetime] = _now()

    __table_args__ = (
        CheckConstraint(_in("source_type", ("message", "manual", "api")), name="rca_requests_source_type_check"),
        CheckConstraint(_in("status", REQUEST_STATUSES), name="rca_requests_status_check"),
        CheckConstraint(_in("triage_decision", TRIAGE_DECISIONS), name="rca_requests_triage_decision_check"),
        Index("rca_requests_one_per_message", "source_message_id", unique=True,
              postgresql_where=text("source_type = 'message'")),
        Index("rca_requests_status_idx", "status", "requested_at"),
        Index("rca_requests_signature_idx", "signature_id", "requested_at"),
    )


class RcaResult(Base):
    """One per skill run."""
    __tablename__ = "rca_results"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    request_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("rca_requests.id"), nullable=False, index=True)
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    report: Mapped[Optional[str]] = mapped_column(Text)                         # full skill output
    chat_text: Mapped[Optional[str]] = mapped_column(Text)                      # reply formatted for the provider
    error: Mapped[Optional[str]] = mapped_column(Text)
    origin: Mapped[Optional[str]] = mapped_column(Text)                         # internal | upstream | undetermined
    confidence: Mapped[Optional[str]] = mapped_column(Text)                     # high | medium | low
    self_healing: Mapped[Optional[str]] = mapped_column(Text)                   # yes | no | unknown
    repo: Mapped[Optional[str]] = mapped_column(Text)
    commit_sha: Mapped[Optional[str]] = mapped_column(Text)
    model: Mapped[Optional[str]] = mapped_column(Text)
    cost_usd: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4))
    duration_s: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 1))
    num_turns: Mapped[Optional[int]] = mapped_column(Integer)
    session_id: Mapped[Optional[str]] = mapped_column(Text)
    skill_version: Mapped[Optional[str]] = mapped_column(Text)                 # hash of the skill files
    created_at: Mapped[datetime] = _now()

    __table_args__ = (CheckConstraint(_in("outcome", OUTCOMES), name="rca_results_outcome_check"),)


class RcaRequestSubject(Base):
    """Subjects per request, as decided by triage: analyzed here (new) or reused from an earlier result."""
    __tablename__ = "rca_request_subjects"

    request_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("rca_requests.id"))
    subject: Mapped[str] = mapped_column(Text, index=True)                     # table/service/... or *
    decision: Mapped[str] = mapped_column(Text, nullable=False)
    reused_result_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("rca_results.id"))

    __table_args__ = (
        PrimaryKeyConstraint("request_id", "subject"),
        CheckConstraint("decision IN ('new', 'reused')", name="rca_request_subjects_decision_check"),
        CheckConstraint("(decision = 'reused') = (reused_result_id IS NOT NULL)",
                        name="rca_request_subjects_reuse_check"),
    )


class RcaPost(Base):
    """Messages the service posted. kind 'notice' = a status message (e.g. RCA delayed by a usage limit).
    destination: 'thread' = a reply in the source message's thread; 'webhook' = a message in the space of
    NIMI_POST_WEBHOOK (thread_external_id / reply_external_id are then in that space)."""
    __tablename__ = "rca_posts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    request_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("rca_requests.id"), nullable=False)
    result_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("rca_results.id"))   # null for reuse
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    destination: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'thread'"))
    channel_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("channels.id"), nullable=False)   # source
    thread_external_id: Mapped[str] = mapped_column(Text, nullable=False)
    reply_external_id: Mapped[str] = mapped_column(Text, nullable=False)
    posted_at: Mapped[datetime] = _now()

    __table_args__ = (
        CheckConstraint("kind IN ('rca', 'reuse', 'mixed', 'not_applicable', 'notice')", name="rca_posts_kind_check"),
        CheckConstraint("destination IN ('thread', 'webhook')", name="rca_posts_destination_check"),
        UniqueConstraint("channel_id", "reply_external_id", name="rca_posts_channel_reply_key"),
    )


class RcaPostReuseLink(Base):
    """Which earlier results a reuse/mixed post pointed to."""
    __tablename__ = "rca_post_reuse_links"

    post_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("rca_posts.id"))
    reused_result_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("rca_results.id"))

    __table_args__ = (PrimaryKeyConstraint("post_id", "reused_result_id"),)


class RcaFeedback(Base):
    """Feedback on results. Reactions on an RCA reply: 👍 correct, 🤔 partial, 👎 wrong."""
    __tablename__ = "rca_feedback"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    result_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("rca_results.id"), nullable=False, index=True)
    verdict: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    user_external_id: Mapped[Optional[str]] = mapped_column(Text)
    reaction_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("reactions.id"), unique=True)
    note: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()
    removed_at: Mapped[Optional[datetime]] = mapped_column(TS)

    __table_args__ = (
        CheckConstraint("verdict IN ('correct', 'partial', 'wrong')", name="rca_feedback_verdict_check"),
        CheckConstraint("source IN ('reaction', 'manual')", name="rca_feedback_source_check"),
    )


# ============================================================================
# 3b. Exclusion rules — bot messages that never need an RCA (known harmless / informational alerts)
# ============================================================================

class RcaExclusion(Base):
    """A rule that skips RCA (and triage) for matching messages. Every set match_* field must match;
    unset fields match anything. Matching messages are still stored (and visible in the skill's history);
    their request is 'skipped' with "excluded by rule #id: reason". Manage with `nimi_service exclude`."""
    __tablename__ = "rca_exclusions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    created_at: Mapped[datetime] = _now()
    created_by: Mapped[Optional[str]] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    expires_at: Mapped[Optional[datetime]] = mapped_column(TS)                # temporary mute; NULL = permanent
    match_signature_key: Mapped[Optional[str]] = mapped_column(Text)          # exact
    match_channel_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("channels.id"))
    match_sender_external_id: Mapped[Optional[str]] = mapped_column(Text)     # exact, e.g. users/123
    match_title: Mapped[Optional[str]] = mapped_column(Text)                  # case-insensitive substring
    match_status: Mapped[Optional[str]] = mapped_column(Text)                 # case-insensitive, parsed status
    match_text: Mapped[Optional[str]] = mapped_column(Text)                   # case-insensitive substring of the text
    hit_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    last_hit_at: Mapped[Optional[datetime]] = mapped_column(TS)

    __table_args__ = (
        CheckConstraint("num_nonnulls(match_signature_key, match_channel_id, match_sender_external_id, "
                        "match_title, match_status, match_text) > 0", name="rca_exclusions_has_match"),
        CheckConstraint("length(trim(reason)) > 0", name="rca_exclusions_reason_check"),
    )


# ============================================================================
# 4. Red flags — sensitive data, rule violations, and errors for the admin to act on
# ============================================================================

RED_FLAG_KINDS = ("sensitive_data", "rule_violation", "error")
RED_FLAG_SEVERITIES = ("critical", "high", "medium", "low")
RED_FLAG_SOURCES = ("scanner", "triage", "skill", "guard", "service")
RED_FLAG_STATUSES = ("open", "acknowledged", "resolved", "ignored")


class RedFlag(Base):
    """Something an admin should look at. Never stores sensitive values: only type, location, and links."""
    __tablename__ = "red_flags"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    created_at: Mapped[datetime] = _now()
    last_seen_at: Mapped[datetime] = _now()
    occurrences: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    message_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("messages.id"))
    request_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("rca_requests.id"))
    channel_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("channels.id"))
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[Optional[dict]] = mapped_column(JSONB)                     # types, locations, links — no values
    dedupe_key: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'open'"))
    notified_at: Mapped[Optional[datetime]] = mapped_column(TS)
    notify_error: Mapped[Optional[str]] = mapped_column(Text)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(TS)
    resolved_by: Mapped[Optional[str]] = mapped_column(Text)
    note: Mapped[Optional[str]] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(_in("kind", RED_FLAG_KINDS), name="red_flags_kind_check"),
        CheckConstraint(_in("severity", RED_FLAG_SEVERITIES), name="red_flags_severity_check"),
        CheckConstraint(_in("source", RED_FLAG_SOURCES), name="red_flags_source_check"),
        CheckConstraint(_in("status", RED_FLAG_STATUSES), name="red_flags_status_check"),
        # one open flag per dedupe key: repeats bump occurrences instead of notifying again
        Index("red_flags_open_dedupe", "dedupe_key", unique=True,
              postgresql_where=text("status IN ('open', 'acknowledged') AND dedupe_key IS NOT NULL")),
        Index("red_flags_status_created_idx", "status", "created_at"),
    )


# ============================================================================
# 5. Service state — small persistent key/value state (e.g. "Claude calls paused until ...")
# ============================================================================

class ServiceState(Base):
    __tablename__ = "service_state"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = _now()
