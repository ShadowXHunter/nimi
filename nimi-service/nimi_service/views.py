"""Database views. Alembic autogenerate doesn't track views, so migrations call
`create_views(op, VIEWS)` / `drop_views(op, VIEWS)` explicitly with frozen copies of the SQL.
`db upgrade` grants the skill's read-only role (NIMI_DB_SKILL_USER) SELECT on SKILL_VIEWS — only those, and
again after every upgrade, since re-creating a view drops its grants. Read-only access for development
reviews is granted separately by dev/review_access.sh.
To change a view: edit it here, then write a migration that calls drop_views(op, OLD) then
create_views(op, NEW) with copies of the SQL inside the migration (views are dropped and re-created,
so changing their columns is fine)."""

VIEWS = {
    # Occurrence stats per signature (computed, so they can't drift).
    "signature_stats": """
        CREATE VIEW signature_stats AS
        SELECT sig.id AS signature_id, sig.signature_key, sig.kind, sig.sample_title, sig.service,
               count(p.message_id) AS occurrences, min(m.created_at) AS first_seen_at,
               max(m.created_at) AS last_seen_at
        FROM signatures sig
        LEFT JOIN message_parses p ON p.signature_id = sig.id
        LEFT JOIN messages m ON m.id = p.message_id
        GROUP BY sig.id""",

    # Current (non-removed) feedback per result.
    "rca_feedback_summary": """
        CREATE VIEW rca_feedback_summary AS
        SELECT result_id,
               count(*) FILTER (WHERE verdict = 'correct') AS correct,
               count(*) FILTER (WHERE verdict = 'partial') AS partial,
               count(*) FILTER (WHERE verdict = 'wrong')   AS wrong
        FROM rca_feedback WHERE removed_at IS NULL
        GROUP BY result_id""",

    # Read by the RCA skill (via nimi-sql, as the skill reader role): top-level messages from bots, any channel and kind
    # (alerts, error logs, ...). No thread replies, no human messages, no raw payloads.
    # security_barrier: the bot filter is applied before any caller-supplied conditions.
    "skill_messages": """
        CREATE VIEW skill_messages WITH (security_barrier) AS
        SELECT m.id AS message_id, m.provider, m.external_id, c.label AS channel_label, m.thread_external_id,
               m.created_at, m.sender_external_id, m.sender_display_name,
               p.kind, p.title, p.status, p.service, sig.signature_key, m.rendered_text
        FROM messages m
        JOIN channels c ON c.id = m.channel_id
        LEFT JOIN message_parses p ON p.message_id = m.id
        LEFT JOIN signatures sig ON sig.id = p.signature_id
        WHERE m.sender_type = 'bot'""",

    # Read by the RCA skill: previous RCAs with their structured fields and feedback counts (no user names,
    # no reaction details, no raw input text). Source message details only for bot-sent messages.
    "skill_rcas": """
        CREATE VIEW skill_rcas WITH (security_barrier) AS
        SELECT res.id AS result_id, res.created_at, req.id AS request_id, req.source_type,
               m.provider, m.external_id AS source_external_id, c.label AS channel_label,
               m.created_at AS source_created_at, p.title AS source_title, p.service AS source_service,
               sig.signature_key, res.outcome, res.origin, res.confidence, res.self_healing, res.repo,
               res.commit_sha, req.triage_decision, req.triage_reason, res.report,
               ARRAY(SELECT s.subject FROM rca_request_subjects s
                     WHERE s.request_id = req.id AND s.decision = 'new' ORDER BY s.subject) AS subjects,
               COALESCE(fs.correct, 0) AS feedback_correct, COALESCE(fs.partial, 0) AS feedback_partial,
               COALESCE(fs.wrong, 0) AS feedback_wrong
        FROM rca_results res
        JOIN rca_requests req ON req.id = res.request_id
        LEFT JOIN messages m ON m.id = req.source_message_id AND m.sender_type = 'bot'
        LEFT JOIN channels c ON c.id = m.channel_id
        LEFT JOIN message_parses p ON p.message_id = m.id
        LEFT JOIN signatures sig ON sig.id = req.signature_id
        LEFT JOIN rca_feedback_summary fs ON fs.result_id = res.id
        WHERE res.outcome IN ('rca', 'not_applicable', 'insufficient_context')""",

    # Read during development reviews (role created by dev/review_access.sh): bot messages only, top-level
    # AND thread replies. No human messages, raw payloads or workflow data.
    "review_messages": """
        CREATE VIEW review_messages WITH (security_barrier) AS
        SELECT m.provider, m.external_id, c.label AS channel_label, false AS is_thread_reply,
               m.thread_external_id, m.created_at, m.sender_external_id, m.sender_display_name, m.rendered_text,
               p.kind, p.title, p.status, p.service, sig.signature_key
        FROM messages m
        JOIN channels c ON c.id = m.channel_id
        LEFT JOIN message_parses p ON p.message_id = m.id
        LEFT JOIN signatures sig ON sig.id = p.signature_id
        WHERE m.sender_type = 'bot'
        UNION ALL
        SELECT t.provider, t.external_id, c.label, true, t.thread_external_id, t.created_at,
               t.sender_external_id, t.sender_display_name, t.rendered_text,
               NULL, NULL, NULL, NULL, NULL
        FROM thread_messages t
        JOIN channels c ON c.id = t.channel_id
        WHERE t.sender_type = 'bot'""",
}

SKILL_VIEWS = ("skill_messages", "skill_rcas")


def grant_skill_access(conn, role: str) -> bool:
    """Grant `role` SELECT on SKILL_VIEWS (nothing else). False if the role doesn't exist (not created yet)."""
    import re
    from sqlalchemy import text
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
        raise ValueError(f"invalid role name {role!r}")
    if not conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role}).scalar():
        return False
    conn.execute(text(f'GRANT USAGE ON SCHEMA public TO "{role}"'))
    conn.execute(text(f'GRANT SELECT ON {", ".join(SKILL_VIEWS)} TO "{role}"'))
    return True


def create_views(op, views: dict = None) -> None:
    """Migrations pass their own frozen copy of `views`, so replaying old migrations on a fresh database
    never uses newer view definitions than the tables at that revision support."""
    for sql in (VIEWS if views is None else views).values():
        op.execute(sql)


def drop_views(op, views: dict = None) -> None:
    for name in reversed(list(VIEWS if views is None else views)):
        op.execute(f"DROP VIEW IF EXISTS {name}")
