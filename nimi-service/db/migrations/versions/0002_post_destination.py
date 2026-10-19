"""post destination: replies can also go to a separate space via an incoming webhook

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-19

rca_posts.destination: 'thread' (a reply in the source message's thread; every existing row) or 'webhook'
(a message in the space of NIMI_POST_WEBHOOK). No views depend on rca_posts.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0002'
down_revision: Union[str, None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('rca_posts', sa.Column('destination', sa.Text(), server_default=sa.text("'thread'"), nullable=False))
    op.create_check_constraint('rca_posts_destination_check', 'rca_posts', "destination IN ('thread', 'webhook')")


def downgrade() -> None:
    op.execute("DELETE FROM rca_post_reuse_links WHERE post_id IN (SELECT id FROM rca_posts WHERE destination = 'webhook')")
    op.execute("DELETE FROM rca_posts WHERE destination = 'webhook'")
    op.drop_constraint('rca_posts_destination_check', 'rca_posts', type_='check')
    op.drop_column('rca_posts', 'destination')
