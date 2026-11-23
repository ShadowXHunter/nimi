"""comments on RCAs; NIMI's own posts hidden from the skill

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-26

rca_comments: what people wrote to NIMI about an RCA (`!nimi` in chat, or the CLI); rca_comment_results links a
comment to the results it is about; rca_comment_verdicts: what later RCAs concluded about it.
skill_comments: the comments the skill may read (no authors). skill_messages no longer shows NIMI's own posts
(by rca_posts.reply_external_id or the NIMI header line). See docs/design/comments.md.
`db upgrade` re-grants the skill reader role; re-run dev/review_access.sh afterwards.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from nimi_service.views import create_views, drop_views

revision: str = '0004'
down_revision: Union[str, None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Frozen copies of the view SQL (see nimi_service/views.py).
OLD_VIEWS = {
    'skill_messages': """
        CREATE VIEW skill_messages WITH (security_barrier) AS
        SELECT m.id AS message_id, m.provider, m.external_id, c.label AS channel_label, m.thread_external_id,
               m.created_at, m.sender_external_id, m.sender_display_name,
               p.kind, p.title, p.status, p.service, sig.signature_key, m.rendered_text
        FROM messages m
        JOIN channels c ON c.id = m.channel_id
        LEFT JOIN message_parses p ON p.message_id = m.id
        LEFT JOIN signatures sig ON sig.id = p.signature_id
        WHERE m.sender_type = 'bot'""",
}
NEW_VIEWS = {
    'skill_messages': """
        CREATE VIEW skill_messages WITH (security_barrier) AS
        SELECT m.id AS message_id, m.provider, m.external_id, c.label AS channel_label, m.thread_external_id,
               m.created_at, m.sender_external_id, m.sender_display_name,
               p.kind, p.title, p.status, p.service, sig.signature_key, m.rendered_text
        FROM messages m
        JOIN channels c ON c.id = m.channel_id
        LEFT JOIN message_parses p ON p.message_id = m.id
        LEFT JOIN signatures sig ON sig.id = p.signature_id
        WHERE m.sender_type = 'bot'
          AND NOT EXISTS (SELECT 1 FROM rca_posts rp WHERE rp.reply_external_id = m.external_id)
          AND m.rendered_text NOT LIKE E'%\\n  👁️ *NIMI* %'""",
    'skill_comments': """
        CREATE VIEW skill_comments WITH (security_barrier) AS
        SELECT cm.id AS comment_id, cm.created_at, cm.request_id, sig.signature_key,
               ARRAY(SELECT cr.result_id FROM rca_comment_results cr WHERE cr.comment_id = cm.id
                     ORDER BY cr.result_id) AS result_ids,
               cm.text
        FROM rca_comments cm
        LEFT JOIN signatures sig ON sig.id = cm.signature_id
        WHERE cm.removed_at IS NULL AND cm.request_id IS NOT NULL""",
}


def upgrade() -> None:
    op.create_table('rca_comments',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('source', sa.Text(), nullable=False),
    sa.Column('provider', sa.Text(), nullable=True),
    sa.Column('message_external_id', sa.Text(), nullable=True),
    sa.Column('channel_id', sa.BigInteger(), nullable=True),
    sa.Column('thread_external_id', sa.Text(), nullable=True),
    sa.Column('request_id', sa.BigInteger(), nullable=True),
    sa.Column('signature_id', sa.BigInteger(), nullable=True),
    sa.Column('author_external_id', sa.Text(), nullable=True),
    sa.Column('author_display_name', sa.Text(), nullable=True),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('flags', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('removed_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("source IN ('chat', 'cli')", name='rca_comments_source_check'),
    sa.CheckConstraint("(source = 'chat') = (message_external_id IS NOT NULL AND provider IS NOT NULL)",
                       name='rca_comments_chat_message_check'),
    sa.CheckConstraint('length(trim(text)) > 0', name='rca_comments_text_check'),
    sa.ForeignKeyConstraint(['channel_id'], ['channels.id'], ),
    sa.ForeignKeyConstraint(['request_id'], ['rca_requests.id'], ),
    sa.ForeignKeyConstraint(['signature_id'], ['signatures.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('provider', 'message_external_id', name='rca_comments_provider_message_key')
    )
    op.create_index('rca_comments_signature_idx', 'rca_comments', ['signature_id', 'created_at'], unique=False)
    op.create_index('rca_comments_request_idx', 'rca_comments', ['request_id'], unique=False)
    op.create_index('rca_comments_thread_idx', 'rca_comments', ['thread_external_id', 'created_at'], unique=False)
    op.create_table('rca_comment_results',
    sa.Column('comment_id', sa.BigInteger(), nullable=False),
    sa.Column('result_id', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['comment_id'], ['rca_comments.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['result_id'], ['rca_results.id'], ),
    sa.PrimaryKeyConstraint('comment_id', 'result_id')
    )
    op.create_index(op.f('ix_rca_comment_results_result_id'), 'rca_comment_results', ['result_id'], unique=False)
    op.create_table('rca_comment_verdicts',
    sa.Column('comment_id', sa.BigInteger(), nullable=False),
    sa.Column('result_id', sa.BigInteger(), nullable=False),
    sa.Column('verdict', sa.Text(), nullable=False),
    sa.Column('evidence', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('relied_on', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('downgraded', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("verdict IN ('confirmed', 'contradicted', 'unverifiable', 'not_applicable')",
                       name='rca_comment_verdicts_verdict_check'),
    sa.ForeignKeyConstraint(['comment_id'], ['rca_comments.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['result_id'], ['rca_results.id'], ),
    sa.PrimaryKeyConstraint('comment_id', 'result_id')
    )
    op.create_index(op.f('ix_rca_comment_verdicts_result_id'), 'rca_comment_verdicts', ['result_id'], unique=False)
    drop_views(op, OLD_VIEWS)
    create_views(op, NEW_VIEWS)


def downgrade() -> None:
    drop_views(op, NEW_VIEWS)
    create_views(op, OLD_VIEWS)
    op.drop_index(op.f('ix_rca_comment_verdicts_result_id'), table_name='rca_comment_verdicts')
    op.drop_table('rca_comment_verdicts')
    op.drop_index(op.f('ix_rca_comment_results_result_id'), table_name='rca_comment_results')
    op.drop_table('rca_comment_results')
    op.drop_index('rca_comments_thread_idx', table_name='rca_comments')
    op.drop_index('rca_comments_request_idx', table_name='rca_comments')
    op.drop_index('rca_comments_signature_idx', table_name='rca_comments')
    op.drop_table('rca_comments')
